"""Conservative before/after comparison of source-visible scene evidence.

A higher global score must never hide a meaningful regression in a critical
source region. These diagnostics rank candidates only; they are not calibrated
acceptance thresholds or reconstruction certificates.
"""
from __future__ import annotations

import math
from pathlib import Path

from .visual_metrics import visible_metrics

CRITICAL_REGRESSION_TOLERANCE = 0.005
ANY_REGRESSION_TOLERANCE = 0.03
MIN_MEAN_IMPROVEMENT = 0.005


def _finite_iou(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def compare_visible_quality(baseline: dict, candidate: dict) -> dict:
    """Compare two visible-quality payloads from identical source annotations."""
    if not baseline.get("available") or not candidate.get("available"):
        return {"comparable": False, "reason": "visible_quality_unavailable",
                "recommendation": "inconclusive", "automatic_acceptance": False}
    before = {item["region_id"]: item for item in baseline.get("regions", [])}
    after = {item["region_id"]: item for item in candidate.get("regions", [])}
    if not before or set(before) != set(after):
        raise ValueError("Baseline and candidate visible regions differ; source annotations are not comparable")
    rows = []
    for identity in sorted(before):
        a, b = before[identity], after[identity]
        if bool(a.get("critical")) != bool(b.get("critical")):
            raise ValueError("Region criticality changed between baseline and candidate")
        ai, bi = a.get("silhouette_iou"), b.get("silhouette_iou")
        scored = _finite_iou(ai) and _finite_iou(bi)
        row = {"region_id": identity, "critical": bool(a.get("critical")),
               "baseline_iou": ai, "candidate_iou": bi, "scored": scored}
        if scored:
            delta = bi-ai
            row.update(delta_iou=delta,
                       critical_regression=row["critical"] and delta < -CRITICAL_REGRESSION_TOLERANCE,
                       major_regression=delta < -ANY_REGRESSION_TOLERANCE)
        else:
            row.update(delta_iou=None, critical_regression=False, major_regression=False,
                       reason="missing_or_low_confidence_source_silhouette")
        rows.append(row)
    scored = [row for row in rows if row["scored"]]
    if not scored:
        return {"comparable": False, "reason": "no_shared_scored_silhouettes", "regions": rows,
                "recommendation": "inconclusive", "automatic_acceptance": False}
    baseline_mean = sum(row["baseline_iou"] for row in scored)/len(scored)
    candidate_mean = sum(row["candidate_iou"] for row in scored)/len(scored)
    critical_scored = [row for row in scored if row["critical"]]
    critical_regressions = [row["region_id"] for row in scored if row["critical_regression"]]
    major_regressions = [row["region_id"] for row in scored if row["major_regression"]]
    missing_critical = [row["region_id"] for row in rows if row["critical"] and not row["scored"]]
    delta_mean = candidate_mean-baseline_mean
    if critical_regressions or major_regressions:
        recommendation = "rollback_recommended"
    elif missing_critical:
        recommendation = "inconclusive"
    elif delta_mean >= MIN_MEAN_IMPROVEMENT:
        recommendation = "prefer_candidate"
    else:
        recommendation = "inconclusive"
    return {"comparable": True, "measurement": "per-region visible silhouette IoU delta on identical source annotations",
            "regions": rows, "scored_region_count": len(scored),
            "scored_critical_region_count": len(critical_scored),
            "baseline_mean_iou": baseline_mean, "candidate_mean_iou": candidate_mean,
            "mean_delta_iou": delta_mean, "critical_regressions": critical_regressions,
            "major_regressions": major_regressions, "unscored_critical_regions": missing_critical,
            "recommendation": recommendation, "automatic_acceptance": False,
            "thresholds": {"critical_regression_tolerance": CRITICAL_REGRESSION_TOLERANCE,
                           "any_regression_tolerance": ANY_REGRESSION_TOLERANCE,
                           "minimum_mean_improvement": MIN_MEAN_IMPROVEMENT},
            "warning": "Candidate ranking only. Rebuild/capture evidence and model review remain required; thresholds are diagnostic, not calibrated release gates."}


def evaluate_scene_candidate(store, job_id: str, capture_id: str, baseline_audit_id: str) -> dict:
    """Compare current signed capture to a prior audited build from the same job/analysis."""
    with store.transaction() as db:
        job = store.get(db, "job", job_id)
        capture = store.checked_capture(db, job, capture_id)
        if capture["observation"].get("errors") or any(not test["passed"] for test in capture["observation"].get("tests", [])):
            raise ValueError("Fix candidate capture runtime or interaction failures before fidelity comparison")
        baseline = store.get(db, "audit", baseline_audit_id)
        if baseline.get("job_id") != job_id:
            raise ValueError("Baseline audit belongs to a different job")
        if baseline.get("analysis_id") != job.get("analysis_id"):
            raise ValueError("Baseline annotations differ from the current source analysis; do not compare them")
        if baseline.get("build_id") == capture.get("build_id"):
            raise ValueError("Candidate must be a different build from the baseline audit")
        baseline_quality = baseline.get("visible_quality")
        if not isinstance(baseline_quality, dict):
            raise ValueError("Baseline audit has no visible-quality evidence")
        plan = store.get(db, "plan", job["current_plan_id"])["data"]
        analysis = store.get(db, "analysis", job["analysis_id"])["data"]
        id_path = Path(store.root) / "captures" / capture_id / "id.png"
        candidate_quality = visible_metrics(plan, analysis, id_path, capture["observation"].get("id_encoding"))
        comparison = compare_visible_quality(baseline_quality, candidate_quality)
        return comparison | {"job_id": job_id, "baseline_audit_id": baseline_audit_id,
                             "baseline_build_id": baseline["build_id"], "candidate_capture_id": capture_id,
                             "candidate_build_id": capture["build_id"], "analysis_id": job["analysis_id"],
                             "plan_id": job["current_plan_id"], "applied": False, "workflow_complete": False,
                             "candidate_visible_quality": candidate_quality}
