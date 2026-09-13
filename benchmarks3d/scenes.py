"""Engineering fixtures. Water-town annotations are approximate manual source boxes.

The pavilion variant tests a different composition, not faithful reconstruction
of the water-town source. Neither fixture is a calibrated acceptance benchmark.
"""
from copy import deepcopy


def water_town():
    palette = [("plaster", "#c7bd98"), ("timber", "#6c5034"), ("roof", "#566452"),
               ("stone", "#b8b49a"), ("water", "#4b9791"), ("foam", "#aad5bc"),
               ("leaf", "#84955a"), ("wood", "#ac854c"), ("cloth", "#e2d4ad")]
    def node(identity, kind, position, size, material, accent=None, **kw):
        return dict(id=identity, label=identity, kind=kind, position=position, dimensions=size,
                    material_id=material, accent_material_id=accent, region_ids=[identity],
                    inferred_surfaces="Single-image reconstruction: hidden faces and depth are inferred.", **kw)
    objects = [
        node("water", "water", [0, 0, 0], [14, .2, 9], "water", "foam"),
        node("quay", "box", [0, .2, -1.6], [14, .75, 5.6], "stone"),
        node("inn", "building", [0, .95, -1.8], [4.2, 4.5, 3], "plaster", "roof", floors=2),
        node("gate", "building", [-4.2, .95, -2.1], [2.0, 3.7, 2.0], "timber", "roof"),
        node("shop", "building", [4.3, .95, -1.6], [3, 2.55, 2.7], "plaster", "roof"),
        node("left-tree", "tree", [-5.6, .95, -2.7], [2.8, 3.6, 2.5], "leaf", "timber"),
        node("right-tree", "tree", [4.6, .95, -3.7], [2.8, 4.2, 2.6], "leaf", "timber"),
        node("dock-left", "dock", [-3.5, .25, 1.8], [4, .6, 2.4], "wood", "timber"),
        node("dock-right", "dock", [3.5, .25, 2.0], [3.3, .6, 2.0], "wood", "timber"),
        node("covered-boat", "boat", [2.3, .22, 3.7], [2.8, .9, 1.3], "timber", "cloth",
             movement={"bounds": [-2, 3.1, 5, 4.2], "amplitude": .6, "speed": .5}),
        node("cargo-boat", "boat", [-4, .22, 3.1], [3.6, .85, 1.3], "timber", "wood", canopy=False,
             movement={"bounds": [-5, 2.8, -2, 4.0], "amplitude": .4, "speed": .4}),
        node("crate", "box", [-3.7, .78, 1.8], [.7, .7, .7], "wood", "timber",
             movement={"bounds": [-4.4, 1.3, -2.6, 2.3]}),
    ]
    return {"title": "青崖渡 · 参数化灰模", "camera": {"position": [12, 12, 18], "target": [0, 1.5, 0], "vertical_span": 13.0},
            "materials": [{"id": i, "color": c} for i, c in palette], "objects": objects,
            "assumptions": ["Engineering prototype: roof details, railings, sails, signs and precise camera fit remain unfinished."]}


def pavilion():
    p = water_town()
    p["title"] = "庭院展台 · 组件复用测试"
    keep = {"quay", "inn", "shop", "right-tree", "crate"}
    p["objects"] = [deepcopy(n) for n in p["objects"] if n["id"] in keep]
    for n in p["objects"]:
        if n["id"] == "quay":
            n["position"] = [0, 0, 0]; n["dimensions"] = [10, .5, 8]
        elif n["id"] == "inn":
            n["position"] = [-2.5, .5, -1]; n["dimensions"] = [3, 2.8, 3]; n["floors"] = 1
        elif n["id"] == "shop":
            n["position"] = [2, .5, 1.3]; n["dimensions"] = [2.2, 3, 2]
        elif n["id"] == "right-tree":
            n["position"] = [2.6, .5, -2.2]
        else:
            n["position"] = [-2, .5, 2]; n["movement"] = {"bounds": [-3, 1.5, 0, 3]}
    p["camera"] = {"position": [10, 13, 16], "target": [0, 1, 0], "vertical_span": 12.0}
    p["assumptions"] = ["Alternate composition for component reuse testing; not a reconstruction of the water-town image."]
    return p


# Boxes measured approximately on the 936x2048 displayed source. Input bytes are
# retained unmodified; coordinates are mapped to the original's actual resolution.
WATER_BOXES = {
    "water": [70, 907, 750, 226], "quay": [170, 797, 620, 236],
    "inn": [383, 642, 208, 258], "gate": [282, 638, 113, 212],
    "shop": [559, 759, 213, 168], "left-tree": [189, 684, 113, 157],
    "right-tree": [591, 690, 103, 127], "dock-left": [249, 920, 178, 83],
    "dock-right": [454, 952, 178, 70], "covered-boat": [431, 984, 117, 88],
    "cargo-boat": [137, 883, 147, 89], "crate": [298, 925, 56, 46],
}


def analysis_for(source, plan, source_layout="water-original"):
    sx, sy = source["width"]/936, source["height"]/2048
    if source_layout == "water-original":
        scene_box = [60*sx, 620*sy, 780*sx, 530*sy]
        boxes = {key: [v[0]*sx, v[1]*sy, v[2]*sx, v[3]*sy] for key, v in WATER_BOXES.items()}
    else:
        scene_box = [0, 0, source["width"], source["height"]]
        # Deliberately low-confidence engineering placeholders, never used as
        # acceptance targets. A caller must replace them after inspecting source.
        boxes = {n["id"]: [0, 0, source["width"], source["height"]] for n in plan["objects"]}
    return {"source_sha256": source["sha256"], "scene_box": scene_box,
            "regions": [{"id": n["id"], "label": n["label"], "box": boxes[n["id"]],
                         "critical": True, "confidence": .65 if source_layout == "water-original" else .1,
                         "evidence": "Approximate manual image region; independent calibrated masks not available."} for n in plan["objects"]],
            "assumptions": ["No reference evidence for hidden surfaces."], "change_reason": "Initial engineering fixture; not an accepted fidelity baseline."}
