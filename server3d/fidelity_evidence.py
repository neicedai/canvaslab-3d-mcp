"""Bind comparisons to original bytes, immutable plans and signed captures.

A baseline may be an older audited capture or another job. Its signature and
files remain mandatory; age is ignored ONLY for historical comparison, never
for current capture acceptance. No cached audit score is trusted as evidence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re

from .scene_schema import validate_analysis


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def reference_identity(analysis, source):
    """Ignore narration/revision counters, not any measurement or confidence."""
    data = validate_analysis(analysis, source).model_dump()
    fields = ("id", "box", "critical", "confidence", "visible_polygons", "visible_holes")
    semantic = {"source_sha256":data["source_sha256"], "scene_box":data["scene_box"],
                "source_size":[source["width"], source["height"]],
                "regions":[{k:r[k] for k in fields} for r in sorted(data["regions"], key=lambda r:r["id"])]}
    return sha(canonical(semantic))


def _id(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{%d}" % length, value):
        raise ValueError("Invalid evidence identity")
    return value


def verified_capture(store, db, capture_id, *, current_job=None):
    """Verify historical and current evidence without mutating any job."""
    _id(capture_id, 32)
    capture = (store.checked_capture(db, current_job, capture_id) if current_job is not None
               else store.get(db, "capture", capture_id))
    signed = {k:v for k, v in capture.items() if k != "signature"}
    signature = hmac.new(store.secret, canonical(signed), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(capture.get("signature", ""), signature):
        raise ValueError("Invalid historical capture signature")
    if capture.get("capture_id") != capture_id:
        raise ValueError("Capture identity mismatch")
    job_id, build_id = _id(capture["job_id"], 32), _id(capture["build_id"], 64)
    build = store.get(db, "build", build_id)
    if build["job_id"] != job_id or build["build_id"] != build_id:
        raise ValueError("Capture and build ownership differ")
    directory = Path(store.root)/"builds"/job_id/build_id
    store.verify_build_record(directory, build)
    plan = store.get(db, "plan", build["plan_id"])
    analysis = store.get(db, "analysis", plan["analysis_id"])
    if (plan["plan_id"] != build["plan_id"] or plan["job_id"] != job_id or
            analysis["job_id"] != job_id or build["manifest"]["plan_id"] != plan["plan_id"]):
        raise ValueError("Build/plan/analysis identity mismatch")
    for filename, data in (("scene.json", plan["data"]), ("reference-annotations.json", analysis["data"])):
        if json.loads((directory/filename).read_text(encoding="utf-8")) != data:
            raise ValueError("Saved plan or analysis differs from immutable build bytes")
    evidence_dir = Path(store.root)/"captures"/capture_id
    files = capture["files"]
    if not {"reference.png", "id.png", "observation.json"}.issubset(files):
        raise ValueError("Capture lacks original-view image/ID/observation evidence")
    for name, digest in files.items():
        if Path(name).name != name or "\\" in name or name.startswith("."):
            raise ValueError("Invalid capture evidence filename")
        payload = evidence_dir/name
        if not payload.is_file() or sha(payload.read_bytes()) != digest:
            raise ValueError("Capture evidence has changed")
    observation = capture["observation"]
    if json.loads((evidence_dir/"observation.json").read_text(encoding="utf-8")) != observation:
        raise ValueError("Capture observation differs from signed evidence")
    tests = observation.get("tests")
    if (not isinstance(tests, list) or not tests or any(t.get("passed") is not True for t in tests)
            or observation.get("errors") != []):
        raise ValueError("Capture requires passing runtime/interaction checks before fidelity comparison")
    reference = observation.get("reference", {})
    if (reference.get("build_id") != build_id or reference.get("playing") is not False
            or reference.get("dusk") is not False or reference.get("time") != 0):
        raise ValueError("Comparison requires the captured static original/day view")
    source = build["manifest"]["source"]
    source_path = Path(store.root)/"assets"/_id(source["sha256"], 64)
    if not source_path.is_file() or sha(source_path.read_bytes()) != source["sha256"]:
        raise ValueError("Original source bytes changed or are missing")
    size = [int(v+.5) for v in analysis["data"]["scene_box"][2:]]
    if observation.get("native_size") != size:
        raise ValueError("Capture native size differs from original crop")
    return {"capture":capture, "plan":plan, "analysis":analysis["data"], "source":source,
            "analysis_id":plan["analysis_id"], "source_path":source_path, "directory":evidence_dir,
            "reference_identity":reference_identity(analysis["data"], source)}
