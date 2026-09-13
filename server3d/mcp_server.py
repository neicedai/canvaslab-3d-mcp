"""Run with python -m server3d.mcp_server (stdio); HTTP is in server3d.api."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from . import VERSION
from .jobs import Store
from .scene_schema import ScenePlan, Analysis
from .component_schema import ComponentRecipe, COMPONENT_CONTRACT, COMPONENT_TEMPLATES
from .deformation_fit import suggest_deformation_handles
from .fidelity_compare import evaluate_scene_candidate


def expected(function, *args):
    try:
        return function(*args)
    except (ValueError, FileNotFoundError) as exc:
        raise ToolError(str(exc)) from exc


def create_mcp(store: Store):
    mcp = MCPServer("CanvasLab 3D", version=VERSION, instructions=(
        "Independent procedural 3D prototype. Upload original bytes through authenticated /assets. "
        "Inspect the original and crop out unrelated page chrome. Save native-coordinate source analysis, "
        "validate and persist a scene plan, then build from that plan ID. Never invent observation evidence. "
        "No production fidelity/completion certificates are supported yet. Generated scenes are previews. "
        "Call scene_runtime_status before testing and retain exact source/build/capture versions. "
        "Reference fidelity is the objective, not a detail preset. First match camera projection, "
        "silhouettes, relative proportions, placement and occlusion against the ORIGINAL, then "
        "refine geometry, surface materials, lighting and water. A preset is never an absolute quality ceiling. "
        "For a near-view request, managed component detail=3 with render_quality=showcase is a "
        "starting recipe; replace or correct mismatching components instead of repeatedly raising detail. "
        "Inspect captured closeup and dusk views as well as the native reference; measured budgets "
        "still apply, and higher detail is not proof of reference fidelity."))

    @mcp.tool(structured_output=True)
    def scene_runtime_status() -> dict[str, Any]:
        """Report exact runtime fingerprints, supported features and explicit development limitations."""
        return store.status()

    @mcp.tool(structured_output=True)
    def get_scene_contract() -> dict[str, Any]:
        """Read strict schemas before proposing source annotations or a procedural scene."""
        return {"analysis_schema": Analysis.model_json_schema(), "scene_schema": ScenePlan.model_json_schema(),
                "component_schema":ComponentRecipe.model_json_schema(), "component_contract":COMPONENT_CONTRACT,
                "workflow_complete_supported": False}

    @mcp.tool(structured_output=True)
    def list_scene_components() -> dict[str, Any]:
        """List managed Blender recipes and reusable immutable components; arbitrary GLB imports are not accepted."""
        return {"templates":list(COMPONENT_TEMPLATES),"blender":store.components.status(),
                "components":expected(store.components.list)}

    @mcp.tool(structured_output=True)
    async def generate_scene_component(recipe: dict, idempotency_key: str) -> dict[str, Any]:
        """Run a fixed bounded Blender recipe, validate static GLB and retain editable .blend.

        No arbitrary Python, shell commands, URLs or .blend uploads. Repeated recipe
        and producer version reuse an immutable asset_id. Assign it to kind=asset
        in a new scene plan; then rebuild and capture, never patch generated JS.
        detail=3 is the bounded near-view showcase recipe; pair it with the
        scene's explicit render_quality=showcase for near-view requests. This
        recipe does not reconstruct arbitrary source geometry or set a quality
        ceiling: compare its shape to the original before accepting it.
        """
        return await asyncio.to_thread(expected, store.components.generate, recipe, idempotency_key)

    @mcp.tool(structured_output=True)
    def get_scene_component(asset_id: str) -> dict[str, Any]:
        """Verify and return component provenance, measured geometry, source recipe and download paths."""
        return expected(store.components.get, asset_id)

    @mcp.tool(structured_output=True)
    def submit_scene_reference(asset_id: str, requirements: str, idempotency_key: str) -> dict[str, Any]:
        """Create a job from an original image asset returned by POST /assets."""
        return expected(store.submit, asset_id, requirements, idempotency_key)

    @mcp.tool(structured_output=True)
    def get_scene_job(job_id: str) -> dict[str, Any]:
        """Read job state, source identity and the exact next required action."""
        return expected(store.job, job_id)

    @mcp.tool(structured_output=True)
    def get_scene_build_handoff(job_id: str) -> dict[str, Any]:
        """Read the original annotations, current plan and revision-bound last audit for repair."""
        return expected(store.handoff, job_id)

    @mcp.tool(structured_output=True)
    def suggest_scene_camera(job_id: str, capture_id: str) -> dict[str, Any]:
        """Suggest bounded orthographic camera parameters from trusted capture and original object boxes.

        Does not apply changes, alter geometry or promise visual fidelity. Validate,
        rebuild and recapture the proposed plan before judging the improvement.
        """
        return expected(store.camera_proposal, job_id, capture_id)

    @mcp.tool(structured_output=True)
    def fit_scene_landmarks(job_id: str, source_sha256: str, landmarks: list[dict], base_revision: int) -> dict[str, Any]:
        """Suggest an orthographic angle/scale/placement from manually measured source correspondences.

        Supply 4..128 spatially distributed {id, world:[x,y,z], pixel:[x,y],
        measurement:'measured'|'estimated', evidence} landmarks. Pixel coordinates
        are on the ORIGINAL image, not a resized screenshot. World points are
        caller-supplied geometric correspondences, not automatically recovered
        surfaces. This version-bound advisory does not apply changes or certify
        image fidelity; rebuild and capture the proposed plan before judging it.
        """
        return expected(store.landmark_proposal, job_id, source_sha256, landmarks, base_revision)

    @mcp.tool(structured_output=True)
    def suggest_component_deformation(job_id: str, object_id: str, source_sha256: str,
                                      landmarks: list[dict], base_revision: int) -> dict[str, Any]:
        """Fit bounded local component shape handles to ORIGINAL-image feature points.

        Each landmark is {id, anchor:[x,y,z], pixel:[x,y], measurement, radius,
        evidence}. anchor is the corresponding point on the CURRENT normalized
        root component (x/z -0.5..0.5, y 0..1); pixel is the desired feature on
        the ORIGINAL image in native coordinates. This first advisory supports
        undeformed root assets with orthographic cameras only. It returns a new
        component recipe but never generates/applies it. Generate that recipe,
        rebuild and recapture before accepting any visual improvement.
        """
        job = expected(store.job, job_id)
        if source_sha256 != job["source"]["sha256"]:
            raise ToolError("Deformation landmarks must refer to this job's original image SHA256")
        if type(base_revision) is not int or base_revision != job["plan_revision"]:
            raise ToolError("Stale plan revision for component deformation fitting")
        handoff = expected(store.handoff, job_id)
        if not handoff.get("plan") or not handoff.get("analysis"):
            raise ToolError("Validate a scene plan and source analysis before fitting component deformation")
        objects = [item for item in handoff["plan"]["objects"] if item["id"] == object_id]
        if len(objects) != 1:
            raise ToolError("object_id must identify exactly one current scene object")
        obj = objects[0]
        if obj.get("kind") != "asset" or not obj.get("asset_id"):
            raise ToolError("Component deformation fitting requires a registered asset object")
        component = expected(store.components.get, obj["asset_id"])
        proposal = expected(suggest_deformation_handles, handoff["plan"], handoff["analysis"],
                            obj, component["recipe"], landmarks)
        return proposal | {"job_id":job_id,"source_sha256":source_sha256,
                           "base_revision":base_revision,"analysis_revision":job["analysis_revision"],
                           "base_build_id":job["current_build_id"],"asset_id":obj["asset_id"],
                           "evidence_type":"caller-measured source pixels and current normalized component anchors"}

    @mcp.tool(structured_output=True)
    def save_scene_analysis(job_id: str, analysis: dict, base_revision: int) -> dict[str, Any]:
        """Persist measured source regions, scene crop, confidence and hidden-surface assumptions."""
        return expected(store.save_analysis, job_id, analysis, base_revision)

    @mcp.tool(structured_output=True)
    def validate_scene_plan(job_id: str, plan: dict, analysis_revision: int, base_revision: int) -> dict[str, Any]:
        """Validate and persist an immutable plan; validation does not certify visual similarity."""
        return expected(store.validate, job_id, plan, analysis_revision, base_revision)

    @mcp.tool(structured_output=True)
    def build_threejs_scene(job_id: str, plan_id: str, idempotency_key: str) -> dict[str, Any]:
        """Generate bounded deterministic Three.js files from this job's current validated plan."""
        return expected(store.build, job_id, plan_id, idempotency_key)

    @mcp.tool(structured_output=True)
    async def capture_scene_views(job_id: str, build_id: str) -> dict[str, Any]:
        """Run the managed local development worker against verified build bytes, never an arbitrary URL."""
        result = await asyncio.to_thread(expected, store.capture, job_id, build_id)
        return {k: v for k, v in result.items() if k != "observation"} | {"tests": result["observation"]["tests"], "errors": result["observation"]["errors"]}

    @mcp.tool(structured_output=True)
    def compare_scene_candidate(job_id: str, capture_id: str, baseline_audit_id: str) -> dict[str, Any]:
        """Compare a new signed capture to a prior audited build using source-visible silhouettes.

        The baseline and candidate must belong to the same job and exact source
        annotations. A critical-region regression cannot be hidden by a higher
        global mean. The result is only prefer_candidate / rollback_recommended /
        inconclusive; it never accepts, publishes or certifies a reconstruction.
        """
        return expected(evaluate_scene_candidate, store, job_id, capture_id, baseline_audit_id)

    @mcp.tool(structured_output=True)
    def audit_scene_views(job_id: str, capture_id: str, model_findings: list[dict]) -> dict[str, Any]:
        """Record per-object findings and diagnostic geometry. Completion gates remain fail-closed."""
        return expected(store.audit, job_id, capture_id, model_findings)

    @mcp.tool(structured_output=True)
    def complete_scene_review(job_id: str) -> dict[str, Any]:
        """Explain missing release-grade evidence; this development version never signs completion."""
        job = expected(store.job, job_id)
        return {"job_id": job_id, "build_id": job["current_build_id"], "workflow_complete": False,
                "completion_certificate": None, "blockers": ["production_worker_not_released", "silhouette_and_occlusion_gates_pending", "calibrated_visual_and_device_benchmarks_pending"]}

    @mcp.tool(structured_output=True)
    def export_scene_project(job_id: str, build_id: str, format: str = "zip") -> dict[str, Any]:
        """Return a verified static project ZIP including components; whole-scene GLB is not yet supported."""
        if format != "zip":
            raise ToolError("Only zip scene export is supported. Download individual GLB components via get_scene_component")
        expected(store.build_path, job_id, build_id)
        return {"download_path": f"/builds/{job_id}/{build_id}/project.zip", "workflow_complete": False}

    @mcp.tool(structured_output=True)
    def cancel_scene_job(job_id: str) -> dict[str, Any]:
        """Cancel future work/publication for this job, retaining existing files. Running capture drains."""
        return expected(store.cancel, job_id)

    return mcp


def configured_store():
    return Store(Path(os.getenv("CANVASLAB3D_DATA", str(Path(__file__).resolve().parents[1] / ".canvaslab3d"))))


if __name__ == "__main__":
    create_mcp(configured_store()).run(transport="stdio")
