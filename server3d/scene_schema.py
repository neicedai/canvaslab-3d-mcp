"""Strict, bounded declarative input. There is deliberately no JavaScript field."""
from __future__ import annotations

import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import CONTRACT

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Number = Annotated[float, Field(ge=-1000, le=1000)]
Vec3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
Point2 = Annotated[list[Annotated[float, Field(ge=0, le=8192)]], Field(min_length=2, max_length=2)]
Polygon = Annotated[list[Point2], Field(min_length=3, max_length=64)]
RenderQuality = Literal["standard", "showcase"]


def scene_budgets(render_quality: str = "standard") -> tuple[int, int]:
    """Instantiated triangles and unique component bytes for a trusted profile."""
    if render_quality == "standard":
        return 250_000, 64 * 1024 * 1024
    if render_quality == "showcase":
        return 1_500_000, 128 * 1024 * 1024
    raise ValueError("Unknown scene render quality; expected standard or showcase")


def primitive_triangle_budget(kind: str, render_quality: str = "standard") -> int:
    # Sign is exactly one box (12) plus one text plane (2) in primitives.js.
    # Keep conservative estimates for the other procedural templates.
    # Showcase water adds 42 round 28-segment lily pads (4704 triangles),
    # the closed volume and one reflection plane. Standard remains unchanged.
    if kind == "water" and render_quality == "showcase":
        return 5000
    return 0 if kind == "asset" else 14 if kind == "sign" else 8000 if kind == "building" else 2500


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Region(Strict):
    id: Id
    label: str = Field(min_length=1, max_length=100)
    box: list[Annotated[float, Field(ge=0, le=8192)]] = Field(min_length=4, max_length=4)
    critical: bool = True
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=1000)
    # Visible silhouettes measured on the ORIGINAL, never inferred from a render.
    # Multiple simple polygons form a union; holes are not supported in v1.
    visible_polygons: list[Polygon] = Field(default_factory=list, max_length=8)


class Analysis(Strict):
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scene_box: list[Annotated[float, Field(ge=0, le=8192)]] = Field(min_length=4, max_length=4)
    regions: list[Region] = Field(min_length=1, max_length=128)
    assumptions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(max_length=128)
    change_reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        if len({r.id for r in self.regions}) != len(self.regions):
            raise ValueError("duplicate region IDs")
        if not any(r.critical for r in self.regions):
            raise ValueError("at least one critical region is required")
        return self


class Material(Strict):
    id: Id
    color: Color
    roughness: float = Field(default=0.8, ge=0, le=1)
    metalness: float = Field(default=0.0, ge=0, le=1)


class Camera(Strict):
    projection: Literal["orthographic", "perspective"] = "orthographic"
    position: Vec3
    target: Vec3
    vertical_span: float = Field(default=15.0, ge=1, le=150)
    fov: float = Field(default=40.0, ge=15, le=80)

    @model_validator(mode="after")
    def direction(self):
        dx, dy, dz = [a-b for a, b in zip(self.position, self.target)]
        if math.hypot(dx, dz) < 0.01 or dy <= 0:
            raise ValueError("camera must be above target with a nonvertical view direction")
        return self


class Movement(Strict):
    # Allowed center coordinates in world X/Z, not an arbitrary script.
    bounds: list[Number] = Field(min_length=4, max_length=4)
    amplitude: float = Field(default=0.0, ge=0, le=20)
    speed: float = Field(default=0.4, ge=0.05, le=2)

    @model_validator(mode="after")
    def ordered(self):
        a, b, c, d = self.bounds
        if a >= c or b >= d:
            raise ValueError("movement bounds must be [minX,minZ,maxX,maxZ]")
        return self


class SceneObject(Strict):
    id: Id
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["box", "cylinder", "sphere", "building", "roof", "boat", "tree", "dock", "water", "asset", "sign"]
    text: str | None = Field(default=None, min_length=1, max_length=24)
    asset_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    parent_id: Id | None = None
    region_ids: list[Id] = Field(min_length=1, max_length=32)
    position: Vec3
    dimensions: Annotated[list[Annotated[float, Field(ge=0.05, le=50)]], Field(min_length=3, max_length=3)]
    rotation: list[Number] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0], min_length=4, max_length=4)
    material_id: Id
    accent_material_id: Id | None = None
    floors: int = Field(default=1, ge=1, le=3)
    roof_enabled: bool = True
    canopy: bool = True
    movement: Movement | None = None
    inferred_surfaces: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_motion(self):
        if (self.kind == "sign") != (self.text is not None):
            raise ValueError("Only volumetric signs accept bounded text, and signs require text")
        if (self.kind == "asset") != (self.asset_id is not None):
            raise ValueError("asset objects require a registered asset_id; procedural objects must not specify it")
        if abs(sum(x*x for x in self.rotation)-1) > 0.001:
            raise ValueError("rotation must be a normalized [x,y,z,w] quaternion")
        if self.movement:
            if self.parent_id is not None or self.kind not in {"boat", "box", "asset"}:
                raise ValueError("only root boats, boxes and permitted registered assets support movement")
            x, _, z = self.position
            a, b, c, d = self.movement.bounds
            if not a <= x <= c or not b <= z <= d:
                raise ValueError("home position must be inside movement bounds")
            if not a <= x-self.movement.amplitude <= x+self.movement.amplitude <= c:
                raise ValueError("animation must stay inside movement bounds")
        return self


