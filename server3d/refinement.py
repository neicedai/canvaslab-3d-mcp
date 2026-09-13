"""Bounded generate/build/capture/compare trials; the original job is never edited."""
from __future__ import annotations

import asyncio
import copy
import subprocess
import time
import uuid

from .deformation_fit import suggest_deformation_handles
from .fidelity_compare import evaluate_scene_candidate, measure_scene_fidelity
from .fidelity_evidence import verified_capture


def refine_component_candidates(store, job_id, object_id, source_sha256, landmarks, base_revision,
                                baseline_audit_id, max_candidates=1):
    """Try at most three strengths in child jobs, keeping all attempted evidence.

    Requires manually supplied anchors and a current audited baseline. This is
    NOT automatic feature matching or acceptance, and never changes the source
    job's plan/build. Generation and capture use only existing managed workers.
    """
    if type(max_candidates) is not int or not 1 <= max_candidates <= 3:
        raise ValueError("max_candidates must be an integer from 1 to 3")
    with store.transaction() as db:
        origin = store.get(db, "job", job_id)
        store.writable(origin)
        if source_sha256 != origin["source"]["sha256"]:
            raise ValueError("Refinement requires the exact original source SHA256")
        if type(base_revision) is not int or base_revision != origin["plan_revision"]:
            raise ValueError("Stale plan revision for refinement")
        audit = store.get(db, "audit", baseline_audit_id)
        if (audit["job_id"] != job_id or audit["build_id"] != origin["current_build_id"]
                or audit.get("plan_id") != origin["current_plan_id"]):
            raise ValueError("Refinement baseline must audit this job's current build")
        verified = verified_capture(store, db, audit["capture_id"], current_job=origin)
        plan, analysis = verified["plan"]["data"], verified["analysis"]
        objects = [o for o in plan["objects"] if o["id"] == object_id and o["kind"] == "asset"]
        if len(objects) != 1:
            raise ValueError("Select exactly one registered component object")
        component = store.components.get(objects[0]["asset_id"], db)
    proposal = suggest_deformation_handles(plan, analysis, objects[0], component["recipe"], landmarks)
    result = {"run_id":uuid.uuid4().hex, "job_id":job_id, "original_build_id":origin["current_build_id"],
              "baseline_audit_id":baseline_audit_id, "source_sha256":source_sha256, "object_id":object_id,
              "candidates":[], "recommended_job_id":job_id, "recommended_build_id":origin["current_build_id"],
              "outcome":"retained_original", "original_job_unchanged":True,
              "automatic_acceptance":False, "workflow_complete":False, "proposal":proposal}
    if proposal["no_op"]:
        result["outcome"] = "no_change_needed_at_supplied_landmarks"
        return result
    baseline_metrics = measure_scene_fidelity(store, job_id, audit["capture_id"])
    if (any(r.get("silhouette_iou") is None for r in baseline_metrics["visible_quality"].get("regions", []) if r["critical"])
            or not baseline_metrics["visible_quality"].get("available")
            or any(not r["available"] for r in baseline_metrics["appearance"]["regions"] if r["critical"])):
        raise ValueError("Annotate every critical source silhouette before automatic candidate trials")
    status = store.status()
    if not status.get("runtime_ready") or status.get("restart_required") or not status.get("blender", {}).get("available"):
        raise ValueError("Rebuild/restart the runtime and enable the managed Blender worker before trials")
    lease_key, owner = "refinement:"+job_id, result["run_id"]
    with store.transaction() as db:
        row = db.execute("SELECT payload FROM records WHERE kind='lease' AND id=?", (lease_key,)).fetchone()
        if row:
            import json
            if json.loads(row[0])["expires_at"] > time.time():
                raise ValueError("A refinement trial is already running for this job")
        store.put(db, "lease", lease_key, {"owner":owner, "expires_at":time.time()+1800})
    best_score = -float("inf")
    try:
        gains = {1:(.5,), 2:(.5, 1.), 3:(.35, .65, 1.)}[max_candidates]
        for index, gain in enumerate(gains):
            latest = store.job(job_id)
            store.writable(latest)
            if (latest["current_plan_id"], latest["analysis_id"]) != (origin["current_plan_id"], origin["analysis_id"]):
                raise ValueError("Original changed during refinement; child evidence retained, no original plan overwritten")
            row = {"gain":gain, "status":"started"}
            result["candidates"].append(row)
            try:
                recipe = copy.deepcopy(proposal["proposed_recipe"])
                for handle in recipe["deformation_handles"]:
                    handle["strength"] *= gain
                asset = store.components.generate(recipe, owner+":"+str(index))
                child = store.submit(origin["source"]["asset_id"],
                                     "Source-guided component trial from baseline " + origin["current_build_id"],
                                     owner+":"+str(index))
                child_id = child["job_id"]
                row["job_id"] = child_id
                child_analysis = copy.deepcopy(analysis)
                child_analysis["change_reason"] = "Bounded refinement trial " + owner
                store.save_analysis(child_id, child_analysis, 0)
                child_plan = copy.deepcopy(plan)
                for obj in child_plan["objects"]:
                    if obj["id"] == object_id:
                        obj["asset_id"] = asset["asset_id"]
                validated = store.validate(child_id, child_plan, 1, 0)
                build = store.build(child_id, validated["plan_id"], owner+":"+str(index))
                row["build_id"] = build["build_id"]
                capture = store.capture(child_id, build["build_id"])
                row["capture_id"] = capture["capture_id"]
                comparison = evaluate_scene_candidate(store, child_id, capture["capture_id"], baseline_audit_id)
                row.update(status="compared", comparison=comparison)
                if comparison["recommendation"] == "prefer_candidate":
                    score = comparison["mean_delta_iou"] - comparison["appearance_comparison"]["mean_loss_delta"]
                    row["diagnostic_rank"] = score
                    if score > best_score:
                        best_score = score
                        result.update(recommended_job_id=child_id, recommended_build_id=build["build_id"],
                                      outcome="candidate_available_for_review")
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                row.update(status="failed", reason=str(exc)[:2000])
            with store.transaction() as db:
                store.put(db, "refinement", owner, result)
        latest = store.job(job_id)
        store.writable(latest)
        if (latest["current_plan_id"], latest["current_build_id"], latest["analysis_id"]) != (
                origin["current_plan_id"], origin["current_build_id"], origin["analysis_id"]):
            result.update(original_job_unchanged=False, outcome="origin_changed_review_required",
                          recommended_job_id=None, recommended_build_id=None)
        result["next_action"] = "Inspect original/reference/alternate-view captures; explicitly adopt a candidate only after review. No original was replaced."
        with store.transaction() as db:
            store.put(db, "refinement", owner, result)
        return result
    finally:
        with store.transaction() as db:
            row = db.execute("SELECT payload FROM records WHERE kind='lease' AND id=?", (lease_key,)).fetchone()
            if row:
                import json
                if json.loads(row[0])["owner"] == owner:
                    db.execute("DELETE FROM records WHERE kind='lease' AND id=?", (lease_key,))


