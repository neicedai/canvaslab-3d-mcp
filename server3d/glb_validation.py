"""Conservative GLB gate for managed Blender components, not a general importer.

Only static, self-contained, opaque triangle meshes are accepted. Geometry is
read from actual binary accessors and transformed through the complete scene;
declared accessor bounds are never trusted as proof of geometry. No third-party
parser, image decoder, script, URI, extension or external process is invoked.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import Any

MAX_GLB_BYTES = 16 * 1024 * 1024
MAX_VERTICES = 200_000
MAX_TRIANGLES = 100_000
NORMALIZATION_TOLERANCE = 1e-4
_IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


@dataclass(frozen=True)
class GlbLimits:
    bytes: int
    vertices: int
    triangles: int
    accessor_elements: int


def glb_limits(profile: str = "standard") -> GlbLimits:
    if profile == "standard":
        return GlbLimits(MAX_GLB_BYTES, MAX_VERTICES, MAX_TRIANGLES, 2_000_000)
    if profile == "showcase":
        return GlbLimits(64 * 1024 * 1024, 1_000_000, 300_000, 8_000_000)
    raise ValueError("Unknown managed GLB budget profile; expected standard or showcase")


def _fail(message: str) -> None:
    raise ValueError(f"Invalid managed GLB: {message}")


def _integer(value: Any, label: str, minimum: int = 0, maximum: int = MAX_GLB_BYTES) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _number(value: Any, label: str, minimum: float = -10000, maximum: float = 10000) -> float:
    if type(value) not in (float, int) or not minimum <= value <= maximum or not math.isfinite(value):
        _fail(f"{label} must be a finite number in [{minimum}, {maximum}]")
    return float(value)


def _vector(value: Any, length: int, label: str, minimum=-10000., maximum=10000.) -> list[float]:
    if type(value) is not list or len(value) != length:
        _fail(f"{label} must contain {length} numbers")
    return [_number(v, label, minimum, maximum) for v in value]


def _object(value: Any, label: str, allowed: set[str]) -> dict:
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        _fail(f"unsupported {label} properties: {', '.join(sorted(unknown))}")
    if "name" in value and (not isinstance(value["name"], str) or len(value["name"]) > 256):
        _fail(f"{label} name must be a short string")
    return value


def _array(value: Any, label: str, maximum: int, minimum: int = 0) -> list:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _fail(f"{label} must contain {minimum} to {maximum} entries")
    return value


def _reference(value: Any, sequence: list, label: str) -> int:
    return _integer(value, label, 0, len(sequence) - 1)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _inspect_json(document: Any) -> None:
    pending = [(document, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 50_000 or depth > 32:
            _fail("JSON nesting or element budget exceeded")
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() == "uri" or key in {"extensions", "extensionsUsed", "extensionsRequired"}:
                    _fail("URIs and extensions are not accepted, even in metadata")
                pending.append((child, depth + 1))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            _fail("non-finite JSON number")
        elif isinstance(value, str) and len(value) > 2048:
            _fail("JSON string budget exceeded")


def _parse_glb(payload: bytes, max_bytes: int = MAX_GLB_BYTES) -> tuple[dict, bytes]:
    if type(payload) is not bytes or not 28 <= len(payload) <= max_bytes:
        _fail(f"payload must be bytes and no larger than {max_bytes // (1024 * 1024)} MiB")
    magic, version, length = struct.unpack_from("<4sII", payload)
    if magic != b"glTF" or version != 2 or length != len(payload):
        _fail("invalid GLB 2 header or total length")
    chunks = []
    offset = 12
    while offset < length:
        if offset + 8 > length:
            _fail("truncated GLB chunk header")
        size, kind = struct.unpack_from("<I4s", payload, offset)
        offset += 8
        if size % 4 or size == 0 or offset + size > length:
            _fail("invalid GLB chunk length or alignment")
        chunks.append((kind, payload[offset:offset + size]))
        if len(chunks) > 2:
            _fail("expected exactly one JSON chunk and one BIN chunk")
        offset += size
    if len(chunks) != 2 or [kind for kind, _ in chunks] != [b"JSON", b"BIN\0"]:
        _fail("expected exactly one JSON chunk followed by one BIN chunk")
    if len(chunks[0][1]) > 2 * 1024 * 1024:
        _fail("JSON chunk exceeds 2 MiB")
    try:
        document = json.loads(chunks[0][1].decode("utf-8"), object_pairs_hook=_unique_json)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        _fail(f"malformed JSON chunk ({type(exc).__name__})")
    _inspect_json(document)
    return document, chunks[1][1]


def _multiply(a: tuple, b: tuple) -> tuple:
    result = tuple(sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
                   for col in range(4) for row in range(4))
    if any(not math.isfinite(n) or abs(n) > 1e6 for n in result):
        _fail("scene world transform exceeds numerical bounds")
    return result


def _node_matrix(node: dict) -> tuple:
    if "matrix" in node:
        if any(k in node for k in ("translation", "rotation", "scale")):
            _fail("node matrix cannot coexist with TRS")
        m = tuple(_vector(node["matrix"], 16, "node matrix"))
        if any(abs(m[i]) > 1e-7 for i in (3, 7, 11)) or abs(m[15] - 1) > 1e-7:
            _fail("node matrix must be affine")
        # glTF matrices must be decomposable into TRS: no shear.
        axes = [[m[c * 4 + r] for r in range(3)] for c in range(3)]
        lengths = [math.sqrt(sum(v*v for v in axis)) for axis in axes]
        if min(lengths) < 1e-6:
            _fail("node matrix must have nonzero scale")
        for i, j in ((0, 1), (0, 2), (1, 2)):
            if abs(sum(a*b for a, b in zip(axes[i], axes[j]))) > lengths[i] * lengths[j] * 1e-5:
                _fail("node matrix shear is unsupported")
        return m
    t = _vector(node.get("translation", [0., 0., 0.]), 3, "node translation")
    s = _vector(node.get("scale", [1., 1., 1.]), 3, "node scale")
    if any(abs(v) < 1e-6 for v in s):
        _fail("node scale cannot collapse geometry")
    x, y, z, w = _vector(node.get("rotation", [0., 0., 0., 1.]), 4, "node rotation", -1., 1.)
    if abs(x*x + y*y + z*z + w*w - 1) > 1e-5:
        _fail("node quaternion must be normalized")
    return ((1-2*(y*y+z*z))*s[0], 2*(x*y+z*w)*s[0], 2*(x*z-y*w)*s[0], 0.,
            2*(x*y-z*w)*s[1], (1-2*(x*x+z*z))*s[1], 2*(y*z+x*w)*s[1], 0.,
            2*(x*z+y*w)*s[2], 2*(y*z-x*w)*s[2], (1-2*(x*x+y*y))*s[2], 0.,
            t[0], t[1], t[2], 1.)


def _check_triangle(a: tuple, b: tuple, c: tuple) -> None:
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    if not any((u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])):
        _fail("degenerate triangles are unsupported")


def validate_glb(payload: bytes, *, profile: str = "standard") -> dict:
    """Validate the v0.3 managed component subset, returning measured metadata.

    Raises ValueError for every rejected input. Limits apply to both stored
    geometry and actual instantiated geometry, so mesh reuse cannot evade them.
    Component world bounds must be [-.5, 0, -.5] to [.5, 1, .5].
    """
    limits = glb_limits(profile)
    document, binary = _parse_glb(payload, limits.bytes)
    _object(document, "document", {"asset", "scene", "scenes", "nodes", "meshes", "materials",
                                   "buffers", "bufferViews", "accessors"})
    asset = _object(document.get("asset"), "asset", {"version", "minVersion", "generator", "copyright"})
    if asset.get("version") != "2.0" or asset.get("minVersion", "2.0") != "2.0":
        _fail("asset must specify glTF 2.0")
    buffers = _array(document.get("buffers"), "buffers", 1, 1)
    buffer = _object(buffers[0], "buffer", {"byteLength", "name"})
    binary_size = _integer(buffer.get("byteLength"), "buffer byteLength", 1, limits.bytes)
    if not binary_size <= len(binary) <= binary_size + 3 or any(binary[binary_size:]):
        _fail("BIN chunk must match buffer byteLength with zero padding only")
    views = _array(document.get("bufferViews"), "bufferViews", 2048, 1)
    for view in views:
        _object(view, "bufferView", {"buffer", "byteOffset", "byteLength", "byteStride", "target", "name"})
        _reference(view.get("buffer"), buffers, "bufferView buffer")
        start = _integer(view.get("byteOffset", 0), "bufferView offset", 0, limits.bytes)
        size = _integer(view.get("byteLength"), "bufferView length", 1, limits.bytes)
        if start + size > binary_size:
            _fail("bufferView range exceeds binary buffer")
        if "byteStride" in view:
            stride = _integer(view["byteStride"], "bufferView stride", 4, 252)
            if stride % 4:
                _fail("bufferView stride must be a multiple of four")
        if "target" in view and view["target"] not in (34962, 34963):
            _fail("unsupported bufferView target")
    accessors = _array(document.get("accessors"), "accessors", 2048, 1)
    layouts = []
    scalar_counts = 0
    for accessor in accessors:
        _object(accessor, "accessor", {"bufferView", "byteOffset", "componentType", "count", "type", "min", "max", "normalized", "name"})
        vi = _reference(accessor.get("bufferView"), views, "accessor bufferView")
        view = views[vi]
        offset = _integer(accessor.get("byteOffset", 0), "accessor offset", 0, limits.bytes)
        component = _integer(accessor.get("componentType"), "accessor componentType", 0, 65535)
        shape = accessor.get("type")
        if component == 5126 and shape == "VEC3":
            fmt, item_bytes, component_bytes, width = "<3f", 12, 4, 3
        elif component in (5121, 5123, 5125) and shape == "SCALAR":
            fmt, component_bytes = {5121: ("<B", 1), 5123: ("<H", 2), 5125: ("<I", 4)}[component]
            item_bytes, width = component_bytes, 1
        else:
            _fail("only float32 VEC3 attributes and unsigned scalar indices are supported")
        if accessor.get("normalized", False) is not False:
            _fail("normalized accessors are unsupported")
        count = _integer(accessor.get("count"), "accessor count", 1, max(limits.vertices, limits.triangles * 3))
        scalar_counts += count * width
        if scalar_counts > limits.accessor_elements:
            _fail("total accessor element budget exceeded")
        stride = view.get("byteStride", item_bytes)
        absolute = view.get("byteOffset", 0) + offset
        if stride < item_bytes or offset % component_bytes or absolute % component_bytes:
            _fail("accessor alignment or stride is invalid")
        if offset + (count - 1) * stride + item_bytes > view["byteLength"]:
            _fail("accessor range exceeds its bufferView")
        for key in ("min", "max"):
            if key in accessor:
                _vector(accessor[key], width, f"accessor {key}", -1e30, 1e30)
        if "min" in accessor and "max" in accessor and any(a > b for a, b in zip(accessor["min"], accessor["max"])):
            _fail("accessor min exceeds max")
        layouts.append((absolute, stride, count, fmt, component, shape, vi))

    materials = _array(document.get("materials", []), "materials", 64)
    for material in materials:
        _object(material, "material", {"name", "pbrMetallicRoughness", "emissiveFactor", "alphaMode", "doubleSided"})
        if material.get("alphaMode", "OPAQUE") != "OPAQUE":
            _fail("only opaque materials are supported")
        if type(material.get("doubleSided", False)) is not bool:
            _fail("doubleSided must be boolean")
        _vector(material.get("emissiveFactor", [0, 0, 0]), 3, "emissive factor", 0, 1)
        pbr = _object(material.get("pbrMetallicRoughness", {}), "PBR material", {"baseColorFactor", "metallicFactor", "roughnessFactor"})
        color = _vector(pbr.get("baseColorFactor", [1, 1, 1, 1]), 4, "base color factor", 0, 1)
        if color[3] != 1:
            _fail("base color alpha must equal one")
        _number(pbr.get("metallicFactor", 1), "metallic factor", 0, 1)
        _number(pbr.get("roughnessFactor", 1), "roughness factor", 0, 1)

    meshes = _array(document.get("meshes"), "meshes", 256, 1)
    positions: dict[int, list[tuple]] = {}
    used_accessors: set[int] = set()
    used_views: set[int] = set()
    checked_normals: set[int] = set()
    mesh_data = []
    stored_triangles = 0
    stored_vertices = 0

    def values(ai):
        start, stride, count, fmt, _, _, vi = layouts[ai]
        used_accessors.add(ai)
        used_views.add(vi)
        for i in range(count):
            yield struct.unpack_from(fmt, binary, start + i * stride)

    for mesh in meshes:
        _object(mesh, "mesh", {"name", "primitives"})
        primitives = _array(mesh.get("primitives"), "mesh primitives", 64, 1)
        total_vertices, total_triangles, primitive_positions = 0, 0, []
        for primitive in primitives:
            _object(primitive, "primitive", {"attributes", "indices", "material", "mode"})
            if _integer(primitive.get("mode", 4), "primitive mode", 0, 6) != 4:
                _fail("only TRIANGLES primitives are supported")
            attributes = _object(primitive.get("attributes"), "attributes", {"POSITION", "NORMAL"})
            pi = _reference(attributes.get("POSITION"), accessors, "POSITION accessor")
            if layouts[pi][4:6] != (5126, "VEC3"):
                _fail("POSITION must be float32 VEC3")
            if views[layouts[pi][6]].get("target", 34962) != 34962:
                _fail("vertex attribute must use ARRAY_BUFFER")
            vertex_count = layouts[pi][2]
            if pi not in positions:
                stored_vertices += vertex_count
                if stored_vertices > limits.vertices:
                    _fail("stored vertex budget exceeded")
                points = list(values(pi))
                if any(not math.isfinite(v) or abs(v) > 10000 for p in points for v in p):
                    _fail("POSITION contains non-finite or unbounded coordinates")
                # GLTFLoader uses declared POSITION extrema for frustum culling;
                # accepting false metadata could hide otherwise valid geometry.
                for key, operation in (("min", min), ("max", max)):
                    if key not in accessors[pi]:
                        _fail("POSITION requires declared min and max bounds")
                    measured = [operation(p[axis] for p in points) for axis in range(3)]
                    if any(not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)
                           for a, b in zip(measured, accessors[pi][key])):
                        _fail("declared POSITION bounds do not match binary coordinates")
                positions[pi] = points
            points = positions[pi]
            if "NORMAL" in attributes:
                ni = _reference(attributes["NORMAL"], accessors, "NORMAL accessor")
                if layouts[ni][4:6] != (5126, "VEC3") or layouts[ni][2] != vertex_count:
                    _fail("NORMAL must be float32 VEC3 with the POSITION count")
                if views[layouts[ni][6]].get("target", 34962) != 34962:
                    _fail("NORMAL must use ARRAY_BUFFER")
                if ni not in checked_normals:
                    for normal in values(ni):
                        if any(not math.isfinite(v) for v in normal) or not 0.99 <= sum(v*v for v in normal) <= 1.01:
                            _fail("NORMAL must contain finite normalized vectors")
                    checked_normals.add(ni)
            if "material" in primitive:
                _reference(primitive["material"], materials, "primitive material")
            if "indices" in primitive:
                ii = _reference(primitive["indices"], accessors, "index accessor")
                if layouts[ii][5] != "SCALAR" or layouts[ii][4] not in (5121, 5123, 5125):
                    _fail("indices must be unsigned scalar integers")
                iview = views[layouts[ii][6]]
                if "byteStride" in iview or iview.get("target", 34963) != 34963:
                    _fail("indices must be tightly packed ELEMENT_ARRAY_BUFFER data")
                index_count = layouts[ii][2]
                triangle_count = index_count // 3
                if index_count % 3 or stored_triangles + triangle_count > limits.triangles:
                    _fail("index count must form triangles within the triangle budget")
                referenced = bytearray(vertex_count)
                triangle = []
                forbidden_restart = {5121: 255, 5123: 65535, 5125: 4294967295}[layouts[ii][4]]
                for (index,) in values(ii):
                    if index >= vertex_count:
                        _fail("index exceeds POSITION count")
                    if index == forbidden_restart:
                        _fail("primitive restart index is forbidden by glTF")
                    referenced[index] = 1
                    triangle.append(points[index])
                    if len(triangle) == 3:
                        _check_triangle(*triangle)
                        triangle.clear()
                if not all(referenced):
                    _fail("unreferenced POSITION vertices cannot establish component bounds")
            else:
                if vertex_count % 3:
                    _fail("non-indexed POSITION count must be divisible by three")
                triangle_count = vertex_count // 3
                for offset in range(0, vertex_count, 3):
                    _check_triangle(*points[offset:offset+3])
            stored_triangles += triangle_count
            if stored_triangles > limits.triangles:
                _fail("stored triangle budget exceeded")
            total_vertices += vertex_count
            total_triangles += triangle_count
            primitive_positions.append(pi)
        mesh_data.append((total_vertices, total_triangles, primitive_positions))
    if len(used_accessors) != len(accessors) or len(used_views) != len(views):
        _fail("unused accessors or bufferViews are unsupported")

    nodes = _array(document.get("nodes"), "nodes", 256, 1)
    scenes = _array(document.get("scenes"), "scenes", 1, 1)
    _reference(document.get("scene", 0), scenes, "default scene")
    scene = _object(scenes[0], "scene", {"name", "nodes"})
    roots = _array(scene.get("nodes"), "scene roots", 256, 1)
    parents = {}
    transforms = []
    for i, node in enumerate(nodes):
        _object(node, "node", {"name", "mesh", "children", "translation", "rotation", "scale", "matrix"})
        transforms.append(_node_matrix(node))
        if "mesh" in node:
            _reference(node["mesh"], meshes, "node mesh")
        for child in _array(node.get("children", []), "node children", 256):
            _reference(child, nodes, "child node")
            if child in parents:
                _fail("nodes must have a single parent without duplicate children")
            parents[child] = i
    for root in roots:
        _reference(root, nodes, "root node")
        if root in parents:
            _fail("scene root cannot have a parent")
    if len(set(roots)) != len(roots):
        _fail("scene roots must be unique")
    pending = [(root, _IDENTITY, 0) for root in roots]
    visited, used_meshes = set(), set()
    bounds_min, bounds_max = [math.inf]*3, [-math.inf]*3
    vertices = triangles = 0
    rank_origin = rank_line = rank_normal = None
    rank_three = False
    while pending:
        index, parent_matrix, depth = pending.pop()
        if index in visited or depth > 32:
            _fail("scene graph cycle or depth limit exceeded")
        visited.add(index)
        node = nodes[index]
        matrix = _multiply(parent_matrix, transforms[index])
        pending.extend((child, matrix, depth+1) for child in node.get("children", []))
        if "mesh" not in node:
            continue
        mi = node["mesh"]
        used_meshes.add(mi)
        nv, nt, pids = mesh_data[mi]
        vertices += nv
        triangles += nt
        if vertices > limits.vertices or triangles > limits.triangles:
            _fail("instantiated geometry budget exceeded")
        for pi in pids:
            for x, y, z in positions[pi]:
                point = [matrix[r]*x + matrix[4+r]*y + matrix[8+r]*z + matrix[12+r] for r in range(3)]
                if any(not math.isfinite(v) for v in point):
                    _fail("world POSITION is non-finite")
                for axis, v in enumerate(point):
                    bounds_min[axis] = min(bounds_min[axis], v)
                    bounds_max[axis] = max(bounds_max[axis], v)
                # Rank 3 rules out a single tilted plane whose AABB is volumetric.
                if rank_three:
                    continue
                if rank_origin is None:
                    rank_origin = point
                    continue
                delta = [a-b for a, b in zip(point, rank_origin)]
                if rank_line is None:
                    length = math.sqrt(sum(v*v for v in delta))
                    if length > 1e-6:
                        rank_line = [v/length for v in delta]
                elif rank_normal is None:
                    cross = [rank_line[1]*delta[2] - rank_line[2]*delta[1],
                             rank_line[2]*delta[0] - rank_line[0]*delta[2],
                             rank_line[0]*delta[1] - rank_line[1]*delta[0]]
                    length = math.sqrt(sum(v*v for v in cross))
                    if length > 1e-6:
                        rank_normal = [v/length for v in cross]
                elif abs(sum(a*b for a, b in zip(rank_normal, delta))) > 1e-6:
                    rank_three = True
    if len(visited) != len(nodes) or len(used_meshes) != len(meshes):
        _fail("all nodes and meshes must belong to the one active scene")
    if not rank_three or triangles < 4:
        _fail("component must contain non-coplanar 3D geometry")
    expected_min, expected_max = [-.5, 0., -.5], [.5, 1., .5]
    if any(abs(actual - expected) > NORMALIZATION_TOLERANCE
           for actual, expected in zip(bounds_min + bounds_max, expected_min + expected_max)):
        _fail("world bounds must be bottom-centered and normalized to [-.5, 0, -.5] / [.5, 1, .5]")
    return {"format": "glb", "validation_profile": "canvaslab-static-component-v1", "budget_profile": profile,
            "sha256": hashlib.sha256(payload).hexdigest(), "byte_length": len(payload),
            "triangles": triangles, "vertices": vertices, "stored_vertices": stored_vertices,
            "mesh_count": len(meshes), "node_count": len(nodes), "material_count": len(materials),
            "bounds": {"min": bounds_min, "max": bounds_max},
            "normalization_tolerance": NORMALIZATION_TOLERANCE}