class ReferenceLighting(Strict):
    """Measured or explicitly estimated lighting; never inferred from quality level."""
    sky_color: Color = "#ffffff"
    ground_color: Color = "#808080"
    hemisphere_intensity: float = Field(default=1.0, ge=0, le=4)
    sun_color: Color = "#ffffff"
    sun_intensity: float = Field(default=2.0, ge=0, le=8)
    sun_position: Vec3 = Field(default_factory=lambda: [-8.0, 14.0, 8.0])
    sun_target: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    exposure: float = Field(default=1.0, ge=0.1, le=4)
    tone_mapping: Literal["aces", "none"] = "aces"

    @model_validator(mode="after")
    def direction(self):
        if math.dist(self.sun_position, self.sun_target) < 0.01:
            raise ValueError("sun position and target must differ")
        return self


class ScenePlan(Strict):
    contract_version: Literal["canvaslab-scene-v1"] = CONTRACT
    title: str = Field(min_length=1, max_length=100)
    presentation: Literal["studio", "jiangnan"] = "studio"
    render_quality: RenderQuality = Field(default="standard", description="Geometry and sampling budget. In reference mode quality never enables stylistic material, water, environment or color-grading changes; legacy mode retains the historical showcase profile.")
    appearance_mode: Literal["legacy", "reference"] = Field(default="legacy", description="Omitted preserves existing scenes. New source-faithful jobs should explicitly use reference.")
    reference_lighting: ReferenceLighting | None = None
    coordinate_system: Literal["right-handed-y-up-relative"] = "right-handed-y-up-relative"
    mode: Literal["true_3d"] = "true_3d"
    seed: int = Field(default=1977, ge=0, le=2**31-1)
    background: Color = "#eee9df"
    camera: Camera
    view_angle_degrees: float = Field(default=60.0, ge=15, le=90)
    materials: list[Material] = Field(min_length=1, max_length=32)
    objects: list[SceneObject] = Field(min_length=1, max_length=128)
    assumptions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def graph(self):
        if self.reference_lighting is not None and self.appearance_mode != "reference":
            raise ValueError("reference_lighting requires appearance_mode=reference")
        nodes = {x.id: x for x in self.objects}
        mats = {x.id for x in self.materials}
        if len(nodes) != len(self.objects) or len(mats) != len(self.materials):
            raise ValueError("duplicate object or material IDs")
        for node in self.objects:
            if node.material_id not in mats or (node.accent_material_id and node.accent_material_id not in mats):
                raise ValueError(f"unknown material on {node.id}")
            seen = {node.id}
            parent = node.parent_id
            while parent:
                if parent not in nodes or parent in seen or len(seen) > 8:
                    raise ValueError("invalid, cyclic or excessively deep parent graph")
                seen.add(parent)
                parent = nodes[parent].parent_id
        # Registered assets use measured, instantiated triangle budgets in Store.
        if sum(primitive_triangle_budget(n.kind,self.render_quality) for n in self.objects) > scene_budgets(self.render_quality)[0]:
            raise ValueError("estimated triangle budget exceeded")
        return self


def validate_analysis(data: dict, source: dict) -> Analysis:
    analysis = Analysis.model_validate(data)
    if analysis.source_sha256 != source["sha256"]:
        raise ValueError("analysis must refer to this job's original image")
    sx, sy, sw, sh = analysis.scene_box
    if sw < 64 or sh < 64 or sx+sw > source["width"] or sy+sh > source["height"]:
        raise ValueError("scene_box must be a native source crop of at least 64x64")
    for region in analysis.regions:
        x, y, w, h = region.box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > source["width"] or y+h > source["height"]:
            raise ValueError(f"region {region.id} is outside native source bounds")
        if x < sx or y < sy or x+w > sx+sw or y+h > sy+sh:
            raise ValueError(f"region {region.id} is outside scene_box")
        for polygon in region.visible_polygons:
            from .visual_metrics import validate_polygon
            validate_polygon(polygon, region.box)
    return analysis


def validate_plan(data: dict, analysis: dict) -> ScenePlan:
    plan = ScenePlan.model_validate(data)
    known = {r["id"] for r in analysis["regions"]}
    used = {r for n in plan.objects for r in n.region_ids}
    if used - known:
        raise ValueError("object refers to unknown reference region")
    if {r["id"] for r in analysis["regions"] if r["critical"]} - used:
        raise ValueError("critical reference regions are missing from scene")
    return plan
