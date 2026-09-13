"""Original water-town engineering composition using registered Blender assets.

This is NOT a calibrated fidelity benchmark. Reuse analysis_for(source,
water_town()) from scenes.py: added asset nodes intentionally refer to existing
source regions rather than inventing object-level image measurements.
"""
import re

from .scenes import water_town


def component_water_town(asset_ids: dict[str, str]) -> dict:
    required = {"open_gate", "tiled_roof", "railing", "cargo_crate"}
    if not required.issubset(asset_ids) or any(
            not isinstance(asset_ids[name], str) or not re.fullmatch(r"[0-9a-f]{64}", asset_ids[name])
            for name in required):
        raise ValueError("registered SHA256 asset IDs are required for all four Blender templates")
    plan = water_town()
    plan["title"] = "青崖渡 · Blender 组件开发预览"
    by_id = {node["id"]: node for node in plan["objects"]}

    # Preserve the existing positions, proportions, native-region associations
    # and crate controls. The original's left gate is an open timber structure.
    for identity, template in (("gate", "open_gate"), ("crate", "cargo_crate")):
        node = by_id[identity]
        node["kind"] = "asset"
        node["asset_id"] = asset_ids[template]
        node.pop("floors", None)
        node["inferred_surfaces"] = (
            "Template shape is guided by the original image; hidden surfaces and exact timber joints remain inferred."
        )

    def asset(identity, label, template, position, dimensions, region, material="wood", **extra):
        return {"id": identity, "label": label, "kind": "asset", "asset_id": asset_ids[template],
                "position": position, "dimensions": dimensions, "material_id": material,
                "region_ids": [region],
                "inferred_surfaces": "Engineering detail within an existing source region; placement is approximate, not independently calibrated.",
                **extra}

    for identity in ("inn", "shop"):
        parent = by_id[identity]
        width, height, depth = parent["dimensions"]
        parent["roof_enabled"] = False
        plan["objects"].append(asset(
            identity + "-tiled-roof", identity + " curved ceramic roof", "tiled_roof",
            [0, height * .68, 0], [width * 1.06, height * .32, depth * 1.1],
            identity, material="roof", parent_id=identity,
        ))

    # The photographed quay has short rail runs with breaks around steps and
    # docking access. Keep those breaks instead of drawing a continuous fence.
    for identity, x, width in (("rail-left", -5.4, 2.4),
                               ("rail-middle", -.2, 2.4),
                               ("rail-right", 5.6, 2.0)):
        plan["objects"].append(asset(identity, "quay timber rail", "railing",
                                     [x, .95, 1.08], [width, .72, .22], "quay", material="timber"))

    # A small number of independent reusable cargo instances, as visible in the
    # original. Do not claim these broad dock-region boxes measure each crate.
    plan["objects"].extend([
        asset("cargo-left-small", "small crate on left dock", "cargo_crate",
              [-2.55, .79, 2.04], [.57, .57, .57], "dock-left"),
        asset("cargo-right", "crate on right dock", "cargo_crate",
              [3.72, .79, 1.86], [.7, .7, .7], "dock-right"),
        asset("cargo-quay", "crate near gate", "cargo_crate",
              [-3.0, .95, -1.05], [.62, .62, .62], "quay"),
    ])
    plan["assumptions"] = [
        "Engineering preview using the original water-town composition; not an accepted visual reconstruction.",
        "Blender supplies volumetric open gate, curved roof tiles, railings and cargo; buildings, trees and boats remain procedural.",
        "Added detail uses existing broad reference regions; independent silhouettes, precise camera fit, signs, sails, quay steps and full collision are still missing.",
        "Hidden surfaces and relative depths are inferred from a single image; reusable assets are not ground-truth recovered meshes.",
    ]
    return plan
