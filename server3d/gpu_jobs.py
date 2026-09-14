"""Opt-in durable local GPU queue. Workers are trusted local operators, not remote uploads.

No torch dependency in the MCP process. Inference produces advisory evidence;
never mutates source annotations, a scene plan, components or build acceptance.
"""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import re
import time
import uuid
from typing import Annotated, Literal

from PIL import Image
from pydantic import Field, model_validator
from .builder import canonical, sha
from .scene_schema import Strict, Id
from gpu3d import CONTRACT
from gpu3d.models import MODELS

MAX_PIXELS = 4_194_304
MAX_TASKS = 256
QUEUE_TTL = 900
HEARTBEAT_TTL = 45
TASK_TIMEOUT = 600
OUTPUTS = {"depth": {"depth.npy", "depth.png", "edges.png", "result.json"},
           "segment": {"mask.png", "edges.png", "result.json"}}


class PromptPoint(Strict):
    x: float = Field(ge=0, le=8192)
    y: float = Field(ge=0, le=8192)
    label: Literal[0, 1]


class VisionRequest(Strict):
    operation: Literal["depth", "segment"]
    execution: Literal["cuda", "cpu"] = "cuda"
    region_id: Id | None = None
    points: list[PromptPoint] = Field(default_factory=list, max_length=16)
    depth_input_side: Literal[518, 784] = 518

    @model_validator(mode="after")
    def prompt_contract(self):
        if self.operation == "segment" and not self.region_id:
            raise ValueError("segment requires a source region_id; its measured box is the prompt")
        if self.operation == "depth" and (self.points or self.region_id):
            raise ValueError("depth processes the scene crop; it does not accept segmentation prompts")
        if self.operation == "segment" and self.depth_input_side != 518:
            raise ValueError("depth_input_side is only configurable for depth")
        return self


def enabled() -> bool:
    return os.getenv("CANVASLAB3D_GPU_ENABLED", "0") == "1"


def source_image(store, snapshot: dict) -> Image.Image:
    source = snapshot["source"]
    payload = (store.root / "assets" / source["asset_id"]).read_bytes()
    if sha(payload) != source["sha256"]:
        raise ValueError("GPU original image changed")
    with Image.open(io.BytesIO(payload)) as image:
        if (list(image.size) != [source["width"], source["height"]]
                or image.getexif().get(274, 1) != 1 or image.info.get("icc_profile")
                or "A" in image.getbands() or "transparency" in image.info
                or getattr(image, "n_frames", 1) != 1):
            raise ValueError("GPU input needs an opaque, orientation-explicit source without an unhandled ICC profile")
        x, y, w, h = snapshot["analysis"]["scene_box"]
        if (any(v != int(v) for v in (x,y,w,h)) or w*h > MAX_PIXELS
                or min(w,h) < 64 or max(w,h) > 4096):
            raise ValueError("GPU scene crop must be integer, 64..4096 per edge and <=4,194,304 pixels")
        return image.convert("RGB").crop((int(x), int(y), int(x+w), int(y+h)))


