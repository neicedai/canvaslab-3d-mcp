"""Fixed Blender worker: --background --factory-startup --disable-autoexec
--python scripts3d/blender_components.py -- recipe.json output-directory.

The caller owns both paths. Recipes cannot supply paths, code, URLs or Blender
operators. No textures, imports, dependency installation or network are used.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sys

CONTRACT = "canvaslab-component-v1"
TEMPLATES = ("open_gate", "tiled_roof", "railing", "cargo_crate", "riverside_inn", "stone_quay", "tea_stall", "broadleaf_tree", "blossom_tree", "woven_boat", "rowing_boat", "timber_pier", "potted_garden", "bamboo_rack", "woven_basket")
FIELDS = {"template", "detail", "primary_color", "secondary_color"}


def validate_recipe_data(value):
    """Blender's bundled Python deliberately does not depend on Pydantic."""
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("recipe must contain only template, detail and colors")
    value = {"detail": 1, "primary_color": "#75513c", "secondary_color": "#397e73", **value}
    if value.get("template") not in TEMPLATES:
        raise ValueError("unsupported component template")
    if type(value["detail"]) is not int or value["detail"] not in (1, 2, 3):
        raise ValueError("detail must be integer 1, 2 or 3")
    for name in ("primary_color", "secondary_color"):
        if not isinstance(value[name], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value[name]):
            raise ValueError("colors must be #RRGGBB")
    return value


def linear(hex_color):
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    return tuple(v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels) + (1,)


def make_material(name, color, roughness=.8, metallic=0):
    import bpy
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = linear(color)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    result.diffuse_color = linear(color)
    return result


def box(name, center, size, material, bevel=0):
    import bpy
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    if bevel:
        modifier = obj.modifiers.new("soft handmade edges", "BEVEL")
        modifier.width = min(bevel, min(size) * .22)
        modifier.segments = 1
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def mesh(name, vertices, faces, material):
    import bpy
    import bmesh
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    data.update()
    bm = bmesh.new()
    bm.from_mesh(data)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    data.materials.append(material)
    return obj


def beam(name, a, b, thickness, material):
    from mathutils import Vector
    delta = Vector(b) - Vector(a)
    obj = box(name, (Vector(a) + Vector(b)) / 2, (thickness, thickness, delta.length), material, .016)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = delta.to_track_quat("Z", "Y")
    return obj


def cylinder(name, center, radius, depth, material, vertices=12):
    import bpy
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material)
    return obj


def roof(width, depth, base, rise, material, trim, detail):
    """Continuous closed curved roof shell + individually raised tile courses."""
    samples = 10 if detail == 1 else (14 if detail == 2 else 22)
    half_depth = depth / 2

    def height(t, x=0):
        return base + rise * ((1 - t) ** 1.7 + .14 * t ** 8) + .11 * rise * (abs(x) / (width / 2)) ** 8 * t ** 3

    for side in (-1, 1):
        vertices = []
        for offset in (0, -.09):
            for x in (-width / 2, width / 2):
                for i in range(samples + 1):
                    t = i / samples
                    vertices.append((x, side * half_depth * t, height(t, x) + offset))
        stride = samples + 1
        faces = []
        for i in range(samples):
            faces.extend([(i, i + 1, stride + i + 1, stride + i),
                          (2 * stride + i, 3 * stride + i, 3 * stride + i + 1, 2 * stride + i + 1)])
        for xrow in (0, 1):
            start = xrow * stride
            for i in range(samples):
                faces.append((start + i, start + i + 1, start + 2 * stride + i + 1, start + 2 * stride + i))
        faces.extend([(0, stride, 3 * stride, 2 * stride),
                      (samples, 2 * stride + samples, 3 * stride + samples, stride + samples)])
        mesh("continuous curved roof shell", vertices, faces, material)

        # Narrow closed strips describe ceramic tile ribs without huge sphere grids.
        cols = 15 if detail == 1 else (23 if detail == 2 else 37)
        for col in range(cols):
            x = -width / 2 + width * (col + .5) / cols
            verts = []
            radius = width / cols * .17
            for i in range(samples + 1):
                t = i / samples
                z = height(t, x) + .015
                for dx, dz in ((-radius, 0), (0, radius * .6), (radius, 0), (0, -.025)):
                    verts.append((x + dx, side * half_depth * t, z + dz))
            strip_faces = [(0, 3, 2, 1)]
            for i in range(samples):
                for j in range(4):
                    strip_faces.append((4 * i + j, 4 * i + (j + 1) % 4,
                                        4 * (i + 1) + (j + 1) % 4, 4 * (i + 1) + j))
            strip_faces.append(tuple(4 * samples + j for j in range(4)))
            mesh("raised ceramic tile course", verts, strip_faces, trim if col % 4 == 0 else material)

        tile_rows=5 if detail == 1 else (8 if detail == 2 else 14)
        for row in range(1,tile_rows):
            t = row / tile_rows
            beam("horizontal tile seam", (-width / 2, side * half_depth * t, height(t, width / 2) + .021),
                 (width / 2, side * half_depth * t, height(t, width / 2) + .021), .022, trim)
        # Eave fascia has the same upswept endpoints as the shell.
        for i in range(12):
            a = -width / 2 + width * i / 12
            b = -width / 2 + width * (i + 1) / 12
            beam("curved eave fascia", (a, side * half_depth, height(1, a) - .06),
                 (b, side * half_depth, height(1, b) - .06), .07, trim)
    beam("capped ridge", (-width / 2 - .05, 0, base + rise + .05),
         (width / 2 + .05, 0, base + rise + .05), .15, trim)
    for side in (-1, 1):
        beam("upturned ridge tip", (side * width / 2, 0, base + rise + .05),
             (side * (width / 2 + .18), 0, base + rise + .24), .1, trim)


def open_gate(recipe, materials):
    if recipe["detail"] == 3:
        g=RiverGeometry(3)
        g.craft_gate(recipe["primary_color"])
        g.finish()
        return
    wood, tile, dark, stone, metal = materials
    # Four actual free-standing posts: central opening remains open from both sides.
    for x in (-1.55, 1.55):
        for y in (-.46, .46):
            box("carved stone column foot", (x, y, .14), (.52, .52, .28), stone, .045)
            box("timber column", (x, y, 1.74), (.24, .24, 3.05), wood, .026)
            for z in (.36, 2.7):
                box("column collar", (x, y, z), (.3, .3, .13), dark, .015)
            direction = -1 if x > 0 else 1
            beam("diagonal open corbel", (x, y, 2.38), (x + direction * .55, y, 2.95), .13, wood)
        beam("gable tie", (x, -.7, 3.05), (x, .7, 3.05), .22, wood)
    for y in (-.46, .46):
        box("main exposed crossbeam", (0, y, 3.04), (3.75, .23, .26), wood, .025)
        box("secondary lintel", (0, y, 2.76), (3.12, .15, .12), dark, .01)
        for x in (-1.15, -.76, -.38, 0, .38, .76, 1.15):
            box("open transom spindle", (x, y, 2.88), (.055, .075, .15), wood, .005)
        for x in (-1.55, 1.55):
            for level in range(2):
                box("stacked bracket", (x, y, 3.21 + level * .11), (.48 + .13 * level, .36, .095), wood, .014)
    box("inset blank sign panel", (0, -.62, 3.04), (1.03, .07, .26), dark, .014)
    # Intentionally no fake lettering. Text belongs in explicit future asset annotations.
    roof(4.35, 1.95, 3.37, .94, tile, dark, recipe["detail"])


def railing(recipe, materials):
    wood, tile, dark, stone, metal = materials
    for x in (-1.9, 0, 1.9):
        box("railing post", (x, 0, .62), (.2, .24, 1.24), wood, .02)
        box("post cap", (x, 0, 1.27), (.28, .3, .1), dark, .02)
        box("stone foot", (x, 0, .09), (.3, .34, .18), stone, .02)
    for z in (.3, 1.11):
        box("continuous rail", (0, 0, z), (4.02, .16, .13), dark, .018)
    for side in (-1, 1):
        center = side * .95
        beam("diagonal brace", (center - .8, 0, .37), (center + .8, 0, 1.03), .072, wood)
        beam("diagonal brace", (center + .8, .012, .37), (center - .8, .012, 1.03), .072, wood)
        if recipe["detail"] >= 2:
            for offset in (-.52, 0, .52):
                box("thin upright", (center + offset, .025, .69), (.045, .06, .65), wood, .004)


def crate(recipe, materials):
    wood, tile, dark, stone, metal = materials
    # Six planked faces, interior shell, braces and metal corner fasteners.
    planks = 5 if recipe["detail"] == 1 else 7
    for side in (-1, 1):
        for i in range(planks):
            along = -.87 + 1.74 * (i + .5) / planks
            material = dark if i % 3 == 0 else wood
            box("front back plank", (along, side * .86, .92), (1.74 / planks - .015, .115, 1.74), material, .013)
            box("side plank", (side * .87, along, .92), (.115, 1.74 / planks - .015, 1.74), material, .013)
            box("lid base plank", (along, 0, .92 + side * .86), (1.74 / planks - .015, 1.65, .11), material, .013)
        for z in (.18, 1.67):
            box("face brace", (0, side * .942, z), (1.85, .075, .15), dark, .015)
            box("side brace", (side * .952, 0, z), (.075, 1.85, .15), dark, .015)
        beam("diagonal shipping brace", (-.72, side * .99, .28), (.72, side * .99, 1.56), .12, wood)
        for x in (-.76, .76):
            for z in (.18, 1.67):
                rivet = cylinder("iron nail", (x, side * .99, z), .035, .035, metal, 8)
                rivet.rotation_euler[0] = math.pi / 2


