"""Print manual-camera calibration and projected component-vertex bounds.

This is an engineering inspection utility, not automatic image detection. The
reported vertex bounds do not account for occlusion by other scene objects.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from server3d.builder import verify_build
from server3d.glb_validation import _multiply, _node_matrix, _parse_glb
from server3d.landmark_fit import fit_orthographic_landmarks, project_orthographic_points


IDENTITY = tuple(np.eye(4).flatten(order="F"))


def component_vertices(payload):
    document, binary = _parse_glb(payload, 64 * 1024 * 1024)
    output = []
    pending = [(i, IDENTITY) for i in document["scenes"][0]["nodes"]]
    while pending:
        identity, parent = pending.pop()
        node = document["nodes"][identity]
        matrix = _multiply(parent, _node_matrix(node))
        pending.extend((child, matrix) for child in node.get("children", []))
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            accessor = document["accessors"][primitive["attributes"]["POSITION"]]
            view = document["bufferViews"][accessor["bufferView"]]
            offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            points = np.ndarray((accessor["count"], 3), dtype="<f4", buffer=binary, offset=offset,
                                strides=(view.get("byteStride", 12), 4)).astype(float)
            transform = np.asarray(matrix).reshape(4, 4, order="F")
            output.append(points @ transform[:3, :3].T + transform[:3, 3])
    return np.vstack(output)


def projected_component_bounds(directory, plan, camera, source_size):
    cache, result = {}, {}
    nodes = {node["id"]: node for node in plan["objects"]}
    def world_matrix(node):
        own = _node_matrix({"translation": node["position"], "rotation": node["rotation"]})
        return _multiply(world_matrix(nodes[node["parent_id"]]), own) if node["parent_id"] else own
    for node in plan["objects"]:
        if node["kind"] != "asset":
            continue
        identity = node["asset_id"]
        if identity not in cache:
            cache[identity] = component_vertices((directory / f"component-{identity}.glb").read_bytes())
        matrix = np.asarray(world_matrix(node)).reshape(4, 4, order="F")
        points = (cache[identity] * node["dimensions"]) @ matrix[:3, :3].T + matrix[:3, 3]
        projected = np.asarray(project_orthographic_points(source_size, points, camera))
        low, high = projected.min(axis=0), projected.max(axis=0)
        result[node["id"]] = np.r_[low, high - low].tolist()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("landmarks", type=Path)
    args = parser.parse_args()
    verify_build(args.build)
    plan = json.loads((args.build / "scene.json").read_text(encoding="utf-8"))
    source = json.loads((args.build / "reference-annotations.json").read_text(encoding="utf-8"))
    manual = json.loads(args.landmarks.read_text(encoding="utf-8"))
    if manual["source_sha256"] != source["source_sha256"]:
        raise ValueError("Manual points must refer to this verified build's exact source")
    fit = fit_orthographic_landmarks(manual["source_size"], manual["landmarks"], plan["camera"])
    print(json.dumps({"fit": fit, "component_bounds_without_occlusion": projected_component_bounds(
        args.build, plan, fit["suggested_camera"], manual["source_size"])}, indent=2))