def install_refinement_tools(mcp, store, expected):
    @mcp.tool(structured_output=True)
    async def measure_scene_fidelity(job_id: str, capture_id: str) -> dict:
        """Measure actual ORIGINAL pixels, fine edges and silhouette; return native worst-patch boxes.

        Requires a current signed reference capture with passing interactions.
        A low loss is not a fidelity certificate. Images are never aligned or
        recolored to improve the score, and only source-owned masks are used.
        """
        from .fidelity_compare import measure_scene_fidelity as measure
        return await asyncio.to_thread(expected, measure, store, job_id, capture_id)

    @mcp.tool(structured_output=True)
    async def refine_scene_component(job_id: str, object_id: str, source_sha256: str,
                                     landmarks: list[dict], base_revision: int,
                                     baseline_audit_id: str, max_candidates: int = 1) -> dict:
        """Run 1..3 bounded Blender/build/capture/compare trials in isolated child jobs.

        Supply current root-asset anchors and ORIGINAL native target pixels as
        for suggest_component_deformation. Needs an undeformed component, an
        orthographic camera, a fresh audited baseline, critical source masks,
        working Blender and Chrome. Default tries one half-strength proposal.
        Trials may be expensive. Original job never changes; return a candidate
        for review, or retain the original if no safe improvement is measured.
        """
        return await asyncio.to_thread(expected, refine_component_candidates, store, job_id, object_id,
                                       source_sha256, landmarks, base_revision, baseline_audit_id, max_candidates)
