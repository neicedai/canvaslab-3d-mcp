"""Compare actual source-visible evidence across immutable builds and jobs."""
from __future__ import annotations

import math

from .visual_metrics import visible_metrics
from .fidelity_evidence import verified_capture
from .reference_metrics import measure_reference_appearance, compare_appearance

CRITICAL_REGRESSION_TOLERANCE = 0.005
ANY_REGRESSION_TOLERANCE = 0.03
MIN_MEAN_IMPROVEMENT = 0.005


def _finite_iou(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def compare_visible_quality(baseline: dict, candidate: dict) -> dict:
    """Diagnostic silhouette ranking; service also checks fine appearance below."""
    if not baseline.get("available") or not candidate.get("available"):
        return {"comparable":False, "reason":"visible_quality_unavailable",
                "recommendation":"inconclusive", "automatic_acceptance":False}
    before = {r["region_id"]:r for r in baseline.get("regions", [])}
    after = {r["region_id"]:r for r in candidate.get("regions", [])}
    if (not before or set(before) != set(after) or len(before) != len(baseline["regions"])
            or len(after) != len(candidate["regions"])):
        raise ValueError("Baseline and candidate visible regions differ or contain duplicates")
    rows = []
    for identity in sorted(before):
        a, b = before[identity], after[identity]
        if bool(a.get("critical")) != bool(b.get("critical")):
            raise ValueError("Region criticality changed between baseline and candidate")
        ai, bi = a.get("silhouette_iou"), b.get("silhouette_iou")
        scored = _finite_iou(ai) and _finite_iou(bi)
        delta = bi-ai if scored else None
        rows.append({"region_id":identity, "critical":bool(a.get("critical")),
                     "baseline_iou":ai, "candidate_iou":bi, "scored":scored, "delta_iou":delta,
                     "critical_regression":scored and bool(a.get("critical")) and delta < -CRITICAL_REGRESSION_TOLERANCE,
                     "major_regression":scored and delta < -ANY_REGRESSION_TOLERANCE})
    scored = [r for r in rows if r["scored"]]
    if not scored:
        return {"comparable":False, "reason":"no_shared_scored_silhouettes", "regions":rows,
                "recommendation":"inconclusive", "automatic_acceptance":False}
    before_mean = sum(r["baseline_iou"] for r in scored)/len(scored)
    after_mean = sum(r["candidate_iou"] for r in scored)/len(scored)
    critical = [r["region_id"] for r in scored if r["critical_regression"]]
    major = [r["region_id"] for r in scored if r["major_regression"]]
    missing = [r["region_id"] for r in rows if r["critical"] and not r["scored"]]
    recommendation = ("rollback_recommended" if critical or major else "inconclusive" if missing
                      else "prefer_candidate" if after_mean-before_mean >= MIN_MEAN_IMPROVEMENT else "inconclusive")
    return {"comparable":True, "measurement":"per-region visible silhouette IoU on identical source annotations",
            "regions":rows, "scored_region_count":len(scored),
            "scored_critical_region_count":sum(r["critical"] for r in scored),
            "baseline_mean_iou":before_mean, "candidate_mean_iou":after_mean,
            "mean_delta_iou":after_mean-before_mean, "critical_regressions":critical,
            "major_regressions":major, "unscored_critical_regions":missing,
            "recommendation":recommendation, "automatic_acceptance":False,
            "thresholds":{"critical_regression_tolerance":CRITICAL_REGRESSION_TOLERANCE,
                          "any_regression_tolerance":ANY_REGRESSION_TOLERANCE,
                          "minimum_mean_improvement":MIN_MEAN_IMPROVEMENT},
            "warning":"Diagnostic ranking only; these thresholds are not calibrated release gates."}


def _quality(evidence):
    return visible_metrics(evidence["plan"]["data"], evidence["analysis"], evidence["directory"]/"id.png",
                           evidence["capture"]["observation"].get("id_encoding"))


def _appearance(evidence):
    return measure_reference_appearance(evidence["source_path"], evidence["directory"]/"reference.png", evidence["analysis"])


def evaluate_scene_candidate(store, job_id: str, capture_id: str, baseline_audit_id: str) -> dict:
    """Cross-job comparison is legal ONLY for identical source measurements.

    Recompute baseline metrics from immutable signed evidence (including holes),
    not an old audit's cached scores. Expired baselines remain historical only.
    """
    with store.transaction() as db:
        job = store.get(db, "job", job_id)
        current = verified_capture(store, db, capture_id, current_job=job)
        baseline = store.get(db, "audit", baseline_audit_id)
        old = verified_capture(store, db, baseline["capture_id"])
        if (baseline["job_id"] != old["capture"]["job_id"] or baseline["build_id"] != old["capture"]["build_id"]
                or baseline.get("analysis_id") != old["analysis_id"] or baseline.get("plan_id") != old["plan"]["plan_id"]):
            raise ValueError("Baseline audit is not bound to its captured build and source analysis")
        if old["reference_identity"] != current["reference_identity"]:
            raise ValueError("Source SHA256, crop or measured regions/holes/confidence differ")
        if old["capture"]["build_id"] == current["capture"]["build_id"]:
            raise ValueError("Candidate must be a different build from the baseline audit")
        # Release SQLite before bounded pixel analysis; check revision again below.
        revision = (job["current_plan_id"], job["current_build_id"], job["analysis_id"])
    baseline_quality, candidate_quality = _quality(old), _quality(current)
    shape = compare_visible_quality(baseline_quality, candidate_quality)
    baseline_appearance, candidate_appearance = _appearance(old), _appearance(current)
    appearance = compare_appearance(baseline_appearance, candidate_appearance)
    choices = (shape["recommendation"], appearance["recommendation"])
    complete = (shape["comparable"] and appearance["comparable"] and
                not shape.get("unscored_critical_regions") and not appearance.get("unscored_critical_regions"))
    recommendation = ("rollback_recommended" if "rollback_recommended" in choices else
                      "prefer_candidate" if complete and "prefer_candidate" in choices else "inconclusive")
    with store.transaction() as db:
        latest = store.get(db, "job", job_id)
        store.writable(latest)
        if (latest["current_plan_id"], latest["current_build_id"], latest["analysis_id"]) != revision:
            raise ValueError("Candidate changed during comparison; recapture the current build")
    return shape | {"comparable":shape["comparable"] and appearance["comparable"],
                    "recommendation":recommendation, "silhouette_recommendation":shape["recommendation"],
                    "appearance_comparison":appearance, "baseline_appearance":baseline_appearance,
                    "candidate_appearance":candidate_appearance, "candidate_visible_quality":candidate_quality,
                    "job_id":job_id, "baseline_job_id":baseline["job_id"], "baseline_audit_id":baseline_audit_id,
                    "baseline_build_id":baseline["build_id"], "candidate_capture_id":capture_id,
                    "candidate_build_id":current["capture"]["build_id"], "analysis_id":revision[2],
                    "plan_id":revision[0], "reference_identity":current["reference_identity"],
                    "cross_job":baseline["job_id"] != job_id, "baseline_recomputed":True,
                    "applied":False, "automatic_acceptance":False, "workflow_complete":False}


def measure_scene_fidelity(store, job_id, capture_id):
    """Return source-visible shape, fine appearance and native worst-patch coordinates."""
    with store.transaction() as db:
        job = store.get(db, "job", job_id)
        evidence = verified_capture(store, db, capture_id, current_job=job)
    quality, appearance = _quality(evidence), _appearance(evidence)
    with store.transaction() as db:
        latest = store.get(db, "job", job_id)
        store.writable(latest)
        if (latest["current_build_id"] != evidence["capture"]["build_id"] or
                latest["current_plan_id"] != evidence["plan"]["plan_id"]):
            raise ValueError("Scene changed during measurement; use a fresh current capture")
    return {"job_id":job_id, "build_id":evidence["capture"]["build_id"], "capture_id":capture_id,
            "reference_identity":evidence["reference_identity"], "visible_quality":quality,
            "appearance":appearance, "workflow_complete":False, "automatic_acceptance":False}
