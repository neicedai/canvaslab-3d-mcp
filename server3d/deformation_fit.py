"""Advisory local component deformation from source pixel correspondences.

This module never edits an asset. A caller identifies a point on the current
normalized component and measures where that same visible feature belongs in the
ORIGINAL image. The solver converts the 2D residual into the minimum-norm bounded
local 3D offset under the current orthographic camera/object transform.
"""
from __future__ import annotations

import copy
import math
from typing import Literal

import numpy as np
from pydantic import Field

from .component_schema import ComponentRecipe
from .scene_schema import Id, Point2, Strict


class DeformationLandmark(Strict):
    id: Id
    anchor: list[float] = Field(min_length=3, max_length=3)
    pixel: Point2
    measurement: Literal["measured", "estimated"] = "measured"
    radius: float = Field(default=.25, ge=.05, le=.9)
    evidence: str = Field(min_length=1, max_length=1000)


def _quat_rotate(point, q):
    x,y,z = map(float, point); qx,qy,qz,qw = map(float,q)
    # v' = v + 2*cross(q.xyz, cross(q.xyz,v)+qw*v)
    uv = np.cross([qx,qy,qz],[x,y,z])
    uuv = np.cross([qx,qy,qz],uv)
    return np.asarray([x,y,z]) + 2*(qw*uv+uuv)


def _camera_basis(camera):
    position=np.asarray(camera["position"],dtype=float);target=np.asarray(camera["target"],dtype=float)
    back=position-target;back/=np.linalg.norm(back)
    horizontal=math.hypot(back[0],back[2])
    if horizontal < 1e-9:
        raise ValueError("orthographic deformation fitting requires a nonvertical camera")
    right=np.asarray([back[2]/horizontal,0,-back[0]/horizontal])
    up=np.asarray([-back[1]*back[0]/horizontal,horizontal,-back[1]*back[2]/horizontal])
    return right,up


def project_local_point(plan, obj, local_point, source_size, *, framing_aspect):
    camera=plan["camera"]
    if camera["projection"] != "orthographic":
        raise ValueError("component deformation fitting currently supports orthographic cameras only")
    width,height=map(float,source_size)
    dimensions=np.asarray(obj["dimensions"],dtype=float)
    local=np.asarray(local_point,dtype=float)*dimensions
    world=_quat_rotate(local,obj["rotation"])+np.asarray(obj["position"],dtype=float)
    right,up=_camera_basis(camera)
    relative=world-np.asarray(camera["target"],dtype=float)
    span=camera["vertical_span"]*max(1.,framing_aspect/(width/height))
    scale=height/span
    return np.asarray([width/2+np.dot(relative,right)*scale,
                       height/2-np.dot(relative,up)*scale])


def suggest_deformation_handles(plan, analysis, obj, recipe, landmarks):
    if obj.get("kind") != "asset" or obj.get("parent_id") is not None:
        raise ValueError("deformation fitting currently supports root registered assets only")
    if plan["camera"]["projection"] != "orthographic":
        raise ValueError("deformation fitting currently supports orthographic cameras only")
    if not isinstance(landmarks,list) or not 1 <= len(landmarks) <= 16:
        raise ValueError("Provide 1..16 source/component deformation landmarks")
    points=[DeformationLandmark.model_validate(item) for item in landmarks]
    if len({p.id for p in points}) != len(points):
        raise ValueError("deformation landmark IDs must be unique")
    sx,sy,sw,sh=analysis["scene_box"]
    aspect=sw/sh if plan.get("appearance_mode") == "reference" else 1.45
    base=ComponentRecipe.model_validate(recipe).model_dump()
    if base.get("deformation_handles"):
        raise ValueError("fit against the undeformed/base component recipe; existing deformation handles must be reviewed explicitly")
    handles=[]; diagnostics=[]
    regularization=1e-3
    epsilon=1e-4
    for point in points:
        ax,ay,az=point.anchor
        if not (-.5 <= ax <= .5 and 0 <= ay <= 1 and -.5 <= az <= .5):
            raise ValueError("component anchor must be inside normalized component bounds")
        px,py=point.pixel
        if not sx <= px <= sx+sw or not sy <= py <= sy+sh:
            raise ValueError("source pixel must lie inside the original scene crop")
        desired=np.asarray([px-sx,py-sy],dtype=float)
        current=project_local_point(plan,obj,point.anchor,[sw,sh],framing_aspect=aspect)
        columns=[]
        for axis in range(3):
            plus=list(point.anchor);minus=list(point.anchor)
            plus[axis]+=epsilon;minus[axis]-=epsilon
            columns.append((project_local_point(plan,obj,plus,[sw,sh],framing_aspect=aspect)-
                            project_local_point(plan,obj,minus,[sw,sh],framing_aspect=aspect))/(2*epsilon))
        jacobian=np.column_stack(columns)
        residual=desired-current
        # Minimum-norm damped inverse. Estimated landmarks are deliberately less aggressive.
        strength=1. if point.measurement == "measured" else .4
        normal=jacobian.T@jacobian + np.eye(3)*regularization*max(1.,np.trace(jacobian.T@jacobian)/3)
        offset=np.linalg.solve(normal,jacobian.T@residual)*strength
        offset=np.clip(offset,-.35,.35)
        norm=float(np.linalg.norm(offset))
        if norm > .45: offset*=.45/norm
        predicted=project_local_point(plan,obj,np.asarray(point.anchor)+offset,[sw,sh],framing_aspect=aspect)
        handle={"anchor":[float(v) for v in point.anchor],"offset":[float(v) for v in offset],
                "radius":point.radius,"strength":strength}
        # ComponentRecipe is the final safety contract for every proposed handle.
        ComponentRecipe.model_validate({**base,"deformation_handles":[handle]})
        handles.append(handle)
        diagnostics.append({"id":point.id,"measurement":point.measurement,"evidence":point.evidence,
                            "source_pixel":[px,py],"current_crop_pixel":current.tolist(),
                            "target_crop_pixel":desired.tolist(),"residual_px":residual.tolist(),
                            "suggested_offset":offset.tolist(),"predicted_crop_pixel":predicted.tolist(),
                            "before_error_px":float(np.linalg.norm(residual)),
                            "linearized_after_error_px":float(np.linalg.norm(desired-predicted))})
    proposed=copy.deepcopy(base);proposed["deformation_handles"]=handles
    proposed=ComponentRecipe.model_validate(proposed).model_dump()
    before=math.sqrt(sum(d["before_error_px"]**2 for d in diagnostics)/len(diagnostics))
    after=math.sqrt(sum(d["linearized_after_error_px"]**2 for d in diagnostics)/len(diagnostics))
    return {"method":"bounded-local-orthographic-deformation-fit-v1","applied":False,"certified":False,
            "object_id":obj["id"],"source_crop":analysis["scene_box"],"proposed_recipe":proposed,
            "landmark_diagnostics":diagnostics,"before_rms_px":before,"linearized_after_rms_px":after,
            "warnings":["This is a local linearized projection fit, not image-to-mesh reconstruction.",
                        "Rebuild and recapture the deformed component; reject it if silhouettes, topology or other regions regress."],
            "next_action":"Generate the proposed component recipe, validate a new scene plan, rebuild and compare a fresh capture to the original."}
