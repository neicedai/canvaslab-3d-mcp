"""Bounded orthographic camera suggestion from trusted world AABBs.

This fits box annotations, NOT silhouettes, and never changes geometry or the
saved plan. A new build/capture and model review are required after application.
"""
import copy
import math
from .scene_schema import Camera


def dot(a,b): return sum(x*y for x,y in zip(a,b))
def sub(a,b): return [x-y for x,y in zip(a,b)]
def basis(camera):
    d = sub(camera["position"], camera["target"])
    length = math.sqrt(dot(d,d)); back = [x/length for x in d]
    horizontal = math.hypot(back[0],back[2])
    right = [back[2]/horizontal, 0, -back[0]/horizontal]
    up = [-back[1]*back[0]/horizontal, horizontal, -back[1]*back[2]/horizontal]
    return right, up


def project_box(camera, world_box, size, *, framing_aspect=1.45):
    right, up = basis(camera)
    width,height = size
    span = camera["vertical_span"]*max(1, framing_aspect/(width/height))
    projected = []
    for x in [world_box[0][0],world_box[1][0]]:
        for y in [world_box[0][1],world_box[1][1]]:
            for z in [world_box[0][2],world_box[1][2]]:
                p = sub([x,y,z],camera["target"])
                projected.append([width/2+dot(p,right)*height/span, height/2-dot(p,up)*height/span])
    xs,ys = zip(*projected)
    return [min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)]


def suggest_camera(plan, analysis, observation):
    camera = plan["camera"]
    if camera["projection"] != "orthographic":
        raise ValueError("Camera fitting currently supports orthographic scenes only; perspective needs separate calibration")
    sx,sy,sw,sh = analysis["scene_box"]
    size = observation["native_size"]
    framing_aspect = sw/sh if plan.get("appearance_mode") == "reference" else 1.45
    targets = {r["id"]: r for r in analysis["regions"]}
    captured = observation["reference"]["objects"]
    pairs = []
    for node in plan["objects"]:
        if len(node["region_ids"]) != 1 or node["id"] not in captured: continue
        r = targets[node["region_ids"][0]]
        # Parent AABBs include children: do not count both as independent evidence.
        if any(n.get("parent_id") == node["id"] for n in plan["objects"]): continue
        if r["confidence"] < .6: continue
        world = captured[node["id"]].get("world_box")
        if not world: continue
        x,y,w,h = r["box"]
        target = [(x-sx)*size[0]/sw,(y-sy)*size[1]/sh,w*size[0]/sw,h*size[1]/sh]
        pairs.append((node["id"],world,target,r["confidence"]))
    if len(pairs) < 3:
        raise ValueError("Camera fitting needs at least three independently annotated objects with confidence >= 0.6")
    centers = [(t[0]+t[2]/2,t[1]+t[3]/2) for _,_,t,_ in pairs]
    if (max(p[0] for p in centers)-min(p[0] for p in centers) < size[0]*.1 or
        max(p[1] for p in centers)-min(p[1] for p in centers) < size[1]*.1):
        raise ValueError("Reference objects must be spatially distributed; camera fit is underconstrained")
    # Verify Python projection against the actual Three.js capture before fitting.
    for identity,world,_,_ in pairs:
        observed = captured[identity].get("screen_box")
        predicted = project_box(camera,world,size,framing_aspect=framing_aspect)
        if observed is None or max(abs(a-b) for a,b in zip(observed,predicted)) > 1.5:
            raise ValueError("Captured camera geometry does not match this fitter; recapture the reference view")
    direction = sub(camera["position"], camera["target"])
    distance = math.sqrt(dot(direction,direction))
    yaw = math.atan2(direction[0],direction[2]); elevation = math.asin(direction[1]/distance)
    right,up = basis(camera); span = camera["vertical_span"]
    values = [yaw,elevation,span,0.,0.]
    limits = [(yaw-math.pi/6,yaw+math.pi/6), (math.radians(10),math.radians(80)),
              (max(1,span*.7),min(150,span*1.4)),(-span*.25,span*.25),(-span*.25,span*.25)]
    def make(v):
        y,e,s,tx,ty = v
        target = [camera["target"][i]+right[i]*tx+up[i]*ty for i in range(3)]
        delta = [distance*math.cos(e)*math.sin(y),distance*math.sin(e),distance*math.cos(e)*math.cos(y)]
        return dict(camera,position=[a+b for a,b in zip(target,delta)],target=target,vertical_span=s)
    diagonal = math.hypot(*size)
    def score(c):
        losses = []
        for _,world,target,weight in pairs:
            a,b,w,h = project_box(c,world,size,framing_aspect=framing_aspect); x,y,tw,th = target
            residuals = [(a+w/2-x-tw/2)/diagonal,(b+h/2-y-th/2)/diagonal,
                         .1*math.log(max(w,.001)/tw),.1*math.log(max(h,.001)/th)]
            losses.append(weight*sum(r*r if abs(r)<=.1 else .2*abs(r)-.01 for r in residuals))
        return sum(losses)/sum(p[3] for p in pairs)
    initial = score(camera); best = initial
    steps = [math.radians(8),math.radians(6),span*.08,span*.04,span*.04]
    evaluations = 0
    for _ in range(12):
        for _ in range(12):
            improved = False
            for i in range(5):
                for sign in [-1,1]:
                    candidate = values[:]
                    candidate[i] = min(limits[i][1],max(limits[i][0],values[i]+sign*steps[i]))
                    c = make(candidate)
                    try: Camera.model_validate(c)
                    except ValueError: continue
                    loss = score(c); evaluations += 1
                    if loss < best-1e-12:
                        values,best,improved = candidate,loss,True
            if not improved: break
        steps = [s*.5 for s in steps]
    proposed = make(values) if best < initial-1e-9 else copy.deepcopy(camera)
    revised = copy.deepcopy(plan); revised["camera"] = proposed
    objects = []
    for identity,world,target,_ in pairs:
        def errors(c):
            x,y,w,h = project_box(c,world,size,framing_aspect=framing_aspect); a,b,tw,th = target
            return {"center_error_ratio":math.hypot(x+w/2-a-tw/2,y+h/2-b-th/2)/diagonal,
                    "width_error_ratio":abs(w-tw)/tw,"height_error_ratio":abs(h-th)/th}
        before,after = errors(camera),errors(proposed)
        objects.append({"object_id":identity,"before":before,"predicted_after":after,
                        "regressed":any(after[k]>before[k]+.01 for k in before)})
    return {"proposed_plan": revised, "before_loss": initial, "predicted_after_loss": score(proposed),
            "improved": best < initial-1e-9, "evaluations": evaluations,
            "fitted_object_ids": [p[0] for p in pairs], "applied": False,
            "object_diagnostics":objects,"requires_model_review":True,
            "regressed_object_ids":[o["object_id"] for o in objects if o["regressed"]],
            "measurement": "robust projected world AABB error; NOT image similarity or silhouette",
            "next_action": "Review proposal, validate_scene_plan, build and capture again; geometry and appearance still need review"}
