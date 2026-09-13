"""Explicit source-led composition study, not an arbitrary-image template.

The input camera proposal comes from fit_scene_landmarks over the original.
Object placement is a reviewable manual hypothesis; new browser evidence is
required. No generated/signed build file is edited in place.
"""
import argparse
import json
import math
import copy
from pathlib import Path


def revised_plan(plan):
    nodes = {node["id"]: node for node in plan["objects"]}
    # Source-visible ground, roof and tree proportions. These are separate
    # hypotheses from the partially occluded water-plane calibration points.
    poses = {
        "inn": ([.302, 1.035, -1.823], [6.04, 4.40, 4.85]),
        "gate": ([-4.553149, 1.02, 2.294483], [2.703341, 2.780153, 1.906461]),
        "tree-back": ([-4.034, 1.07, -2.11], [3.61, 3.55, 3.34]),
        "tree-pink": ([-4.334, 1.10, .554], [3.54, 2.83, 2.83]),
        "tree-right": ([5.1, 1.04, -2.48], [3.25, 4.2, 3.0]),
        "pier": ([-2.2, .02, 2.70], [2.76, .92, 1.63]),
        "boat-main": ([1.5, .015, 4.55], [5.65, 1.86, 1.91]),
        "boat-small": ([5.45, .005, 2.65], [3.4, 1.1, 1.9]),
        "tea": ([5.115804, 1.05, -.801894], [2.35, 2.4, 1.0]),
        "garden-left": ([-5.55, 1.0, 2.45], [1.6, 1.35, 1.65]),
        "garden-gate": ([-3.4, 1.02, 1.35], [1.05, .85, .85]),
        "garden-right": ([6.10, 1.08, -.15], [1.2, 1.2, 1.6]),
        "garden-inn-right": ([2.45, 1.07, .75], [1.05, .95, .8]),
    }
    for identity, (position, dimensions) in poses.items():
        nodes[identity].update(position=position, dimensions=dimensions)
    for identity, height_factor in [("garden-front", .72), ("garden-gate", .8), ("garden-inn-right", .7)]:
        nodes[identity]["dimensions"][1] *= height_factor
    # The original land edge/roof ridge are not parallel in screen space to
    # the water slab. A small relative yaw preserves a real 3D configuration.
    yaw = math.radians(12)
    for identity in ["quay", "inn", "gate", "tea"]:
        nodes[identity]["rotation"] = [0., math.sin(yaw/2), 0., math.cos(yaw/2)]
    nodes["gate"]["rotation"] = [0., .5, 0., math.sqrt(.75)]
    nodes["boat-main"]["movement"]["bounds"] = [.9, 4.05, 2.0, 5.05]
    nodes["boat-small"]["movement"]["bounds"] = [5.1, 2.45, 5.9, 3.05]
    # Existing pier cargo must stay on its deck after moving/scaling the pier.
    nodes["crate-0"]["position"] = [-1.65, .64, 2.85]
    nodes["crate-1"]["position"] = [-2.25, .64, 3.0]
    nodes["crate-2"]["position"] = [5.75, 1.02, .7]
    for identity in ["crate-0", "crate-1", "crate-2"]:
        nodes[identity]["label"] = "编织竹篮"
        nodes[identity]["dimensions"] = [.43, .47, .43]

    rack = copy.deepcopy(nodes["tea"])
    rack.update(id="tea-rack", label="茶摊后竹架", region_ids=["tea-rack"],
                position=[6.65,1.05,-1.55], dimensions=[1.55,2.5,.9],
                inferred_surfaces="Separate bamboo rack observed behind the source canopy; hidden rear joinery inferred.")
    plan["objects"].append(rack)
    nodes["tea-rack"] = rack
    banner = copy.deepcopy(nodes["inn-side-sign"])
    banner.update(id="tea-banner", label="清茶一盏", text="清茶一盏", region_ids=["tea-rack"],
                  material_id="ma5ac8d", accent_material_id="m494a34")
    plan["objects"].append(banner)
    nodes["tea-banner"] = banner

    def attached(identity, parent_id, local_center, dimensions, extra_yaw=0., rotation=None):
        parent = nodes[parent_id]
        center = [a*b for a, b in zip(local_center, parent["dimensions"])]
        angle = 2*math.atan2(parent["rotation"][1], parent["rotation"][3])
        x, y, z = center
        center = [math.cos(angle)*x+math.sin(angle)*z, y, -math.sin(angle)*x+math.cos(angle)*z]
        spec = nodes[identity]
        total = angle+extra_yaw
        spec["rotation"] = rotation or [0., math.sin(total/2), 0., math.cos(total/2)]
        # Transform the sign's bottom-origin offset with its FULL quaternion,
        # not just world-Y: roof lettering is tilted along the cloth surface.
        qx,qy,qz,qw = spec["rotation"]
        half = dimensions[1]/2
        offset = [2*(qx*qy-qw*qz)*half, (1-2*(qx*qx+qz*qz))*half,
                  2*(qy*qz+qw*qx)*half]
        spec.update(position=[a+b-c for a,b,c in zip(parent["position"],center,offset)], dimensions=dimensions)

    attached("inn-sign", "inn", [-.02477948,.36407278,.46180990], [1.18,.21,.05])
    attached("inn-side-sign", "inn", [.38585448,.55406294,.12686568], [.44,1.12,.05], math.pi/2)
    attached("gate-sign", "gate", [0.,.6518020,.2989483], [.57,.16,.05])
    # Cloth sign follows the roof slope AND the stall's new rotation.
    # Normal follows the front canopy slope AFTER nonuniform model scaling:
    # rise/run = .25 * (height/3.05) / (depth/2.263).
    ax, ay = -math.atan(1/(.25*(nodes["tea"]["dimensions"][1]/3.05)/(nodes["tea"]["dimensions"][2]/2.263))), yaw
    rotation = [math.sin(ax/2)*math.cos(ay/2), math.cos(ax/2)*math.sin(ay/2),
                -math.sin(ax/2)*math.sin(ay/2), math.cos(ax/2)*math.cos(ay/2)]
    nodes["tea-sign"]["accent_material_id"] = "m373325"
    attached("tea-sign", "tea", [.010944,.922951,.223933], [.82,.62,.05], rotation=rotation)
    attached("tea-banner", "tea-rack", [-.46,.52,.40], [.32,.9,.05])
    plan["assumptions"] += [
        "Camera fit uses one visible water corner and three explicit occlusion estimates; its low residual is not fidelity proof.",
        "Land and architecture are rotated 12 degrees relative to the water rectangle to match the source edge directions.",
        "Component placements remain source-led hypotheses and must be checked for actual occlusion and collision in fresh captures.",
    ]
    return plan


