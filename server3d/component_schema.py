"""Bounded local Blender recipes, not a general Python execution interface."""
from typing import Literal

from pydantic import Field

from .scene_schema import Color, Strict


COMPONENT_CONTRACT = "canvaslab-component-v1"
COMPONENT_TEMPLATES = ("open_gate", "tiled_roof", "railing", "cargo_crate", "riverside_inn", "stone_quay", "tea_stall", "broadleaf_tree", "blossom_tree", "woven_boat", "rowing_boat", "timber_pier", "potted_garden", "bamboo_rack", "woven_basket")


class ComponentRecipe(Strict):
    template: Literal["open_gate", "tiled_roof", "railing", "cargo_crate", "riverside_inn", "stone_quay", "tea_stall", "broadleaf_tree", "blossom_tree", "woven_boat", "rowing_boat", "timber_pier", "potted_garden", "bamboo_rack", "woven_basket"]
    detail: int = Field(default=1, ge=1, le=3, description="1: lightweight. 2: refined curves, smooth foliage/round surfaces and small edge bevels. 3: showcase architecture, fine joinery, woven surfaces and layered foliage. All levels remain bounded; detail 3 uses the explicit showcase component budget.")
    primary_color: Color = "#75513c"
    secondary_color: Color = "#397e73"