class RiverGeometry:
    """Batched solid geometry for the measured riverside component family.

    All dimensions and topology come from fixed recipes; caller input is colors
    and one bounded detail level, never mesh data or Python.
    """
    def __init__(self, detail=1):
        import random
        self.rng = random.Random(82031)
        self.parts = {}
        self.materials = {}
        self.detail = detail

    def add(self, verts, faces, color, treatment="flat"):
        points, polygons = self.parts.setdefault((color, treatment), ([], []))
        offset = len(points)
        points.extend(verts)
        polygons.extend(tuple(i + offset for i in face) for face in faces)

    def box(self, c, s, color, yaw=0):
        a,b,d = [v/2 for v in s]
        raw=[(-a,-b,-d),(a,-b,-d),(a,b,-d),(-a,b,-d),(-a,-b,d),(a,-b,d),(a,b,d),(-a,b,d)]
        cs,sn=math.cos(yaw),math.sin(yaw)
        self.add([(c[0]+x*cs-y*sn,c[1]+x*sn+y*cs,c[2]+z) for x,y,z in raw],
                 [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],color,
                 "bevel" if self.detail >= 2 and min(s) >= .065 else "flat")

    def rod(self,a,b,r,color,n=8,r2=None):
        from mathutils import Vector
        if self.detail >= 2:n=max(n,(16 if self.detail == 3 else 12) if r >= .04 else 8)
        av,bv=Vector(a),Vector(b); axis=(bv-av).normalized()
        other=Vector((0,0,1)) if abs(axis.z)<.9 else Vector((0,1,0))
        u=axis.cross(other).normalized();v=axis.cross(u).normalized()
        r2=r if r2 is None else r2
        verts=[tuple(center+(u*math.cos(i*2*math.pi/n)+v*math.sin(i*2*math.pi/n))*radius)
               for center,radius in ((av,r),(bv,r2)) for i in range(n)]
        caps=[tuple(reversed(range(n))),tuple(range(n,2*n))]
        sides=[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
        if self.detail >= 2:
            self.add(verts,caps,color)
            self.add(verts,sides,color,"smooth")
        else:self.add(verts,caps+sides,color)

    def ball(self,c,s,color):
        phi=(1+math.sqrt(5))/2
        verts=[(-1,phi,0),(1,phi,0),(-1,-phi,0),(1,-phi,0),(0,-1,phi),(0,1,phi),(0,-1,-phi),(0,1,-phi),(phi,0,-1),(phi,0,1),(-phi,0,-1),(-phi,0,1)]
        k=math.sqrt(1+phi*phi)
        faces=[(0,11,5),(0,5,1),(0,1,7),(0,7,10),(0,10,11),(1,5,9),(5,11,4),(11,10,2),(10,7,6),(7,1,8),(3,9,4),(3,4,2),(3,2,6),(3,6,8),(3,8,9),(4,9,5),(2,4,11),(6,2,10),(8,6,7),(9,8,1)]
        verts=[tuple(v/k for v in p) for p in verts]
        if self.detail >= 2:
            midpoints={}
            def midpoint(a,b):
                edge=tuple(sorted((a,b)))
                if edge not in midpoints:
                    p=tuple((verts[a][i]+verts[b][i])*.5 for i in range(3));length=math.sqrt(sum(v*v for v in p))
                    midpoints[edge]=len(verts);verts.append(tuple(v/length for v in p))
                return midpoints[edge]
            refined=[]
            for a,b,c0 in faces:
                ab,bc,ca=midpoint(a,b),midpoint(b,c0),midpoint(c0,a)
                refined.extend([(a,ab,ca),(b,bc,ab),(c0,ca,bc),(ab,bc,ca)])
            faces=refined
        self.add([(c[0]+x*s[0],c[1]+y*s[1],c[2]+z*s[2]) for x,y,z in verts],faces,color,
                 "smooth" if self.detail >= 2 else "flat")

    def rope(self,a,b,sag=.15,color="#a58b5a",radius=.018):
        points=[(a[0]+(b[0]-a[0])*i/12,a[1]+(b[1]-a[1])*i/12,
                 a[2]+(b[2]-a[2])*i/12-sag*4*(i/12)*(1-i/12)) for i in range(13)]
        for x,y in zip(points,points[1:]):self.rod(x,y,radius,color,5)

    def leaf(self,c,length,color,yaw=0,tilt=.4,width=.45):
        """Thin closed curved leaflet, not a large sphere or alpha billboard."""
        count=12;cs,sn=math.cos(yaw),math.sin(yaw);ct,st=math.cos(tilt),math.sin(tilt)
        local=[(0,0,length*.09),(0,0,-length*.025)]
        for i in range(count):
            a=2*math.pi*i/count
            local.append((math.sin(a)*length*width,math.cos(a)*length,length*.04*math.cos(a)))
        verts=[]
        for x,y,z in local:
            yy,zz=y*ct-z*st,y*st+z*ct
            verts.append((c[0]+x*cs-yy*sn,c[1]+x*sn+yy*cs,c[2]+zz))
        faces=[]
        for i in range(count):
            a,b=2+i,2+(i+1)%count
            faces.extend([(0,b,a),(1,a,b)])
        self.add(verts,faces,color,"smooth")

    def pot(self,x,y,z,r=.18,height=.35):
        col=self.rng.choice(["#857951","#887047","#776244","#aaa17a"])
        if self.detail == 3:
            # Lathed vessel with an open mouth, rolled rim, internal wall and earth.
            profile=[(.62,0),(.72,.06),(.98,.24),(1,.6),(.87,.91),(1.01,.94),(1.01,1.04),(.82,1.04),(.76,.89),(.76,.70)]
            verts=[(x+r*rr*math.cos(a*math.pi/12),y+r*rr*math.sin(a*math.pi/12),z+zz*height) for rr,zz in profile for a in range(24)]
            faces=[(i*24+j,i*24+(j+1)%24,(i+1)*24+(j+1)%24,(i+1)*24+j) for i in range(len(profile)-1) for j in range(24)]
            faces.append(tuple(reversed(range(24))))
            faces.append(tuple(range((len(profile)-1)*24,len(profile)*24)))
            self.add(verts,faces,col,"smooth")
            self.rod((x,y,z+height*.76),(x,y,z+height*.78),r*.74,"#493e28",24)
            return
        self.rod((x,y,z),(x,y,z+height),r*.7,col,10,r)
        self.rod((x,y,z+height),(x,y,z+height+.035),r*1.07,col,10)
        self.rod((x,y,z+height+.036),(x,y,z+height+.039),r*.8,"#493e28",10)

    def plant(self,x,y,z,scale=1):
        self.pot(x,y,z,.17*scale,.3*scale)
        for i in range(15 if self.detail == 3 else 9):
            angle=i*2.4; h=self.rng.uniform(.35,.75)*scale
            tip=(x+math.cos(angle)*.26*scale,y+math.sin(angle)*.26*scale,z+.3*scale+h)
            self.rod((x,y,z+.26*scale),tip,.012*scale,"#70773c",5)
            color=self.rng.choice(["#7e914c","#9d9f55","#5c753f"])
            if self.detail >= 2:
                self.leaf(tip,(.17 if self.detail == 3 else .22)*scale,color,angle,.55,.4)
                if self.detail == 3:
                    for step in (.45,.70):
                        c=(x+(tip[0]-x)*step,y+(tip[1]-y)*step,z+.3*scale+h*step)
                        self.leaf(c,.13*scale,color,angle+1.2,.35,.38)
            else:self.ball(tip,(.1*scale,.19*scale,.12*scale),color)

    def lantern(self,x,y,z,scale=1):
        self.rod((x,y,z+.23*scale),(x,y,z+.48*scale),.012*scale,"#423c2e",6)
        if self.detail == 3:
            verts=[(x+math.cos(a*math.pi/8)*.15*math.sin(t*math.pi/12)**.55*scale,
                    y+math.sin(a*math.pi/8)*.15*math.sin(t*math.pi/12)**.55*scale,
                    z+math.cos(t*math.pi/12)*.24*scale) for t in range(1,12) for a in range(16)]
            faces=[(row*16+i,row*16+(i+1)%16,(row+1)*16+(i+1)%16,(row+1)*16+i) for row in range(10) for i in range(16)]
            faces.extend([tuple(reversed(range(16))),tuple(range(160,176))])
            self.add(verts,faces,"#d9482f","smooth")
        else:self.ball((x,y,z),(.15*scale,.15*scale,.24*scale),"#d9482f")
        for dz in (-.23,.23):self.rod((x,y,z+dz*scale-.025),(x,y,z+dz*scale+.025),.085*scale,"#c79348",10)
        self.rod((x,y,z-.24*scale),(x,y,z-.48*scale),.023*scale,"#bc6331",6)
        for i in range(8):
            a=i*math.pi/4
            if self.detail == 3:
                self.stroke([(x+math.cos(a)*(.152*math.sin(t*math.pi/6)**.55)*scale,
                              y+math.sin(a)*(.152*math.sin(t*math.pi/6)**.55)*scale,
                              z+math.cos(t*math.pi/6)*.24*scale) for t in range(1,6)],.006*scale,"#e16c3e",6)
            else:
                self.rod((x+.1*math.cos(a)*scale,y+.1*math.sin(a)*scale,z-.16*scale),
                         (x+.1*math.cos(a)*scale,y+.1*math.sin(a)*scale,z+.16*scale),.009*scale,"#e16c3e",4)

    def roof(self,c,w,d,rise,columns=26,rows=10):
        if self.detail == 3:
            return self.craft_roof(c,w,d,rise,columns,rows)
        cx,cy,base=c; dark="#4c5145"
        colors=["#66776a","#758074","#566959","#839080","#536558"]
        def height(t,x):return base+rise*((1-t)**1.8+.14*t**8)+.2*(abs(x)/(w/2))**10*t*t
        for side in (-1,1):
            # Closed substrate, following the same curve as the raised clay tiles.
            for row in range(rows):
                t0=row/rows;t1=(row+1)/rows
                verts=[(cx+x,cy+side*d*.5*t,height(t,x)+off) for off in (0,-.065) for t in (t0,t1) for x in (-w/2,w/2)]
                self.add(verts,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],dark)
            for col in range(columns):
                x=-w/2+(col+.5)*w/columns; radius=w/columns*.46
                for row in range(rows):
                    t0=row/rows;t1=(row+.96)/rows
                    segments=6 if self.detail == 2 else 4
                    profile=[(-radius,-.01)]+[(radius*math.cos(math.pi-j*math.pi/segments),radius*.55*math.sin(j*math.pi/segments)) for j in range(segments+1)]+[(radius,-.01)]
                    verts=[(cx+x+dx,cy+side*d*.5*t,height(t,x)+dz+.025) for t in (t0,t1) for dx,dz in profile]
                    n=len(profile);faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
                    faces +=[(j,(j+1)%n,(j+1)%n+n,j+n) for j in range(n)]
                    color=self.rng.choice(colors)
                    if self.detail == 2:
                        self.add(verts,faces[:2],color)
                        self.add(verts,faces[2:],color,"smooth")
                    else:self.add(verts,faces,color)
            for edge in (-1,1):
                x=edge*w/2
                for i in range(12):
                    t0=i/12;t1=(i+1)/12
                    self.rod((cx+x,cy+side*d*.5*t0,height(t0,x)+.09),(cx+x,cy+side*d*.5*t1,height(t1,x)+.09),.073,dark,7)
                self.rod((cx+x,cy+side*d*.5,height(1,x)+.08),(cx+x,cy+side*(d*.5+.16),height(1,x)+.35),.075,dark,7)
        self.rod((cx-w*.52,cy,base+rise+.09),(cx+w*.52,cy,base+rise+.09),.09,dark,8)
        for side in (-1,1):self.rod((cx+side*w*.5,cy,base+rise+.09),(cx+side*w*.53,cy,base+rise+.36),.09,dark,8)

    def window(self,x,y,z,w,h):
        if self.detail == 3:return self.craft_window(x,y,z,w,h)
        wood="#665033";gold="#b78845"
        self.box((x,y+.06,z),(w,.07,h),"#302e20")
        # Amber panes are volumetric inset panels, not a flat picture of a facade.
        for ix in range(3):
            for iz in range(4):self.box((x-w*.36+ix*w*.36,y,z-h*.36+iz*h*.24),(w*.27,.035,h*.18),self.rng.choice(["#977237","#bf964f","#6a512b"]))
        for dx in (-w*.5,0,w*.5):self.box((x+dx,y-.04,z),(.045,.11,h+.12),wood)
        for dz in (-h*.5,0,h*.5):self.box((x,y-.05,z+dz),(w+.08,.12,.055),wood)
        for dx in (-w*.3,w*.3):self.box((x+dx,y-.075,z),(.022,.08,h),gold)
        for dz in (-h*.31,h*.31):self.box((x,y-.078,z+dz),(w,.08,.022),gold)

    def bench(self,x,y,z,width=1.2):
        self.box((x,y,z+.36),(width,.4,.09),"#82603b")
        for dx in (-width*.4,width*.4):
            for dy in (-.12,.12):self.box((x+dx,y+dy,z+.17),(.07,.07,.34),"#614a30")

    def inn(self):
        if self.detail == 3:return self.craft_inn()
        plaster="#cbbd93"; wood="#5d4830"
        self.box((0,.08,2.63),(6,3.85,5.12),plaster)
        for z in (.2,2.65,5.06):self.box((0,-1.99,z),(6.15,.18,.17),wood)
        for x in (-2.9,-1,1,2.9):self.box((x,-2.04,2.58),(.15,.18,5.05),wood)
        # Right side exposed gable framing and narrow windows.
        for y in (-1.6,0,1.5):
            self.box((3.03,y,2.6),(.13,.15,5.1),wood)
            for z in (1.25,3.9):self.box((3.045,y+.36,z),(.035,.6,1.1),"#736040")
        for x in (-2.35,-1.45,1.45,2.35):self.window(x,-2.10,1.40,.73,1.6)
        for x in (-2.35,-1.4,-.46,.46,1.4,2.35):self.window(x,-2.10,4.0,.77,1.72)
        self.box((0,-2.14,1.28),(1.55,.06,2.20),"#29281c")
        self.box((0,-2.65,.12),(1.9,1.30,.22),"#b3a786")
        # Part-open doors, revealing shadowed threshold and interior furniture.
        for x,rot in ((-.79,-.55),(.79,.48)):
            self.box((x,-2.34,1.21),(.50,.10,2.08),"#735032",rot)
            for dz in (.57,1.57):self.box((x,-2.41,dz),(.38,.07,.72),"#60442c",rot)
        self.bench(0,-2.14,.17,.7)
        self.box((0,-2.39,3.08),(6.15,.95,.16),wood)
        for x in [-2.86+i*.25 for i in range(24)]:self.box((x,-2.82,3.42),(.055,.065,.58),"#85643b")
        for z in (3.15,3.71):self.box((0,-2.82,z),(6.10,.12,.08),wood)
        for x in (-3,-1,1,3):self.box((x,-2.75,4.17),(.12,.14,2.0),wood)
        self.roof((0,0,5.17),7.04,5.4,1.6,32,13)
        for x in (-1.94,1.94):self.roof((x,-2.22,2.48),2.65,1.35,.56,14,6)
        self.roof((0,-2.08,2.85),1.98,1.40,.5,12,6)
        for x,z in ((-2.92,4.83),(-.94,4.72),(1.06,4.72),(2.95,4.83),(-2.92,2.2),(-.98,2.15),(.98,2.15),(2.96,2.2)):
            self.lantern(x,-2.68,z,.80)
        self.box((0,-2.94,2.81),(1.48,.12,.38),"#3d392b")
        for x in (-2.1,2.06):
            self.bench(x,-2.75,.23,1.2)
            for dx in (-.26,.3):self.pot(x+dx,-2.75,.65,.09,.15)
        for x in (-3.04,-1.10,1.12,3.05):self.plant(x,-2.88,.13,.83)
        # Stone base blocks and chipped plaster accents make the volume readable.
        for i in range(12):self.box((-2.8+i*.51,-2.11,.11),(.48,.35,.22),self.rng.choice(["#a8a38d","#b6ae96","#918b72"]))
        for i in range(28):
            x=self.rng.uniform(-2.9,2.9); z=self.rng.uniform(.3,4.9)
            self.box((x,2.015,z),(.14,.015,.22),"#b4a480")

    def quay(self):
        if self.detail == 3:return self.craft_quay()
        cols=["#aaa791","#bfb8a0","#c7bea3","#989b87","#b1ae94"]
        self.box((0,0,.41),(14,6,.82),"#7f8574")
        for ix in range(20):
            for iy in range(10):
                x=-6.66+ix*.70+(iy%2)*.11;y=-2.70+iy*.59
                self.box((min(x,6.61),y,.875+self.rng.uniform(-.012,.012)),(.67,.565,.15),self.rng.choice(cols),self.rng.uniform(-.018,.018))
        for side in (-1,1):
            for row in range(3):
                for i in range(23):self.box((-6.69+i*.6+(row%2)*.13,side*3,.16+row*.265),(.565,.24,.25),self.rng.choice(cols))
        for x in (-6.99,6.99):
            for row in range(3):
                for j in range(10):self.box((x,-2.7+j*.6,.16+row*.265),(.24,.56,.25),self.rng.choice(cols))
        # Four wide steps descend from the waterfront to the water.
        for j in range(4):
            h=.76-j*.17; cy=-3.20-j*.34
            for i in range(4):self.box((1.65+i*.39,cy,h*.5),(.375,.38,h),self.rng.choice(cols))
        # Rear and side boundary garden walls.
        for j in range(12):
            for row in range(2):self.box((-6.55+j*.58,2.72,1.10+row*.3),(.55,.35,.28),self.rng.choice(cols))
        for j in range(6):
            for row in range(2):self.box((6.7,.1+j*.49,1.1+row*.3),(.35,.47,.28),self.rng.choice(cols))
        # Wooden mooring posts and drooping ropes, with a gap at the stairs.
        positions=[-6.1,-4.5,-2.6,-.6,3.8,5.5,6.7]
        for x in positions:
            self.rod((x,-3.10,.15),(x,-3.1,1.43),.075,"#706044",9)
            for z in (1.13,1.19,1.25):self.rod((x,-3.1,z),(x,-3.1,z+.04),.09,"#b7a57d",10)
        for x1,x2 in zip(positions,positions[1:]):
            if x2-x1<2.5:self.rope((x1,-3.1,1.25),(x2,-3.1,1.25),.25)
        for i in range(38):
            x=self.rng.uniform(-6.8,6.8);y=-3.15;z=self.rng.uniform(.12,.72)
            self.ball((x,y,z),(.14,.055,.13),self.rng.choice(["#7d8850","#6b7949","#99a066"]))

    def tree(self,blossom=False):
        if self.detail == 3:return self.craft_tree(blossom)
        col="#786044";self.rod((0,0,0),(.12,0,2.2),.17,col,9,.11)
        palette=["#78905a","#9ca361","#5e7856","#abb16f","#526e50"] if not blossom else ["#d58d88","#e6ac9e","#f0c7b3","#cb8585","#ebafa6"]
        for i in range(13):
            a=i*2.4;r=self.rng.uniform(.6,1.45);h=self.rng.uniform(1.8,3.4)
            end=(math.cos(a)*r,math.sin(a)*r,h)
            self.rod((.04,0,.8+h*.27),end,.06,col,7,.026)
            for j in range(24 if not blossom else 32):
                theta=self.rng.random()*6.28;rr=self.rng.uniform(.06,.58)
                c=(end[0]+math.cos(theta)*rr,end[1]+math.sin(theta)*rr,end[2]+self.rng.uniform(-.20,.55))
                radius=self.rng.uniform(.13,.27) if not blossom else self.rng.uniform(.075,.17)
                color=self.rng.choice(palette)
                if self.detail == 2:
                    # Separate thin leaves / petals preserve a finely broken silhouette;
                    # smoothing big balls alone makes a canopy look like grapes.
                    for k in range(3):
                        a=theta+k*2.094
                        tip=(c[0]+math.cos(a)*radius*.55,c[1]+math.sin(a)*radius*.55,c[2]+(k-1)*radius*.25)
                        self.leaf(tip,radius*(.9 if blossom else 1.1),color,a,.25+k*.22,.8 if blossom else .48)
                else:self.ball(c,(radius,radius*.87,radius*.94),color)
        for i in range(6):self.ball((self.rng.uniform(-.3,.3),self.rng.uniform(-.3,.3),.07),(.22,.19,.12),"#8c9078")

    def tea(self):
        if self.detail == 3:return self.craft_tea()
        wood="#7e5d37"
        for x in (-1.5,1.5):
            for y in (-.78,.78):self.rod((x,y,0),(x,y,2.80),.045,wood,8)
        self.box((0,-.1,.9),(3.05,.82,.15),wood)
        for x in (-1.39,1.39):self.box((x,-.1,.45),(.13,.67,.88),"#634a2d")
        for i in range(15):self.box((-1.43+i*.204,-.52,.49),(.19,.07,.8),self.rng.choice(["#7e5d37","#9a7344","#866139"]))
        # Closed fabric canopy, sagged edge; underside is modeled, not a billboard.
        vs=[]
        for off in (0,-.035):
            for y in (-1.03,0,1.03):
                for x in (-1.76,0,1.76):vs.append((x,y,2.79+.24*(1-abs(y))-.16*(1-abs(x)/1.76)+off))
        faces=[]
        for j in range(2):
            for i in range(2):
                a=j*3+i;faces.extend([(a,a+1,a+4,a+3),(a+9,a+12,a+13,a+10)])
        edge=[0,1,2,5,8,7,6,3]
        for a,b in zip(edge,edge[1:]+edge[:1]):faces.append((a,b,b+9,a+9))
        self.add(vs,faces,"#a3ac89")
        for x in (-1.3,-.7,0,.7,1.3):
            self.pot(x,-.12,.99,.13,.21)
            self.rod((x,-.12,1.21),(x,-.12,1.24),.10,"#bba77c",10)
        for x in (-.42,-.2,.02,.24):self.pot(x,-.39,1.0,.054,.075)
        for x in (-.95,.95):self.bench(x,-.99,0,.5)
        self.plant(-1.66,-.65,0,.85);self.pot(1.55,-.68,0,.31,.59)
        for x in (-1.06,0,1.06):self.box((x,.49,.5),(.91,.24,.85),"#967346")

    def boat(self,covered=True):
        if self.detail == 3:return self.craft_boat(covered)
        length=5.1 if covered else 3.45;width=1.40 if covered else 1.28
        stations=[(-.5,.08,.24),(-.43,.60,.11),(-.27,.94,.025),(0,1,0),(.27,.94,.035),(.43,.60,.14),(.5,.08,.27)]
        if self.detail == 2:
            refined=[]
            for i in range(len(stations)-1):
                p0,p1,p2,p3=stations[max(0,i-1)],stations[i],stations[i+1],stations[min(len(stations)-1,i+2)]
                refined.append(p1)
                mid=[(p1[0]+p2[0])*.5]
                for j in (1,2):mid.append(max(0,min(1,(-p0[j]+9*p1[j]+9*p2[j]-p3[j])/16)))
                refined.append(tuple(mid))
            stations=refined+[stations[-1]]
        cols=["#7e613e","#98774e","#695338","#a38458"]
        for side in (-1,1):
            for band in range(4):
                for i in range(len(stations)-1):
                    verts=[]
                    for inset in (0,.06):
                        for x,w,bow in stations[i:i+2]:
                            for t in (band/4,(band+1)/4):verts.append((x*length,side*(width*.5*w*(.55+.45*t)-inset),.09+t*.43+bow))
                    self.add(verts,[(0,2,3,1),(4,5,7,6),(0,1,5,4),(2,6,7,3),(0,4,6,2),(1,3,7,5)],cols[(i+band)%4])
            for i in range(len(stations)-1):
                x,w,bow=stations[i];xx,ww,bb=stations[i+1]
                self.rod((x*length,side*width*.5*w,.54+bow),(xx*length,side*width*.5*ww,.54+bb),.045,"#b19568",7)
        self.box((0,0,.19),(length*.79,width*.58,.12),"#5d4931")
        for i in range(16):
            x=-length*.38+i*length*.76/15
            self.box((x,0,.30),(.16,width*.68,.075),cols[i%4])
        for x in (-length*.32,0,length*.31):self.box((x,0,.44),(.21,width*.85,.08),"#ad8857")
        if covered:
            start,end=-1.55,.62;radius=width*.49
            # Bamboo woven canopy: actual arched shell, transverse hoops and strips.
            for i in range(18):
                x=start+(end-start)*i/17
                arc_steps=24 if self.detail == 2 else 12
                for j in range(arc_steps):
                    a=j*math.pi/arc_steps;b=(j+1)*math.pi/arc_steps
                    p=(x,math.cos(a)*radius,.53+math.sin(a)*.93)
                    q=(x,math.cos(b)*radius,.53+math.sin(b)*.93)
                    self.rod(p,q,.026,"#b09b6c",6)
            for j in range(15):
                a=(j+.3)*math.pi/15
                self.rod((start,math.cos(a)*radius,.53+math.sin(a)*.93),(end,math.cos(a)*radius,.53+math.sin(a)*.93),.022,"#8a794e",6)
            arc_steps=24 if self.detail == 2 else 12
            for j in range(arc_steps):
                a=j*math.pi/arc_steps;b=(j+1)*math.pi/arc_steps
                verts=[(x,math.cos(t)*r,.53+math.sin(t)*h) for r,h in ((radius,.93),(radius-.024,.906)) for x in (start,end) for t in (a,b)]
                self.add(verts,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],self.rng.choice(["#a3936d","#91815e","#ab9975"]))
            self.rod((-2,-.46,.45),(-2,-.46,1.77),.025,"#675339",7)
            self.rod((-2,-.46,1.77),(-1.7,-.46,1.77),.023,"#675339",7)
            self.lantern(-1.7,-.46,1.51,.4)
        else:
            self.pot(.8,0,.35,.19,.21)
            for i in range(7):self.rope((-.4+i*.09,-.37,.46),(.05+i*.09,.32,.44),.04,"#c0ab78",.011)
        self.rod((length*.25,.35,.49),(length*.59,.92,.04),.025,"#715536",7)
        self.box((length*.57,.87,.055),(.32,.16,.04),"#967445",-.6)

    def pier(self):
        for ix in range(15):
            self.box((-1.72+ix*.245,0,.91),(.23,1.70,.12),self.rng.choice(["#8f704b","#a08660","#9d7951","#7d6244"]))
        for x in (-1.57,1.57):
            for y in (-.73,.73):
                self.rod((x,y,0),(x,y,1.38),.095,"#6f5b40",9)
                for z in (1.08,1.13,1.18):self.rod((x,y,z),(x,y,z+.038),.108,"#c2ad83",10)
            self.rope((x,-.73,1.2),(x,.73,1.2),.19)
        for y in (-.63,.63):self.box((0,y,.72),(3.7,.14,.17),"#644d34")
        if self.detail == 3:
            for i in range(15):
                x=-1.72+i*.245
                for y in (-.62,.62):self.rod((x,y,.970),(x,y,.982),.017,"#493e28",8)
                for j in range(2):
                    self.stroke([(x-.07+j*.10,-.65,.971),(x-.055+j*.10,-.08,.972),(x-.067+j*.10,.64,.971)],.004,"#7d6244",6)

    def garden(self):
        for i,(x,y,s) in enumerate([(-.8,-.4,1.0),(.65,-.2,.8),(.1,.6,1.3),(-.75,.6,.7)]):self.plant(x,y,.05,s)
        self.bench(.1,.1,0,1.2)
        self.pot(.45,.15,.44,.12,.17)

    def oriented(self, action, center, yaw):
        """Transform only the fixed geometry emitted by a local component call."""
        previous={key:len(value[0]) for key,value in self.parts.items()}
        action();cs,sn=math.cos(yaw),math.sin(yaw)
        for key,(verts,_) in self.parts.items():
            for i in range(previous.get(key,0),len(verts)):
                x,y,z=verts[i]
                verts[i]=(center[0]+x*cs-y*sn,center[1]+x*sn+y*cs,center[2]+z)

    def stroke(self,points,r,color,n=8):
        for a,b in zip(points,points[1:]):self.rod(a,b,r,color,n)

    def craft_stone(self,c,s,color,yaw=0):
        # Eight independently clipped corners and slightly sloping top faces.
        # Real joints/shadows survive close views without random cube rotations.
        sx,sy,sz=[v/2 for v in s];cs,sn=math.cos(yaw),math.sin(yaw)
        cuts=[self.rng.uniform(.055,.18)*min(s[0],s[1]) for _ in range(4)]
        outline=[(-sx+cuts[0],-sy),(sx-cuts[1],-sy),(sx,-sy+cuts[1]),(sx,sy-cuts[2]),
                 (sx-cuts[2],sy),(-sx+cuts[3],sy),(-sx,sy-cuts[3]),(-sx,-sy+cuts[0])]
        verts=[]
        for level,inset in ((-sz,.015),(sz-.02,0),(sz,.014)):
            for x,y in outline:
                x*=1-inset/max(sx,.05);y*=1-inset/max(sy,.05)
                verts.append((c[0]+x*cs-y*sn,c[1]+x*sn+y*cs,c[2]+level+self.rng.uniform(-.008,.008)))
        faces=[tuple(reversed(range(8))),tuple(range(16,24))]
        faces.extend((row*8+i,row*8+(i+1)%8,(row+1)*8+(i+1)%8,(row+1)*8+i) for row in range(2) for i in range(8))
        self.add(verts,faces,color)

    def craft_roof(self,c,w,d,rise,columns=36,rows=15,hip=False):
        cx,cy,base=c;dark="#3a4139";wood="#5d4830"
        colors=["#424e46","#526155","#38463e","#617061","#455648"]
        columns=max(columns,round(w*4.6));rows=max(rows,round(d*2.0))
        hip_cut=w*.13 if hip else 0;split=.43
        def span(t):return w/2-hip_cut*(1-max(0,(t-split)/(1-split)))
        def height(t,x):
            # The source roof is a nearly planar pitch. Only the last eave
            # course and the four corners curl upward, not the entire slope.
            return base+rise*(max(0,1-t)+.035*math.sin(math.pi*min(t,1))+.035*t**8)+.25*(abs(x)/(w/2))**10*t**5
        def point(u,t,side):
            x=u*span(t);return (cx+x,cy+side*d*.5*t,height(t,x))
        def tile_strip(p,q,r,color):
            # Curved ceramic half-cylinder with distinct overlap lip and sealed underside.
            dx,dy=q[0]-p[0],q[1]-p[1];length=math.hypot(dx,dy)
            vx,vy=-dy/length,dx/length
            profile=[(-r,-.008)]+[(r*math.cos(math.pi-j*math.pi/6),r*.72*math.sin(j*math.pi/6)) for j in range(7)]+[(r,-.008)]
            verts=[(pt[0]+vx*x,pt[1]+vy*x,pt[2]+z+.019) for pt in (p,q) for x,z in profile]
            n=len(profile)
            self.add(verts,[tuple(reversed(range(n))),tuple(range(n,2*n))],color)
            self.add(verts,[(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)],color,"smooth")
            # Raised ceramic overlap lip makes each short barrel tile readable.
            # One closed half-ring, not a horizontal wire across the roof.
            lip=[]
            ax,ay=dx/length,dy/length
            for along in (-.018,.006):
                for j in range(7):
                    a=j*math.pi/6
                    lip.append((q[0]+vx*r*math.cos(a)+ax*along,q[1]+vy*r*math.cos(a)+ay*along,q[2]+r*.72*math.sin(a)+.025))
            self.add(lip,[(j,j+1,j+8,j+7) for j in range(6)]+[(0,7,13,6),tuple(reversed(range(7))),tuple(range(7,14))],color,"smooth")
        for side in (-1,1):
            for row in range(rows):
                t0=row/rows;t1=(row+1)/rows
                # Sample the substrate across X too. Joining only the raised
                # corner endpoints would lift a flat shelf above the center tiles.
                across=12;stride=across+1
                vs=[tuple(v+(off if k==2 else 0) for k,v in enumerate(point(-1+2*i/across,t,side))) for off in (0,-.075) for t in (t0,t1) for i in range(stride)]
                faces=[(0,stride,3*stride,2*stride),(stride-1,3*stride-1,4*stride-1,2*stride-1)]
                for i in range(across):
                    faces.extend([(i,i+1,stride+i+1,stride+i),(2*stride+i,3*stride+i,3*stride+i+1,2*stride+i+1),
                                  (i,2*stride+i,2*stride+i+1,i+1),(stride+i,stride+i+1,3*stride+i+1,3*stride+i)])
                self.add(vs,faces,dark)
            for col in range(columns):
                u=-1+2*(col+.5)/columns
                for row in range(rows):
                    t0=row/rows;t1=(row+1.04)/rows
                    p,q=point(u,t0,side),point(u,min(1.015,t1),side)
                    tile_strip(p,q,w/columns*.44,self.rng.choice(colors))
                # Round exposed rafter ends under every alternate tile course.
                if col%2==0:
                    p=point(u,.86,side);q=point(u,1,side)
                    self.rod((p[0],p[1],p[2]-.13),(q[0],q[1],q[2]-.13),.031,wood,8)
            for edge in (-1,1):
                self.stroke([tuple(v+(.065 if k==2 else 0) for k,v in enumerate(point(edge,i/24,side))) for i in range(25)],.055,dark)
            self.stroke([tuple(v+(-.07 if k==2 else 0) for k,v in enumerate(point(-1+2*i/24,1,side))) for i in range(25)],.060,wood)
        self.rod((cx-span(0)-.11,cy,base+rise+.07),(cx+span(0)+.11,cy,base+rise+.07),.092,dark)
        for side in (-1,1):
            x=cx+side*span(0)
            self.stroke([(x,cy,base+rise+.07),(x+side*.12,cy,base+rise+.18),(x+side*.15,cy,base+rise+.37)],.066,dark)
        if hip:
            # True sloping end roofs below a small framed triangular gable.
            for side in (-1,1):
                def end_point(u,t):
                    tt=split+(1-split)*t;x=side*span(tt)
                    return (cx+x,cy+u*d*.5*tt,height(tt,x))
                for row in range(7):
                    t0=row/7;t1=(row+1)/7
                    vs=[tuple(v+(off if k==2 else 0) for k,v in enumerate(end_point(u,t))) for off in (0,-.07) for t in (t0,t1) for u in (-1,1)]
                    self.add(vs,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],dark)
                for col in range(23):
                    u=-1+2*(col+.5)/23
                    for row in range(7):tile_strip(end_point(u,row/7),end_point(u,(row+1.03)/7),d/23*.39,self.rng.choice(colors))
                x=cx+side*span(0);z0=height(split,span(split))-.06
                # Closed triangular gable with three exposed timber braces.
                self.add([(x,cy-d*.5*split,z0),(x,cy+d*.5*split,z0),(x,cy,base+rise-.11),
                          (x-side*.1,cy-d*.5*split,z0),(x-side*.1,cy+d*.5*split,z0),(x-side*.1,cy,base+rise-.11)],
                         [(0,1,2),(3,5,4),(0,3,4,1),(1,4,5,2),(2,5,3,0)],"#cbbd93")
                self.stroke([(x+side*.025,cy-d*.5*split,z0),(x+side*.025,cy,base+rise-.1),(x+side*.025,cy+d*.5*split,z0)],.065,wood)
                self.rod((x+side*.04,cy,z0),(x+side*.04,cy,base+rise-.12),.044,wood)

    def craft_gate(self,wood):
        dark="#493f35";stone="#aca48e"
        for x in (-1.55,1.55):
            for y in (-.46,.46):
                self.craft_stone((x,y,.15),(.50,.50,.30),stone)
                self.box((x,y,1.72),(.24,.24,3.03),wood)
                for z in (.38,2.70):self.box((x,y,z),(.29,.29,.13),dark)
                direction=-1 if x>0 else 1
                self.rod((x,y,2.42),(x+direction*.48,y,2.95),.077,wood,12)
                for level in range(3):self.box((x,y,3.12+level*.105),(.41+level*.16,.35+level*.055,.087),wood)
            self.box((x,0,3.08),(.23,1.41,.23),wood)
        for y in (-.46,.46):
            self.box((0,y,3.0),(3.68,.24,.25),wood)
            self.box((0,y,2.77),(3.1,.13,.10),dark)
            for x in (-1.17,-.78,-.39,0,.39,.78,1.17):self.box((x,y,2.87),(.048,.075,.16),wood)
        self.box((0,-.62,3.035),(1.05,.08,.28),dark)
        for z in (2.865,3.205):self.box((0,-.675,z),(1.16,.06,.035),wood)
        self.craft_roof((0,0,3.34),4.35,2.20,.94,25,7)
        for x in (-1.42,1.42):self.lantern(x,-.79,2.53,.63)

    def craft_window(self,x,y,z,w,h):
        wood="#665033";gold="#b78845"
        # Nested frames, inset amber panes and 3D meander lattices.
        self.box((x,y+.11,z),(w+.08,.065,h+.08),"#302e20")
        for ix in range(3):
            for iz in range(4):
                self.box((x-w*.34+ix*w*.34,y+.055,z-h*.36+iz*h*.24),(w*.30,.025,h*.21),self.rng.choice(["#977237","#bf964f","#6a512b"]))
        for inset,thickness,offset in ((0,.057,0),(.073,.025,-.044)):
            for dx in (-w*.5+inset,w*.5-inset):self.box((x+dx,y+offset,z),(thickness,.12,h-2*inset+.1),wood)
            for dz in (-h*.5+inset,h*.5-inset):self.box((x,y+offset,z+dz),(w-2*inset+.1,.12,thickness),wood)
        for ix in range(4):self.box((x-w*.42+ix*w*.28,y-.05,z),(.022,.065,h*.83),gold)
        for iz in range(5):self.box((x,y-.05,z-h*.41+iz*h*.205),(w*.86,.065,.022),wood)
        for ix in range(3):
            for iz in range(4):
                xx=x-w*.28+ix*w*.28;zz=z-h*.3075+iz*h*.205
                # Offset double corner key pattern remains open around pane centers.
                for sign in (-1,1):
                    self.box((xx+sign*w*.055,y-.078,zz+sign*h*.036),(w*.11,.034,.015),wood)
                    self.box((xx+sign*w*.11,y-.078,zz),(.015,.034,h*.075),wood)
        self.box((x,y-.085,z-h*.5-.055),(w+.21,.24,.087),"#5d4830")
        for dx in (-w*.40,w*.40):self.box((x+dx,y-.09,z-h*.5-.13),(.083,.16,.13),wood)

    def craft_bracket(self,x,y,z,side=1):
        wood="#5d4830"
        for level in range(3):
            self.box((x,y-.055*level,z+.085*level),(.25+.14*level,.24+.11*level,.077),wood)
            if level<2:
                for dx in (-.12-.04*level,.12+.04*level):self.box((x+dx,y-.08,z+.065+.085*level),(.070,.15,.085),"#85643b")
        self.stroke([(x,y+.20,z-.45),(x,y+.03,z-.22),(x,y-.19,z+.03)],.065,wood)

    def craft_table(self,x,y,z,scale=1):
        self.box((x,y,z+.62*scale),(.64*scale,.55*scale,.065*scale),"#82603b")
        for dx in (-.24,.24):
            for dy in (-.20,.20):self.box((x+dx*scale,y+dy*scale,z+.30*scale),(.055*scale,.055*scale,.6*scale),"#614a30")
        self.box((x,y,z+.43*scale),(.51*scale,.47*scale,.045*scale),"#614a30")
        self.pot(x-.1*scale,y,z+.66*scale,.062*scale,.08*scale)
        self.pot(x+.12*scale,y,z+.66*scale,.038*scale,.047*scale)

    def craft_inn(self):
        plaster="#cbbd93";wood="#5d4830";stone="#b3a786"
        def upper_finish(previous):
            # The reference has a set-back, narrower upper storey. Its extra
            # headroom comes from facade proportions, not stretching the roof.
            for key,(verts,_) in self.parts.items():
                for i in range(previous.get(key,0),len(verts)):
                    x,y,z=verts[i];verts[i]=(x*.87,y+.62,2.93+(z-2.93)*1.22)
        def upper(action):
            previous={key:len(value[0]) for key,value in self.parts.items()}
            action();upper_finish(previous)
        # Genuine wall thickness and hollow interior: doors and window recesses
        # reveal floorboards, rafters and furniture instead of a solid facade cube.
        self.box((0,1.92,1.49),(6,.18,2.92),plaster)
        upper(lambda:self.box((0,1.92,4.065),(6,.18,2.26),plaster))
        for x in (-2.94,2.94):
            self.box((x,0,1.49),(.18,3.85,2.92),plaster)
            upper(lambda:self.box((x,0,4.065),(.18,3.85,2.26),plaster))
        for z in (.10,2.93):
            for i in range(30):
                if z>2:upper(lambda:self.box((-2.9+i*.20,0,z),(.192,3.85,.11),"#82603b"))
                else:self.box((-2.9+i*.20,0,z),(.192,3.85,.11),"#82603b")
        for z,h in ((.30,.45),(2.53,.60),(3.18,.46),(5.02,.36)):
            if z>3:upper(lambda:self.box((0,-1.94,z),(5.93,.17,h),plaster))
            else:self.box((0,-1.94,z),(5.93,.17,h),wood if z==2.53 else plaster)
        # The exposed entrance header is timber, not a bright plaster flap
        # between the two shop awnings. All framing stays within existing bounds.
        for z in (2.26,2.80):self.box((0,-2.066,z),(1.73,.073,.058),"#665033")
        for x in (-.835,.835):self.box((x,-2.066,2.53),(.060,.073,.59),"#665033")
        for x in (-2.90,-1.02,1.02,2.90):
            self.box((x,-1.97,1.50),(.17,.24,2.96),wood)
            upper(lambda:self.box((x,-1.97,4.05),(.17,.24,2.22),wood))
            self.craft_stone((x,-1.98,.25),(.32,.34,.46),stone)
        for x in (-2.35,-1.48,1.48,2.35):self.window(x,-2.065,1.39,.72,1.57)
        for x in (-2.34,-1.41,-.47,.47,1.41,2.34):upper(lambda:self.window(x,-2.065,4.07,.79,1.55))
        for x in (-1.91,1.91):self.box((x,-1.98,1.41),(.105,.15,1.94),wood)
        for x in (-.85,.85):
            self.box((x,-1.97,1.36),(.13,.20,2.35),wood)
            # Open outer door leaves with inset panel rails and brass ring handles.
            yaw=-.95 if x<0 else .95
            self.oriented(lambda:self.craft_window(0,0,1.24,.60,1.95),(x,-2.20,.02),yaw)
        self.box((0,-1.78,2.55),(1.75,.22,.18),wood)
        self.craft_stone((0,-2.55,.13),(1.95,1.22,.24),stone)
        self.craft_stone((0,-2.91,.07),(2.13,.39,.12),stone)
        for x in (-1.9,0,1.8):
            self.craft_table(x,-.85,.12,.94)
            upper(lambda:self.craft_table(x,-.85,3.0,.94))
        for x in (-2.2,2.0):
            self.bench(x,-.22,.13,.66)
            upper(lambda:self.bench(x,-.22,3.0,.66))
        for x in (-2.65,-1.35,0,1.35,2.65):
            upper(lambda:(self.box((x,-.10,4.96),(.12,4.21,.15),wood),self.craft_bracket(x,-2.18,4.87)))
        # Projecting balcony with separate plank ends, carved balusters and returns.
        balcony_start={key:len(value[0]) for key,value in self.parts.items()}
        for i in range(35):self.box((-3.06+i*.18,-2.22,3.02),(.173,.70,.115),"#82603b")
        self.box((0,-2.55,3.07),(6.30,.13,.19),wood)
        for z in (3.25,3.78):self.box((0,-2.59,z),(6.27,.095,.08),wood)
        for i in range(33):
            x=-2.93+i*.183
            self.box((x,-2.59,3.48),(.033,.055,.46),"#85643b")
            self.box((x,-2.61,3.48),(.063,.047,.067),wood)
        for x in (-3.04,-1.00,1.00,3.04):
            self.box((x,-2.53,4.06),(.105,.135,2.08),wood)
            self.craft_bracket(x,-2.18,4.94)
            self.rod((x,-2.53,4.88),(x,-2.24,5.06),.032,wood,8)
            self.stroke([(x,-1.98,2.36),(x,-2.16,2.65),(x,-2.48,2.92)],.074,wood)
            for dz in (3.21,3.83):self.box((x,-2.55,dz),(.17,.17,.085),"#85643b")
        for x in (-3.07,3.07):
            for z in (3.26,3.77):self.box((x,-2.31,z),(.075,.51,.070),wood)
            for y in (-2.49,-2.34,-2.19):self.box((x,y,3.48),(.045,.033,.46),"#85643b")
        upper_finish(balcony_start)
        self.craft_roof((0,.50,5.65),6.55,4.80,1.18,34,11,True)
        # Distinct projecting shop awnings remain visible below the shallow
        # balcony. The entry between them has a hanging sign and exposed lintel.
        for x,base,depth in ((-1.95,2.38,2.45),(1.95,2.31,2.33)):
            self.craft_roof((x,-2.12,base),2.57,depth,.56,15,5)
            for dx in (-1.02,1.02):self.craft_bracket(x+dx,-2.88,base-.16)
        for x in (-.60,.60):self.rod((x,-2.58,3.01),(x,-2.96,2.82),.032,wood,8)
        # Framed side windows, projecting side canopy and hanging sign frame.
        for side in (-1,1):
            for y in (-1.08,.19,1.28):
                for z in (1.38,3.99):
                    action=lambda:self.oriented(lambda:self.window(0,0,z,.70,1.38),(side*3.035,y,0),side*math.pi/2)
                    if z>3:upper(action)
                    else:action()
            for y in (-1.72,-.44,.84,1.72):
                self.box((side*3.06,y,1.50),(.12,.13,2.96),wood)
                upper(lambda:self.box((side*3.06,y,4.05),(.12,.13,2.22),wood))
            for z in (.33,2.68,4.96):
                if z>3:upper(lambda:self.box((side*3.06,0,z),(.12,3.86,.14),wood))
                else:self.box((side*3.06,0,z),(.12,3.86,.14),wood)
        self.oriented(lambda:self.craft_roof((0,0,2.48),2.60,1.30,.38,15,5),(3.1,.10,0),math.pi/2)
        upper(lambda:self.box((3.22,-1.65,3.80),(.10,.62,1.52),"#b3a786"))
        for y in (-1.98,-1.32):upper(lambda:self.box((3.30,y,3.80),(.1,.045,1.62),wood))
        for z in (3.0,4.60):upper(lambda:self.box((3.30,-1.65,z),(.1,.75,.07),wood))
        for x,z in ((-2.90,4.61),(-.97,4.63),(.98,4.63),(2.92,4.61),(-2.95,2.00),(-.96,2.06),(.97,2.06),(2.96,2.0)):
            if z>3:upper(lambda:self.lantern(x,-2.51,z,.79))
            else:self.lantern(x,-3.06,z,.79)
        self.box((0,-3.00,2.62),(1.45,.11,.35),"#3d392b")
        for z in (2.43,2.81):self.box((0,-3.07,z),(1.57,.06,.045),"#b78845")
        for x in (-.78,.78):self.box((x,-3.07,2.62),(.045,.06,.41),"#b78845")
        for x in (-2.07,2.12):
            self.bench(x,-2.94,.23,1.13)
            for dx in (-.3,.20):self.pot(x+dx,-2.91,.67,.075,.13)
            self.craft_table(x,-2.65,.14,.40)
        for x in (-3.13,-1.1,1.11,3.18):self.plant(x,-3.0,.14,.86)
        for x in (-2.34,1.61):upper(lambda:self.plant(x,-2.27,3.09,.65))
        for i in range(12):self.craft_stone((-2.81+i*.51,-2.05,.10),(.49,.31,.20),self.rng.choice(["#a8a38d","#b6ae96","#918b72"]))
        # Plaster erosion is grouped near the footing and joints.
        for side in (-1,1):
            for i in range(32):
                y=self.rng.uniform(-1.70,1.65);z=self.rng.choice([.40,.56,2.79,4.74])+self.rng.uniform(-.12,.12)
                size=(.018,self.rng.uniform(.06,.15),self.rng.uniform(.035,.12))
                if z>3:upper(lambda:self.box((side*3.04,y,z),size,"#b4a480"))
                else:self.box((side*3.04,y,z),size,"#b4a480")

    def craft_quay(self):
        colors=["#aaa791","#bfb8a0","#c7bea3","#989b87","#b1ae94"]
        self.box((0,0,.40),(13.92,5.92,.79),"#7f8574")
        for row in range(13):
            y=-2.73+row*.452;x=-6.94
            while x<6.86:
                length=min(self.rng.uniform(.49,.91),6.98-x)
                if length<.035:break
                self.craft_stone((x+length/2,y,.849+self.rng.uniform(-.012,.012)),(length-.022,.427,.17),self.rng.choice(colors))
                x+=length
        for side in (-1,1):
            for row in range(3):
                x=-6.97
                while x<6.86:
                    length=min(self.rng.uniform(.48,.77),6.98-x)
                    if length<.025:break
                    self.craft_stone((x+length/2,side*2.98,.13+row*.263),(length-.025,.28,.25),self.rng.choice(colors))
                    x+=length
            for row in range(3):
                for j in range(12):self.craft_stone((side*6.98,-2.75+j*.5,.13+row*.263),(.26,.476,.25),self.rng.choice(colors))
        for j in range(5):
            h=.82-j*.157;cy=-3.13-j*.325
            for i in range(4):self.craft_stone((1.59+i*.418,cy,h*.5),(.397,.354,h),self.rng.choice(colors))
        for j in range(12):
            for row in range(2):self.craft_stone((-6.59+j*.59,2.75,1.04+row*.29),(.57,.36,.275),self.rng.choice(colors))
        for j in range(7):
            for row in range(2):self.craft_stone((6.72,-.18+j*.49,1.04+row*.29),(.36,.47,.275),self.rng.choice(colors))
        positions=[-6.1,-4.5,-2.6,-.6,3.8,5.5,6.7]
        for x in positions:
            self.rod((x,-3.10,.13),(x,-3.1,1.43),.075,"#706044",16)
            for z in (1.13,1.17,1.21,1.25):
                points=[(x+.086*math.cos(i*math.pi/8),-3.1+.086*math.sin(i*math.pi/8),z+.033*i/16) for i in range(17)]
                self.stroke(points,.014,"#b7a57d",6)
        for x1,x2 in zip(positions,positions[1:]):
            if x2-x1<2.5:self.rope((x1,-3.1,1.25),(x2,-3.1,1.25),.25)
        for i in range(80):
            x=self.rng.uniform(-6.8,6.8);z=self.rng.uniform(.12,.62)
            self.leaf((x,-3.132,z),self.rng.uniform(.035,.075),self.rng.choice(["#7d8850","#6b7949","#99a066"]),self.rng.random()*6.28,1.35,.7)
        for i in range(21):
            x=self.rng.uniform(-6.65,6.65);y=self.rng.choice([-2.84,2.73])+self.rng.uniform(-.08,.08)
            for k in range(3):
                a=k*2.4;self.leaf((x+math.cos(a)*.08,y+math.sin(a)*.08,.99),.095,"#6b7949",a,.65,.24)

    def craft_foliage(self,c,length,color,yaw,tilt,width=.78,petal=False):
        # A broad, slightly folded polygon leaf has a body and a central ridge.
        # Flat facet normals echo the source's solid leafy clumps; ellipsoidal
        # smooth shading on a very narrow leaflet would instead resemble pasta.
        n=6 if petal else 8;cs,sn=math.cos(yaw),math.sin(yaw);ct,st=math.cos(tilt),math.sin(tilt)
        local=[(0,0,length*(.052 if petal else .18)),(0,0,-length*(.023 if petal else .08))]
        for i in range(n):
            a=i*2*math.pi/n
            local.append((math.sin(a)*length*width,math.cos(a)*length,length*.015*math.cos(a)))
        verts=[]
        for x,y,z in local:
            yy,zz=y*ct-z*st,y*st+z*ct
            verts.append((c[0]+x*cs-yy*sn,c[1]+x*sn+yy*cs,c[2]+zz))
        self.add(verts,[(0,2+i,2+(i+1)%n) for i in range(n)]+[(1,2+(i+1)%n,2+i) for i in range(n)],color)

    def craft_tree(self,blossom=False):
        wood="#786044";palette=["#78905a","#9ca361","#5e7856","#abb16f","#526e50"]
        petals=["#d58d88","#e6ac9e","#f0c7b3","#cb8585","#ebafa6"]
        self.stroke([(0,0,0),(.10,.025,.73),(-.035,.07,1.53),(.13,.015,2.27)],.125,wood,16)
        for c in ((.10,.025,.73),(-.035,.07,1.53),(.13,.015,2.27)):
            self.ball(c,(.127,.127,.127),wood)
        for i in range(7):
            a=i*2.4
            self.rod((.04,0,.38),(.32*math.cos(a),.32*math.sin(a),.025),.070,wood,12,.023)
        for i in range(16):
            # Irregular branch tiers and unequal clusters leave occasional
            # windows through the crown instead of a regular hydrangea ball.
            a=i*2.399
            h=1.91+1.18*((i*.618034)%1)+self.rng.uniform(-.08,.08)
            r=self.rng.uniform(.63,1.12)-max(0,h-2.65)*.18
            start=(.035,0,.9+h*.27);mid=(math.cos(a)*r*.57,math.sin(a)*r*.57,h-.29);end=(math.cos(a)*r,math.sin(a)*r,h)
            self.rod(start,mid,.060,wood,12,.037);self.rod(mid,end,.037,wood,10,.018)
            self.ball(mid,(.039,.039,.039),wood)
            for j in range(3+(i*3)%5):
                theta=a+j*2.40;rr=self.rng.uniform(.15,.44)
                tip=(end[0]+math.cos(theta)*rr,end[1]+math.sin(theta)*rr,end[2]+self.rng.uniform(-.20,.28))
                self.rod(end,tip,.018,wood,8,.006)
                count=23 if blossom else (30,48,66)[j%3]
                spread=self.rng.uniform(.19,.36) if blossom else self.rng.uniform(.23,.43)
                for k in range(count):
                    angle=theta+k*2.399
                    # Ellipsoidal volume distribution rather than a thin spiral:
                    # many rotated small leaves retain detail and fill silhouette.
                    radial=self.rng.random()**(1/3)*spread
                    vertical=self.rng.uniform(-1,1);horizontal=math.sqrt(1-vertical*vertical)
                    c=(end[0]+(tip[0]-end[0])*.55+math.cos(angle)*radial*horizontal,
                       end[1]+(tip[1]-end[1])*.55+math.sin(angle)*radial*horizontal,
                       end[2]+(tip[2]-end[2])*.55+vertical*radial*.88)
                    if blossom:
                        radius=self.rng.uniform(.037,.059)
                        if k%4==0:self.rod((c[0],c[1],c[2]-.045),c,.004,wood,6)
                        for petal in range(5):
                            aa=angle+petal*math.pi*2/5
                            self.craft_foliage((c[0]+math.cos(aa)*radius*.61,c[1]+math.sin(aa)*radius*.61,c[2]),radius,self.rng.choice(petals),aa+math.pi/2,.12+(k%3)*.16,.72,True)
                        # Eight-faced pollen center saves topology for actual
                        # additional flowers instead of invisible sphere rings.
                        cx,cy,cz=c;r0=.008
                        self.add([(cx-r0,cy,cz),(cx+r0,cy,cz),(cx,cy-r0,cz),(cx,cy+r0,cz),(cx,cy,cz+r0),(cx,cy,cz-r0)],
                                 [(0,2,4),(2,1,4),(1,3,4),(3,0,4),(2,0,5),(1,2,5),(3,1,5),(0,3,5)],"#b78845")
                    else:
                        self.craft_foliage(c,self.rng.uniform(.091,.138),self.rng.choice(palette),angle,self.rng.uniform(-.65,.90),.84)
                        if k%8==0:self.rod((c[0],c[1],c[2]-.028),c,.0035,"#9ca361",5)
        for i in range(6):self.craft_stone((self.rng.uniform(-.28,.28),self.rng.uniform(-.28,.28),.065),(.28,.22,.14),"#8c9078")

    def craft_basket(self,x=0,y=0,z=0,r=.44,height=.56):
        """Open round woven basket; distinct from the solid shipping crate."""
        n,rows,stakes=(16,6,12) if self.detail==1 else ((24,8,18) if self.detail==2 else (36,11,26))
        tan="#b79b67";dark="#887047";edge="#a18a58"
        def radius(t):return r*(.69+.31*math.sin(t*math.pi/2))
        # Thin sealed wall backing and bottom. The mouth is genuinely open.
        verts=[(x+math.cos(i*math.pi*2/n)*(radius(t)-inset),y+math.sin(i*math.pi*2/n)*(radius(t)-inset),z+t*height)
               for inset in (0,.022) for t in (0,1) for i in range(n)]
        faces=[]
        for i in range(n):
            j=(i+1)%n
            faces.extend([(i,j,n+j,n+i),(2*n+i,3*n+i,3*n+j,2*n+j),
                          (n+i,n+j,3*n+j,3*n+i),(i,2*n+i,2*n+j,j)])
        faces.extend([tuple(reversed(range(n))),tuple(range(2*n,3*n))])
        self.add(verts,faces,dark,"smooth" if self.detail>=2 else "flat")
        for row in range(rows+1):
            t=(row+.10)/(rows+1)
            self.stroke([(x+math.cos(i*math.pi*2/n)*(radius(t)+.008),y+math.sin(i*math.pi*2/n)*(radius(t)+.008),z+t*height) for i in range(n+1)],r*.018,tan,6)
        for i in range(stakes):
            a=i*math.pi*2/stakes
            self.stroke([(x+math.cos(a)*(radius(j/rows)+(.010 if (i+j)%2 else .002)),
                          y+math.sin(a)*(radius(j/rows)+(.010 if (i+j)%2 else .002)),z+j/rows*height)
                         for j in range(rows+1)],r*.023,edge,6)
        self.stroke([(x+math.cos(i*math.pi*2/n)*r,y+math.sin(i*math.pi*2/n)*r,z+height) for i in range(n+1)],r*.043,tan,8)
        for side in (-1,1):
            self.stroke([(x+math.cos(i*math.pi/10)*r*.29,y+side*r*.90,z+height+math.sin(i*math.pi/10)*height*.26) for i in range(11)],r*.032,edge,6)

    def craft_bamboo_rack(self):
        bamboo="#8c8050";node="#b7a57d";cord="#c0ab78"
        for x in (-1.08,1.08):
            for y in (-.24,.24):
                self.rod((x,y,0),(x,y,2.37),.036,bamboo,10)
                for z in (.22,.56,.9,1.24,1.58,1.92,2.26):self.rod((x,y,z),(x,y,z+.025),.045,node,10)
            self.rod((x,-.33,2.18),(x,.33,2.18),.031,bamboo,10)
        for z in (.23,1.02,2.16):self.rod((-1.20,.22,z),(1.20,.22,z),.032,bamboo,10)
        self.rod((-1.07,.25,.28),(.95,.25,2.13),.022,bamboo,8)
        for x in (-1.08,1.08):
            for z in (1.02,2.16):
                for i in range(3):self.rope((x-.055,.19,z-.045+i*.026),(x+.055,.19,z-.045+i*.026),.015,cord,.011)
        # Rolled bamboo blind, composed of longitudinal split-bamboo slats and
        # circular bindings, with a short woven panel hanging below it.
        n=16 if self.detail==1 else (24 if self.detail==2 else 32)
        for i in range(n):
            a=i*math.pi*2/n
            self.rod((-.85,math.cos(a)*.20,1.73+math.sin(a)*.20),(.85,math.cos(a)*.20,1.73+math.sin(a)*.20),.017,node,6)
        for x in (-.83,-.45,0,.45,.83):
            self.stroke([(x,math.cos(i*math.pi*2/n)*.22,1.73+math.sin(i*math.pi*2/n)*.22) for i in range(n+1)],.012,cord,6)
        rows=12 if self.detail==1 else (18 if self.detail==2 else 24)
        for i in range(rows):
            z=.71+i*.88/(rows-1)
            self.rod((-.82,-.19,z),(.82,-.19,z),.012,bamboo,6)
        for x in (-.77,-.38,0,.38,.77):self.rod((x,-.207,.70),(x,-.207,1.61),.009,cord,6)
        for x in (-.64,.64):self.rope((x,.22,2.14),(x,-.05,1.90),.04,cord,.013)
        self.craft_basket(-.65,-.03,.04,.27,.33)

    def craft_tea(self):
        wood="#7e5d37"
        for x in (-1.5,1.5):
            for y in (-.78,.78):
                self.rod((x,y,0),(x,y,2.90),.047,wood,12)
                self.rod((x,y,2.78),(x,y,2.86),.061,"#c0ab78",12)
        for x in (-1.5,1.5):self.rod((x,-1.0,2.82),(x,1.0,2.82),.035,wood,10)
        for y in (-.78,.78):self.rod((-1.57,y,2.79),(1.57,y,2.79),.032,wood,10)
        nx,ny=20,12
        def canopy(x,y):return 2.80+.25*(1-abs(y))-.17*(1-(x/1.76)**2)+.017*math.cos(x*15)*abs(y)**3
        verts=[(x,y,canopy(x,y)+off) for off in (0,-.022) for j in range(ny+1) for i in range(nx+1)
               for x,y in [(-1.76+3.52*i/nx,-1.03+2.06*j/ny)]]
        count=(nx+1)*(ny+1);faces=[]
        for j in range(ny):
            for i in range(nx):
                a=j*(nx+1)+i;faces.extend([(a,a+1,a+nx+2,a+nx+1),(a+count,a+count+nx+1,a+count+nx+2,a+count+1)])
        edge=list(range(nx+1))+[j*(nx+1)+nx for j in range(1,ny+1)]+[ny*(nx+1)+i for i in range(nx-1,-1,-1)]+[j*(nx+1) for j in range(ny-1,0,-1)]
        faces.extend((a,b,b+count,a+count) for a,b in zip(edge,edge[1:]+edge[:1]));self.add(verts,faces,"#a3ac89","smooth")
        for y in (-1.03,1.03):
            self.stroke([(x,y,canopy(x,y)) for x in [-1.76+3.52*i/32 for i in range(33)]],.013,"#c0ab78",6)
            for i in range(24):
                x=-1.70+3.4*i/23;self.box((x,y,canopy(x,y)-.07),(.142,.025,.13+.025*math.cos(i*1.6)),"#a3ac89")
        for x in (-1.75,1.75):self.rope((x,-1.0,canopy(x,-1)),(x*.86,-.78,2.53),.028,"#c0ab78",.012)
        for i in range(16):self.box((-1.435+i*.191,-.49,.49),(.18,.09,.79),self.rng.choice(["#7e5d37","#9a7344","#866139"]))
        self.box((0,-.12,.92),(3.11,.84,.085),"#967346")
        for x in (-1.40,1.4):self.box((x,-.1,.45),(.11,.66,.9),"#634a2d")
        for z in (.12,.81):self.box((0,-.551,z),(3.03,.07,.066),"#634a2d")
        for x in (-1.19,-.59,.02,.62,1.21):
            self.pot(x,.01,.98,.13,.22)
            self.rod((x,.01,1.22),(x,.01,1.25),.109,"#bba77c",16)
            self.ball((x,.01,1.275),(.029,.029,.033),"#887047")
        for x in (-.39,-.17,.06,.29):self.pot(x,-.36,.98,.053,.071)
        for x in (-1.0,1.02):
            self.bench(x,-1.02,0,.49)
            for y in (-1.15,-.89):self.rod((x-.20,y,.18),(x+.20,y,.18),.021,"#614a30",8)
        for x in (-1.06,0,1.06):self.box((x,.49,.50),(.91,.24,.84),"#967346")
        self.plant(-1.68,-.65,0,.88);self.pot(1.59,-.72,0,.29,.57)
        self.pot(1.34,.37,.98,.19,.29)

    def craft_boat(self,covered=True):
        length=5.1 if covered else 3.45;width=1.40 if covered else 1.28
        colors=["#7e613e","#98774e","#695338","#a38458"]
        stations=49;bands=6
        def hull(t,side,v,inset=0):
            shape=max(.04,math.sin(math.pi*t)**.59);bow=.26*abs(2*t-1)**3
            return ((t-.5)*length,side*(width*.5*shape*(.47+.53*v)-inset),.09+v*.43+bow)
        for side in (-1,1):
            for band in range(bands):
                vs=[]
                for inset in (0,.040):
                    for i in range(stations):
                        for v in (band/bands+.003,(band+1)/bands-.003):vs.append(hull(i/(stations-1),side,v,inset))
                stride=stations*2;faces=[]
                for i in range(stations-1):
                    a=2*i
                    faces.extend([(a,a+2,a+3,a+1),(stride+a,stride+a+1,stride+a+3,stride+a+2),
                                  (a,stride+a,stride+a+2,a+2),(a+1,a+3,stride+a+3,stride+a+1)])
                faces.extend([(0,1,stride+1,stride),(stride-2,2*stride-2,2*stride-1,stride-1)])
                self.add(vs,faces,colors[band%4],"smooth")
            self.stroke([hull(i/(stations-1),side,1.04) for i in range(stations)],.037,"#b19568",12)
            self.stroke([hull(i/(stations-1),side,.08) for i in range(stations)],.025,"#5d4931",8)
            for i in range(5,45,3):
                self.stroke([hull(i/48,side,v,.055) for v in (.13,.42,.70,1.01)],.024,"#ad8857",8)
                for band in (1,4):
                    pt=hull(i/48,side,band/6)
                    self.ball((pt[0],pt[1]+side*.008,pt[2]),(.009,.006,.009),"#493e28")
        # Closed shaped keel floor follows the same smooth longitudinal stations.
        for i in range(stations-1):
            vs=[tuple(v+(off if k==2 else 0) for k,v in enumerate(hull(t,side,.04))) for off in (0,-.036)
                for t in (i/(stations-1),(i+1)/(stations-1)) for side in (-1,1)]
            self.add(vs,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],"#5d4931","smooth")
        for i in range(25):
            t=.12+i*.76/24;shape=math.sin(math.pi*t)**.59
            self.box(((t-.5)*length,0,.30),(.125,width*shape*.71,.068),colors[i%4])
        for x in (-length*.31,.17,length*.31):self.box((x,0,.465),(.22,width*.82,.08),"#ad8857")
        if covered:
            start,end=-1.50,.60;radius=width*.485;h=.91;steps=32
            # Opaque sealed bamboo backing with two true interlaced thin-strip layers.
            for j in range(steps):
                a=j*math.pi/steps;b=(j+1)*math.pi/steps
                vs=[(x,math.cos(t)*r,.54+math.sin(t)*hh) for r,hh in ((radius,h),(radius-.018,h-.018)) for x in (start,end) for t in (a,b)]
                self.add(vs,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],"#91815e","smooth")
            for i in range(64):
                x=start+(end-start)*i/63
                for j in range(steps):
                    a=j*math.pi/steps;b=(j+1)*math.pi/steps
                    # Flat radial splints have enough thickness to catch tiny highlights.
                    vs=[(x+dx,math.cos(t)*(radius+off),.54+math.sin(t)*(h+off)) for off in (.011,.019) for t in (a,b) for dx in (-.009,.009)]
                    self.add(vs,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],"#b09b6c")
            for j in range(45):
                a=(j+.25)*math.pi/45
                for i in range(32):
                    x0=start+(end-start)*i/32;x1=start+(end-start)*(i+1)/32
                    off=.024 if (i+j)%2 else .009
                    vs=[(x,math.cos(t)*(radius+off+depth),.54+math.sin(t)*(h+off+depth)) for depth in (0,.006) for x in (x0,x1) for t in (a-.006,a+.006)]
                    self.add(vs,[(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)],"#ab9975")
            for x in (start-.012,start+.065,-.81,-.10,end-.065,end+.012):
                self.stroke([(x,math.cos(j*math.pi/steps)*(radius+.033),.54+math.sin(j*math.pi/steps)*(h+.033)) for j in range(steps+1)],.019,"#8a794e",8)
            self.rod((-2,-.46,.45),(-2,-.46,1.77),.025,"#675339",10)
            self.rod((-2,-.46,1.77),(-1.7,-.46,1.77),.023,"#675339",10)
            self.lantern(-1.7,-.46,1.50,.40)
            self.pot(.98,-.17,.35,.13,.19)
        else:
            self.pot(.78,0,.35,.19,.23)
            for i in range(13):self.rope((-.49+i*.055,-.37,.47),(-.11+i*.055,.32,.46),.065,"#c0ab78",.007)
            for i in range(10):self.rope((-.49,-.36+i*.070,.47),(.54,-.36+i*.070,.45),.06,"#c0ab78",.006)
        # Coil on the bow deck and a broad shaped oar blade.
        coil=[]
        for i in range(129):
            a=i*math.pi/16;r=.15-.0007*i
            coil.append((length*.32+math.cos(a)*r,-.20+math.sin(a)*r,.525))
        self.stroke(coil,.009,"#c0ab78",6)
        self.rod((length*.23,.33,.53),(length*.60,.92,.065),.025,"#715536",10)
        self.box((length*.58,.885,.065),(.38,.16,.037),"#967445",-.57)

    def finish(self):
        import bpy
        for index,((color,treatment),(verts,faces)) in enumerate(self.parts.items()):
            if color not in self.materials:self.materials[color]=make_material("river palette "+color,color)
            obj=mesh("batched craft "+str(index),verts,faces,self.materials[color])
            if treatment == "smooth":
                for polygon in obj.data.polygons:polygon.use_smooth=True
            if treatment == "bevel":
                # Only solid boxes. Never bevel water, thin leaflets, tile shells or every edge globally.
                bpy.context.view_layer.objects.active=obj
                modifier=obj.modifiers.new("small rounded timber and masonry edges","BEVEL")
                modifier.width=.010 if self.detail == 3 else .012
                modifier.segments=2 if self.detail == 3 else 1;modifier.limit_method="ANGLE"
                bpy.ops.object.modifier_apply(modifier=modifier.name)


