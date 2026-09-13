"""Bounded local Blender recipes, not a general Python execution interface."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .scene_schema import Color, Strict


COMPONENT_CONTRACT = "canvaslab-component-v1"
COMPONENT_TEMPLATES = ("open_gate", "tiled_roof", "railing", "cargo_crate", "riverside_inn", "stone_quay", "tea_stall", "broadleaf_tree", "blossom_tree", "woven_boat", "rowing_boat", "timber_pier", "potted_garden", "bamboo_rack", "woven_basket")

Offset = Annotated[float, Field(ge=-0.35, le=0.35)]


class DeformationHandle(Strict):
    """Bounded local edit in normalized glTF component coordinates.

    anchor uses x/z in [-.5,.5] and y in [0,1]. offset is a normalized local
    displacement before the post-edit mesh is normalized back to unit bounds.
    """
    anchor: Annotated[list[float], Field(min_length=3, max_length=3)]
    offset: Annotated[list[Offset], Field(min_length=3, max_length=3)]
    radius: float = Field(default=0.25, ge=0.05, le=0.9)
    strength: float = Field(default=1.0, ge=0.05, le=1.0)

    @model_validator(mode="after")
    def nonzero(self):
        x, y, z = self.anchor
        if not (-0.5 <= x <= 0.5 and 0.0 <= y <= 1.0 and -0.5 <= z <= 0.5):
            raise ValueError("deformation anchor must be inside normalized component bounds")
        if sum(value * value for value in self.offset) < 1e-8:
            raise ValueError("deformation offset must be nonzero")
        return self


class ComponentRecipe(Strict):
    template: Literal["open_gate", "tiled_roof", "railing", "cargo_crate", "riverside_inn", "stone_quay", "tea_stall", "broadleaf_tree", "blossom_tree", "woven_boat", "rowing_boat", "timber_pier", "potted_garden", "bamboo_rack", "woven_basket"]
    detail: int = Field(default=1, ge=1, le=3, description="1: lightweight. 2: refined curves, smooth foliage/round surfaces and small edge bevels. 3: showcase architecture, fine joinery, woven surfaces and layered foliage. All levels remain bounded; detail 3 uses the explicit showcase component budget.")
    primary_color: Color = "#75513c"
    secondary_color: Color = "#397e73"
    deformation_handles: list[DeformationHandle] = Field(default_factory=list, max_length=16, description="Optional source-measured local shape edits in normalized component coordinates. They are applied after the fixed template build and before final validation; this is not arbitrary mesh or script input.")