def revised_analysis(analysis):
    result = copy.deepcopy(analysis)
    for region in result["regions"]:
        if region["id"] == "tea":
            region["box"] = [1070., 390., 222., 235.]
            region["evidence"] = "Re-measured the standalone cloth canopy, counter and feet; excludes the independent rack to the right."
    result["regions"].append({"id":"tea-rack", "label":"独立竹架", "box":[1260.,410.,155.,208.],
                               "critical":True, "confidence":.7,
                               "evidence":"Manually observed separate bamboo poles, rolled bamboo screen and vertical tea banner to the right of the cloth stall."})
    result["change_reason"] = "Separate the observed bamboo rack from the canopy instead of stretching one component over both."
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("camera_proposal", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--analysis-base", type=Path)
    parser.add_argument("--analysis-output", type=Path)
    parser.add_argument("--recipes-output", type=Path)
    args = parser.parse_args()
    plan = revised_plan(json.loads(args.camera_proposal.read_text(encoding="utf-8")))
    from server3d.scene_schema import ScenePlan
    plan = ScenePlan.model_validate(plan).model_dump()
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(plan, output, ensure_ascii=False, indent=2)
    if args.analysis_output:
        if not args.analysis_base:
            parser.error("--analysis-output requires --analysis-base")
        with args.analysis_output.open("x", encoding="utf-8") as output:
            json.dump(revised_analysis(json.loads(args.analysis_base.read_text(encoding="utf-8"))), output, ensure_ascii=False, indent=2)
    if args.recipes_output:
        recipes = {identity:{"template":"woven_basket","detail":3} for identity in ["crate-0","crate-1","crate-2"]}
        recipes["tea-rack"] = {"template":"bamboo_rack","detail":3}
        with args.recipes_output.open("x", encoding="utf-8") as output:
            json.dump(recipes, output, indent=2)