def river_component(template, detail=1):
    g=RiverGeometry(detail)
    {"riverside_inn":g.inn,"stone_quay":g.quay,"tea_stall":g.tea,
     "broadleaf_tree":lambda:g.tree(False),"blossom_tree":lambda:g.tree(True),
     "woven_boat":lambda:g.boat(True),"rowing_boat":lambda:g.boat(False),
     "timber_pier":g.pier,"potted_garden":g.garden,"bamboo_rack":g.craft_bamboo_rack,
     "woven_basket":g.craft_basket}[template]()
    g.finish()


def build(recipe, output):
    import bpy
    from mathutils import Vector

    if bpy.app.version < (4, 2, 0):
        raise RuntimeError("Blender 4.2 or newer is required for the bounded worker")
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("component.glb", "component.blend", "metadata.json")):
        raise ValueError("component output already exists; build into a fresh staging directory")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    materials = (make_material("warm timber", recipe["primary_color"]),
                 make_material("glazed roof tile", recipe["secondary_color"], .62),
                 make_material("dark carved edges", "#493f35"),
                 make_material("stone footing", "#aca48e"),
                 make_material("iron fasteners", "#4c5253", .55, .6))
    if recipe["template"] == "open_gate":
        open_gate(recipe, materials)
    elif recipe["template"] == "tiled_roof":
        if recipe["detail"] == 3:
            g=RiverGeometry(3);g.craft_roof((0,0,.12),4.35,2.4,1.08,25,7);g.finish()
        else:roof(4.35, 2.4, .12, 1.08, materials[1], materials[2], recipe["detail"])
    elif recipe["template"] == "railing":
        railing(recipe, materials)
    elif recipe["template"] == "cargo_crate":
        crate(recipe, materials)
    else:
        river_component(recipe["template"], recipe["detail"])
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = recipe["template"]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    points = [vertex.co.copy() for vertex in obj.data.vertices]
    minimum = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    maximum = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    extent = maximum - minimum
    if min(extent) <= 0:
        raise RuntimeError("component has degenerate bounds")
    center = (minimum + maximum) / 2
    for vertex in obj.data.vertices:
        # Blender +Z -> glTF +Y and Blender -Y -> glTF +Z.
        vertex.co = Vector(((vertex.co.x - center.x) / extent.x,
                            (vertex.co.y - center.y) / extent.y,
                            (vertex.co.z - minimum.z) / extent.z))
    obj.data.update()
    obj.data.calc_loop_triangles()
    triangles = len(obj.data.loop_triangles)
    triangle_limit = ((250000 if recipe["template"] == "riverside_inn" else 180000) if recipe["detail"] == 3 else
                      (90000 if recipe["template"] == "riverside_inn" else 45000) if recipe["detail"] == 2 else
                      (60000 if recipe["template"] == "riverside_inn" else 20000))
    if triangles > triangle_limit:
        raise RuntimeError(f"component exceeds {triangle_limit} triangle limit: {triangles}")
    # Keep one object and native editable mesh/material slots in the .blend.
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "component.blend"), check_existing=False)
    bpy.ops.export_scene.gltf(filepath=str(output / "component.glb"), export_format="GLB",
                              use_selection=True, export_yup=True, export_apply=True,
                              export_texcoords=False, export_normals=True, export_materials="EXPORT",
                              export_animations=False, export_skins=False, export_morph=False,
                              export_cameras=False, export_lights=False, export_extras=False,
                              export_draco_mesh_compression_enable=False)
    glb = (output / "component.glb").read_bytes()
    metadata = {"contract": CONTRACT, "recipe": recipe, "blender_version": bpy.app.version_string,
                "triangles": triangles, "vertices": len(obj.data.vertices), "mesh_count": 1,
                "coordinate_system": "right-handed-y-up", "front": "+Z", "origin": "bottom-center",
                "normalized_bounds": [[-.5, 0, -.5], [.5, 1, .5]],
                "natural_dimensions": [extent.x, extent.z, extent.y],
                "glb_sha256": hashlib.sha256(glb).hexdigest(), "glb_bytes": len(glb),
                "worker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "detail_profile": ("showcase-architecture-weave-and-botanical-microgeometry" if recipe["detail"] == 3 else
                                   "refined-curves-and-selective-bevels" if recipe["detail"] == 2 else "lightweight"),
                "smooth_faces":sum(p.use_smooth for p in obj.data.polygons),
                "limitations": ["procedural template, not automatic image-to-mesh reconstruction",
                                "static asset; no rig, collision proxy or texture maps"]}
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"component_built": True, "triangles": triangles, "glb_sha256": metadata["glb_sha256"]}))


def main():
    if "--" not in sys.argv or len(sys.argv[sys.argv.index("--") + 1:]) != 2:
        raise ValueError("expected -- recipe.json output-directory")
    args = sys.argv[sys.argv.index("--") + 1:]
    recipe_path, output = Path(args[0]).resolve(), Path(args[1]).resolve()
    if recipe_path.stat().st_size > 4096:
        raise ValueError("recipe exceeds 4096 bytes")
    recipe = validate_recipe_data(json.loads(recipe_path.read_text(encoding="utf-8")))
    build(recipe, output)


if __name__ == "__main__":
    main()