class GPUQueue:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "gpu-work"

    def _rows(self, db, kind):
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind=?", (kind,))]

    def _current(self, db, task):
        job = self.store.get(db, "job", task["job_id"])
        return (job["state"] != "cancelled" and job.get("analysis_id") == task["analysis_id"]
                and job["source"]["sha256"] == task["source_sha256"])

    def _reap(self, db):
        now = time.time()
        for task in self._rows(db, "gpu_task"):
            if task["state"] not in {"queued", "running"}:
                continue
            reason = None
            if not self._current(db, task):
                reason = "source_analysis_changed_or_job_cancelled"
            elif task["state"] == "queued" and now > task["created_at"] + QUEUE_TTL:
                reason = "queue_wait_timeout"
            elif task["state"] == "running" and (now > task["deadline"] or now > task["heartbeat"] + HEARTBEAT_TTL):
                reason = "worker_lost_or_task_timeout"
            if reason:
                # Never blindly reclaim a GPU task; an old process may still be alive.
                task.update(state="failed", reason=reason, finished_at=now)
                self.store.put(db, "gpu_task", task["task_id"], task)

    def register(self, worker_id: str, identity: dict, operations: list[str]):
        if not operations or set(operations) - set(MODELS):
            raise ValueError("Unknown worker operation")
        with self.store.transaction() as db:
            self.store.put(db, "gpu_worker", worker_id, {"worker_id": worker_id, "identity": identity,
                "operations": operations, "last_seen": time.time(), "active_task": None,
                "contract": CONTRACT, "models": {op: MODELS[op] for op in operations}})

    def status(self):
        with self.store.transaction() as db:
            self._reap(db)
            workers = self._rows(db, "gpu_worker")
            counts = {}
            for task in self._rows(db, "gpu_task"):
                counts[task["state"]] = counts.get(task["state"], 0)+1
        for worker in workers:
            worker["online"] = time.time()-worker["last_seen"] <= HEARTBEAT_TTL
        return {"enabled": enabled(), "contract": CONTRACT, "workers": workers, "task_counts": counts,
                "model_lifecycle": "one_inference_subprocess_per_task",
                "idle_cuda_context": False, "release_policy": "child_exit_after_success_failure_or_cancellation",
                "supported_operations": list(MODELS), "precision": "float32", "max_inflight_per_device": 1,
                "combined_vram": False, "image_to_mesh_enabled": False, "automatic_scene_mutation": False}

    def submit(self, job_id, request, analysis_revision, source_sha256, idempotency_key):
        if not enabled():
            raise ValueError("GPU processing disabled; operator must set CANVASLAB3D_GPU_ENABLED=1")
        request = VisionRequest.model_validate(request).model_dump()
        with self.store.transaction() as db:
            self._reap(db)
            job = self.store.get(db, "job", job_id)
            self.store.writable(job)
            if source_sha256 != job["source"]["sha256"]:
                raise ValueError("GPU request must name the exact original SHA256")
            if type(analysis_revision) is not int or analysis_revision != job["analysis_revision"] or not job.get("analysis_id"):
                raise ValueError("Save source analysis and supply its current revision before GPU processing")
            analysis = self.store.get(db, "analysis", job["analysis_id"])["data"]
            sx, sy, sw, sh = analysis["scene_box"]
            if request["region_id"] not in {None, *[r["id"] for r in analysis["regions"]]}:
                raise ValueError("Unknown source segmentation region")
            if any(not sx <= p["x"] < sx+sw or not sy <= p["y"] < sy+sh for p in request["points"]):
                raise ValueError("Prompt points must use ORIGINAL native coordinates inside scene crop")
            fingerprint = sha(canonical([CONTRACT, job_id, job["analysis_id"], source_sha256, request, MODELS[request["operation"]]]))
            previous = self.store.retry(db, "gpu", idempotency_key, fingerprint)
            if previous:
                return self._get(db, previous["task_id"])
            snapshot = {"source": copy.deepcopy(job["source"]), "analysis": copy.deepcopy(analysis)}
            source_image(self.store, snapshot).close()  # Decode/check before admitting work.
            workers = self._rows(db, "gpu_worker")
            if not any(time.time()-w["last_seen"] <= HEARTBEAT_TTL and request["operation"] in w["operations"]
                       and w["identity"]["execution"] == request["execution"] and w.get("contract") == CONTRACT
                       and w.get("models", {}).get(request["operation"]) == MODELS[request["operation"]] for w in workers):
                raise ValueError("No online compatible worker; no implicit CPU fallback or queued success")
            tasks = self._rows(db, "gpu_task")
            if len(tasks) >= MAX_TASKS or sum(t["state"] in {"queued", "running"} for t in tasks) >= 64:
                raise ValueError("GPU queue quota reached; archive managed records before resubmitting")
            task = {"task_id": uuid.uuid4().hex, "job_id": job_id, "analysis_id": job["analysis_id"],
                    "analysis_revision": analysis_revision, "source_sha256": source_sha256,
                    "request": request, "snapshot": snapshot, "fingerprint": fingerprint, "contract": CONTRACT,
                    "model": MODELS[request["operation"]], "state": "queued", "created_at": time.time(),
                    "files": {}, "result": None, "advisory_only": True, "workflow_complete": False}
            self.store.put(db, "gpu_task", task["task_id"], task)
            self.store.retry(db, "gpu", idempotency_key, fingerprint, {"task_id": task["task_id"]})
            return self._get(db, task["task_id"])

    def claim(self, worker_id):
        with self.store.transaction() as db:
            self._reap(db)
            worker = self.store.get(db, "gpu_worker", worker_id)
            worker.update(last_seen=time.time())
            tasks = self._rows(db, "gpu_task")
            if any(t["state"] == "running" and t.get("worker_id") == worker_id for t in tasks):
                raise ValueError("Worker already owns an active task")
            selected = None
            worker["active_task"] = None
            if enabled():
                for task in sorted(tasks, key=lambda t: t["created_at"]):
                    req = task["request"]
                    if (task["state"] == "queued" and req["operation"] in worker["operations"]
                            and req["execution"] == worker["identity"]["execution"]
                            and task["contract"] == CONTRACT and task["model"] == MODELS[req["operation"]]):
                        selected = task
                        task.update(state="running", worker_id=worker_id, claim_token=uuid.uuid4().hex,
                                    heartbeat=time.time(), deadline=time.time()+TASK_TIMEOUT, started_at=time.time())
                        worker["active_task"] = task["task_id"]
                        self.store.put(db, "gpu_task", task["task_id"], task)
                        break
            self.store.put(db, "gpu_worker", worker_id, worker)
            return selected

    def heartbeat(self, worker_id, task_id=None, token=None):
        with self.store.transaction() as db:
            self._reap(db)
            worker = self.store.get(db, "gpu_worker", worker_id)
            worker["last_seen"] = time.time()
            self.store.put(db, "gpu_worker", worker_id, worker)
            if task_id is None:
                return True
            task = self.store.get(db, "gpu_task", task_id)
            valid = (enabled() and task["state"] == "running" and task.get("worker_id") == worker_id
                     and task.get("claim_token") == token and self._current(db, task))
            if valid:
                task["heartbeat"] = time.time()
                self.store.put(db, "gpu_task", task_id, task)
            return valid

    def finish(self, task_id, token, result=None, reason=None):
        with self.store.transaction() as db:
            self._reap(db)
            task = self.store.get(db, "gpu_task", task_id)
            if task["state"] != "running" or task.get("claim_token") != token:
                return False
            if reason:
                task.update(state="failed", reason=str(reason)[:1000])
            else:
                from gpu3d.outputs import validate_outputs
                folder = self.root / task_id / "output"
                parsed, files = validate_outputs(folder, task)
                worker_identity = self.store.get(db, "gpu_worker", task["worker_id"])["identity"]
                runtime = parsed.get("metadata", {}).get("runtime", {})
                if (runtime.get("execution") != worker_identity["execution"]
                        or runtime.get("device_key") != worker_identity["device_key"]):
                    raise ValueError("GPU result does not match the admitted physical device")
                if result is not None and parsed != result:
                    raise ValueError("GPU result differs from on-disk evidence")
                task.update(state="succeeded", result=parsed, files=files)
            task["finished_at"] = time.time()
            self.store.put(db, "gpu_task", task_id, task)
            worker = self.store.get(db, "gpu_worker", task["worker_id"])
            worker.update(active_task=None, last_seen=time.time())
            self.store.put(db, "gpu_worker", worker["worker_id"], worker)
            return True

    def _get(self, db, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Invalid GPU task ID")
        task = self.store.get(db, "gpu_task", task_id)
        if task["state"] == "succeeded":
            for name, digest in task["files"].items():
                if name not in OUTPUTS[task["request"]["operation"]] or sha((self.root/task_id/"output"/name).read_bytes()) != digest:
                    raise ValueError("GPU evidence changed")
        return {k: v for k, v in task.items() if k not in {"snapshot", "claim_token"}} | {
            "matches_current_analysis": self._current(db, task),
            "downloads": {n: f"/gpu-tasks/{task_id}/{n}" for n in task["files"]},
            "next_action": "Review predicted depth/mask/edges against original; GPU predictions are not measured annotations or 3D geometry."}

    def get(self, task_id):
        with self.store.transaction() as db:
            self._reap(db)
            return self._get(db, task_id)

    def cancel(self, task_id):
        with self.store.transaction() as db:
            self._get(db, task_id)
            task = self.store.get(db, "gpu_task", task_id)
            if task["state"] in {"queued", "running"}:
                task.update(state="cancelled", finished_at=time.time(), reason="cancelled_by_caller")
                self.store.put(db, "gpu_task", task_id, task)
            return self._get(db, task_id)

    def artifact(self, task_id, name):
        task = self.get(task_id)
        if name not in task["files"]:
            raise ValueError("Unknown GPU artifact")
        return self.root / task_id / "output" / name
