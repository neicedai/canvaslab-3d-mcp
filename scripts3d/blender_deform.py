"""Bounded post-template mesh deformation for CanvasLab managed components.

Run only after the fixed component worker has produced output/component.blend.
The recipe supplies at most 16 normalized control handles; no code, paths, URLs,
mesh data or Blender operators are caller controlled.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

MAX_HANDLES = 16
MAX_COMPONENT_OFFSET = .35
MAX_TOTAL_DISPLACEMENT = .45


def _number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} out of bounds")
    return float(value)


def validate_handles(recipe):
    if not isinstance(recipe, dict):
        raise ValueError("recipe must be an object")
    handles = recipe.get("deformation_handles", [])
    if not isinstance(handles, list) or len(handles) > MAX_HANDLES:
        raise ValueError("deformation_handles must contain at most 16 entries")
    clean = []
    for handle in handles:
        if not isinstance(handle, dict) or set(handle) - {"anchor", "offset", "radius", "strength"}:
            raise ValueError("invalid deformation handle fields")
        anchor, offset = handle.get("anchor"), handle.get("offset")
        if not isinstance(anchor, (list, tuple)) or len(anchor) != 3 or not isinstance(offset, (list, tuple)) or len(offset) != 3:
            raise ValueError("deformation anchor and offset must contain three numbers")
        a = (_number(anchor[0], -.5, .5, "anchor x"), _number(anchor[1], 0, 1, "anchor y"),
             _number(anchor[2], -.5, .5, "anchor z"))
        o = tuple(_number(v, -MAX_COMPONENT_OFFSET, MAX_COMPONENT_OFFSET, "offset") for v in offset)
        if sum(v*v for v in o) < 1e-8:
            raise ValueError("deformation offset must be nonzero")
        radius = _number(handle.get("radius", .25), .05, .9, "radius")
        strength = _number(handle.get("strength", 1), .05, 1, "strength")
        clean.append({"anchor": a, "offset": o, "radius": radius, "strength": strength})
    return clean


def deform_point(point, handles):
    """Return a deformed normalized glTF point without mutating inputs."""
    px, py, pz = map(float, point)
    dx = dy = dz = 0.0
    for handle in handles:
        ax, ay, az = handle["anchor"]
        radius = handle["radius"]
        distance = math.sqrt((px-ax)**2 + (py-ay)**2 + (pz-az)**2)
        if distance >= radius:
            continue
        t = 1 - distance/radius
        # Smooth compact radial basis: zero derivative at the boundary.
        weight = t*t*(3-2*t) * handle["strength"]
        ox, oy, oz = handle["offset"]
        dx += ox*weight; dy += oy*weight; dz += oz*weight
    length = math.sqrt(dx*dx + dy*dy + dz*dz)
    if length > MAX_TOTAL_DISPLACEMENT:
        scale = MAX_TOTAL_DISPLACEMENT/length
        dx *= scale; dy *= scale; dz *= scale
    return px+dx, py+dy, pz+dz


def _normalize(points):
    minimum = [min(p[i] for p in points) for i in range(3)]
    maximum = [max(p[i] for p in points) for i in range(3)]
    extent = [maximum[i]-minimum[i] for i in range(3)]
    if min(extent) <= 1e-6:
        raise RuntimeError("deformation collapsed component bounds")
    return [((p[0]-(minimum[0]+maximum[0])/2)/extent[0],
             (p[1]-minimum[1])/extent[1],
             (p[2]-(minimum[2]+maximum[2])/2)/extent[2]) for p in points]


def run(recipe_path, output):
    import bpy
    from mathutils import Vector

    if bpy.app.version < (4, 2, 0):
        raise RuntimeError("Blender 4.2 or newer is required for bounded deformation")
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    handles = validate_handles(recipe)
    if not handles:
        return
    blend = output / "component.blend"
    metadata_path = output / "metadata.json"
    if not blend.is_file() or not metadata_path.is_file():
        raise ValueError("base component output is incomplete")
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise RuntimeError("deformation expects exactly one joined mesh")
    obj = meshes[0]
    obj.data.transform(obj.matrix_world)
    obj.matrix_world.identity()
    # Worker .blend uses normalized Blender axes. Convert to glTF-normalized
    # coordinates, deform, normalize again, then convert back for export_yup.
    deformed = []
    for vertex in obj.data.vertices:
        point = (vertex.co.x, vertex.co.z, -vertex.co.y)
        deformed.append(deform_point(point, handles))
    normalized = _normalize(deformed)
    for vertex, (x, y, z) in zip(obj.data.vertices, normalized):
        vertex.co = Vector((x, -z, y))
    obj.data.update()
    obj.data.calc_loop_triangles()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    glb_path = output / "component.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", use_selection=True,
                              export_yup=True, export_apply=True, export_texcoords=True,
                              export_normals=True, export_materials="EXPORT", export_animations=False,
                              export_skins=False, export_morph=False, export_cameras=False,
                              export_lights=False, export_extras=False,
                              export_draco_mesh_compression_enable=False)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    glb = glb_path.read_bytes()
    metadata["recipe"] = recipe
    metadata["deformation_handles"] = handles
    metadata["deformation_profile"] = "bounded-normalized-rbf-v1"
    metadata["glb_sha256"] = hashlib.sha256(glb).hexdigest()
    metadata["glb_bytes"] = len(glb)
    metadata["worker_sha256"] = metadata.get("worker_sha256")
    metadata["deformer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    metadata["limitations"] = list(metadata.get("limitations", [])) + [
        "local deformation is source-guided but not automatic image-to-mesh reconstruction",
        "deformation does not guarantee collision-free or topologically optimal geometry",
    ]
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def main():
    if "--" not in sys.argv or len(sys.argv[sys.argv.index("--")+1:]) != 2:
        raise ValueError("expected -- recipe.json output-directory")
    recipe_path, output = [Path(x).resolve() for x in sys.argv[sys.argv.index("--")+1:]]
    if recipe_path.stat().st_size > 16384:
        raise ValueError("recipe exceeds deformation byte budget")
    run(recipe_path, output)


if __name__ == "__main__":
    main()
