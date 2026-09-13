"""Transactional metadata and immutable revisions; no access to 2D job storage."""
from __future__ import annotations

import hashlib
import copy
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import CONTRACT, VERSION
from .builder import REPO, build_files, canonical, runtime_files, sha, verify_build, write_build
from .scene_schema import validate_analysis, validate_plan, ScenePlan, Analysis
from .camera_fit import suggest_camera
from .visual_metrics import visible_metrics
from .components import ComponentLibrary

MAX_UPLOAD = 20 * 1024 * 1024
# Freeze at module load. Reading source files on every status call could falsely
# report a new on-disk revision while an old Python process is still running.
LOADED_SOURCE_DIGEST = sha(canonical({p.name: sha(p.read_bytes()) for p in sorted((REPO / "server3d").glob("*.py"))}))


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.capture_slot = threading.Lock()
        self.db = self.root / "state.sqlite3"
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS retries(scope TEXT,key TEXT,fingerprint TEXT,result TEXT,PRIMARY KEY(scope,key))")
        self.key_path = self.root / "capture.key"
        try:
            with self.key_path.open("xb") as f:
                f.write(secrets.token_bytes(32))
            self.key_path.chmod(0o600)
        except FileExistsError:
            pass
        self.secret = self.key_path.read_bytes()
        if len(self.secret) != 32:
            raise ValueError("Invalid capture signing key")
        self.components = ComponentLibrary(self)

    @contextmanager
    def transaction(self):
        with self.lock, closing(sqlite3.connect(self.db, timeout=15)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    @staticmethod
    def get(db, kind, identity):
        row = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        if not row:
            raise ValueError(f"Unknown {kind} ID")
        return json.loads(row[0])

    @staticmethod
    def put(db, kind, identity, value):
        db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)", (kind, identity, canonical(value).decode()))

    @staticmethod
    def retry(db, scope, key, fingerprint, result=None):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("idempotency_key must contain 1..128 characters")
        row = db.execute("SELECT fingerprint,result FROM retries WHERE scope=? AND key=?", (scope, key)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise Conflict("idempotency key was already used for different input")
            return json.loads(row[1])
        if result is not None:
            db.execute("INSERT INTO retries VALUES(?,?,?,?)", (scope, key, fingerprint, canonical(result).decode()))
        return None

    def upload(self, payload: bytes):
        if not payload or len(payload) > MAX_UPLOAD:
            raise ValueError("Image must be between 1 byte and 20 MiB")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                w, h = image.size
                if image.format not in {"PNG", "JPEG", "WEBP"} or max(w, h) > 8192 or w*h > 16_777_216:
                    raise ValueError("Unsupported image format or decoded image size")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated images are not supported")
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("Invalid image payload") from exc
        digest = sha(payload)
        source = {"asset_id": digest, "sha256": digest, "width": w, "height": h, "bytes": len(payload)}
        with self.transaction() as db:
            total = db.execute("SELECT COUNT(*) FROM records WHERE kind='asset'").fetchone()[0]
            if total >= 200 and not db.execute("SELECT 1 FROM records WHERE kind='asset' AND id=?", (digest,)).fetchone():
                raise ValueError("Asset quota reached; archive unused data before submitting")
            folder = self.root / "assets"
            folder.mkdir(exist_ok=True)
            destination = folder / digest
            if not destination.exists():
                with destination.open("xb") as f:
                    f.write(payload)
            self.put(db, "asset", digest, source)
        return source

    def submit(self, asset_id: str, requirements: str, idempotency_key: str):
        if not isinstance(requirements, str) or not 1 <= len(requirements) <= 8000:
            raise ValueError("requirements must contain 1..8000 characters")
        fingerprint = sha(canonical([asset_id, requirements]))
        with self.transaction() as db:
            previous = self.retry(db, "submit", idempotency_key, fingerprint)
            if previous:
                return previous
            source = self.get(db, "asset", asset_id)
            if db.execute("SELECT COUNT(*) FROM records WHERE kind='job'").fetchone()[0] >= 200:
                raise ValueError("Job quota reached")
            job = {"job_id": uuid.uuid4().hex, "source": source, "requirements": requirements,
                   "state": "reference_ready", "analysis_revision": 0, "plan_revision": 0,
                   "current_plan_id": None, "current_build_id": None, "created_at": time.time(),
                   "workflow_complete": False, "preview_only": True, "contract_version": CONTRACT,
                   "next_action": "Inspect original; save_scene_analysis with native-coordinate regions."}
            self.put(db, "job", job["job_id"], job)
            self.retry(db, "submit", idempotency_key, fingerprint, job)
        return job

    def job(self, job_id):
        with self.transaction() as db:
            return self.get(db, "job", job_id)

    @staticmethod
    def writable(job):
        if job["state"] == "cancelled":
            raise Conflict("Job is cancelled; create a new job")

    def save_analysis(self, job_id, data, base_revision):
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            self.writable(job)
            if type(base_revision) is not int or job["analysis_revision"] != base_revision:
                raise Conflict("Stale analysis revision")
            analysis = validate_analysis(data, job["source"]).model_dump()
            identity = sha(canonical([job_id, base_revision+1, analysis]))
            self.put(db, "analysis", identity, {"data": analysis, "job_id": job_id, "revision": base_revision+1})
            job.update(analysis_revision=base_revision+1, analysis_id=identity, current_plan_id=None,
                       current_build_id=None, state="analysis_ready", next_action="validate_scene_plan")
            self.put(db, "job", job_id, job)
        return {"analysis_id": identity, "analysis_revision": base_revision+1, "data": analysis}

    def validate(self, job_id, data, analysis_revision, base_revision):
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            self.writable(job)
            if (type(base_revision) is not int or type(analysis_revision) is not int or
                    job["analysis_revision"] != analysis_revision or job["plan_revision"] != base_revision):
                raise Conflict("Stale analysis or plan revision")
            if not job.get("analysis_id"):
                raise ValueError("Save source analysis before planning")
            analysis = self.get(db, "analysis", job["analysis_id"])
            plan = validate_plan(data, analysis["data"]).model_dump()
            self.components.build_assets(plan, db)
            identity = sha(canonical([job_id, job["analysis_id"], base_revision+1, plan]))
            record = {"plan_id": identity, "job_id": job_id, "plan_revision": base_revision+1,
                      "analysis_id": job["analysis_id"], "analysis_revision": analysis_revision,
                      "plan_digest": sha(canonical(plan)), "data": plan}
            self.put(db, "plan", identity, record)
            job.update(plan_revision=base_revision+1, current_plan_id=identity, current_build_id=None,
                       state="plan_validated", next_action="build_threejs_scene")
            self.put(db, "job", job_id, job)
        return {k: v for k, v in record.items() if k != "data"} | {"ready_for_build": True, "visual_acceptance": False}

    def build(self, job_id, plan_id, idempotency_key):
        # Bounded local generation is synchronous in this prototype. SQLite's write
        # transaction keeps revision validation + publication atomic across processes.
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            self.writable(job)
            plan = self.get(db, "plan", plan_id)
            if plan["job_id"] != job_id or job["current_plan_id"] != plan_id:
                raise Conflict("Build must use this job's current validated plan")
            analysis = self.get(db, "analysis", plan["analysis_id"])["data"]
            assets = self.components.build_assets(plan["data"], db)
            build_id, files, manifest = build_files(plan["data"], job["source"], analysis, job_id, plan_id, assets)
            fingerprint = sha(canonical([job_id, plan_id, build_id]))
            prior = self.retry(db, "build:"+job_id, idempotency_key, fingerprint)
            directory = self.root / "builds" / job_id / build_id
            if prior:
                # Idempotence reuses an existing artifact, not merely its success
                # record. Do not reopen a transaction here: the write transaction
                # holds the current plan until these immutable bytes are checked.
                self.verify_build_record(directory, prior)
                return prior
            if db.execute("SELECT COUNT(*) FROM records WHERE kind='build'").fetchone()[0] >= 1000:
                raise ValueError("Build quota reached; archive unused data before building")
            if not directory.exists():
                write_build(directory, files)
            existing = db.execute("SELECT payload FROM records WHERE kind='build' AND id=?", (build_id,)).fetchone()
            if existing:
                # A new retry key must not bless a modified ZIP by replacing its
                # saved digest for this same immutable build identity.
                self.verify_build_record(directory, json.loads(existing[0]))
            else:
                verify_build(directory)
            record = {"job_id": job_id, "build_id": build_id, "plan_id": plan_id, "manifest": manifest,
                      "zip_sha256": sha((directory / "project.zip").read_bytes()),
                      "state": "built", "workflow_complete": False, "preview_only": True,
                      "next_action": "capture_scene_views; visual calibration and completion gates are not released"}
            self.put(db, "build", build_id, record)
            self.retry(db, "build:"+job_id, idempotency_key, fingerprint, record)
            job.update(current_build_id=build_id, state="built", next_action=record["next_action"])
            self.put(db, "job", job_id, job)
        return record

    @staticmethod
    def verify_build_record(directory, build):
        manifest = verify_build(directory)
        if manifest != (build["manifest"] | {"build_id": build["build_id"]}):
            raise ValueError("Build identity differs from the saved job record")
        if build.get("zip_sha256") and sha((directory / "project.zip").read_bytes()) != build["zip_sha256"]:
            raise ValueError("Export archive changed")

    def build_path(self, job_id, build_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id) or not re.fullmatch(r"[a-f0-9]{64}", build_id):
            raise ValueError("Invalid job/build ID")
        with self.transaction() as db:
            build = self.get(db, "build", build_id)
            if build["job_id"] != job_id:
                raise ValueError("Build belongs to a different job")
        directory = self.root / "builds" / job_id / build_id
        self.verify_build_record(directory, build)
        return directory

    def capture(self, job_id, build_id):
        if not self.capture_slot.acquire(blocking=False):
            raise Conflict("Capture worker busy; retry later")
        try:
            lease = secrets.token_hex(16)
            with self.transaction() as db:
                row = db.execute("SELECT payload FROM records WHERE kind='lease' AND id='capture'").fetchone()
                if row and json.loads(row[0])["expires_at"] > time.time():
                    raise Conflict("Capture worker leased by another process; retry later")
                if db.execute("SELECT COUNT(*) FROM records WHERE kind='capture'").fetchone()[0] >= 300:
                    raise ValueError("Capture quota reached")
                self.put(db, "lease", "capture", {"owner": lease, "expires_at": time.time()+150})
            job = self.job(job_id)
            self.writable(job)
            if job["current_build_id"] != build_id:
                raise Conflict("Cannot audit an obsolete build")
            directory = self.build_path(job_id, build_id)
            capture_id = uuid.uuid4().hex
            out = self.root / "captures" / capture_id
            out.mkdir(parents=True, exist_ok=False)
            result = subprocess.run([os.getenv("CANVASLAB3D_NODE", "node"), str(REPO / "scripts3d/capture.mjs"),
                                     str(directory), str(out)], capture_output=True, text=True, timeout=120,
                                    encoding="utf-8", errors="replace", shell=False)
            if result.returncode:
                raise ValueError("Capture worker failed: " + result.stderr[-2500:])
            observation = json.loads((out / "observation.json").read_text(encoding="utf-8"))
            record = {"capture_id": capture_id, "job_id": job_id, "build_id": build_id,
                      "nonce": secrets.token_hex(16), "created_at": time.time(),
                      "worker": "local-development-worker-v1", "production_attestation": False,
                      "files": {p.name: sha(p.read_bytes()) for p in out.iterdir() if p.is_file()},
                      "observation": observation}
            record["signature"] = hmac.new(self.secret, canonical(record), hashlib.sha256).hexdigest()
            with self.transaction() as db:
                job = self.get(db, "job", job_id)
                self.writable(job)
                if job["current_build_id"] != build_id:
                    raise Conflict("Scene changed during capture; capture cannot certify the new build")
                self.put(db, "capture", capture_id, record)
                job.update(state="captured", last_capture_id=capture_id, next_action="audit_scene_views")
                self.put(db, "job", job_id, job)
            return record
        finally:
            if 'lease' in locals():
                with self.transaction() as db:
                    row = db.execute("SELECT payload FROM records WHERE kind='lease' AND id='capture'").fetchone()
                    if row and json.loads(row[0])["owner"] == lease:
                        db.execute("DELETE FROM records WHERE kind='lease' AND id='capture'")
            self.capture_slot.release()

    def checked_capture(self, db, job, capture_id):
        capture = self.get(db, "capture", capture_id)
        signed = {k: v for k, v in capture.items() if k != "signature"}
        if not hmac.compare_digest(capture["signature"], hmac.new(self.secret, canonical(signed), hashlib.sha256).hexdigest()):
            raise ValueError("Invalid capture signature")
        if capture["job_id"] != job["job_id"] or capture["build_id"] != job["current_build_id"]:
            raise Conflict("Capture must belong to the current job and build")
        directory = self.root / "builds" / job["job_id"] / capture["build_id"]
        verified = verify_build(directory)
        saved = self.get(db, "build", capture["build_id"])
        if verified != (saved["manifest"] | {"build_id": capture["build_id"]}):
            raise ValueError("Build identity differs from the captured job record")
        if time.time()-capture["created_at"] > 1800:
            raise ValueError("Capture expired; capture this build again")
        self.writable(job)
        for name, digest in capture["files"].items():
            if sha((self.root / "captures" / capture_id / name).read_bytes()) != digest:
                raise ValueError("Capture evidence has changed")
        return capture

    def camera_proposal(self, job_id, capture_id):
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            capture = self.checked_capture(db, job, capture_id)
            if capture["observation"].get("errors") or any(not t["passed"] for t in capture["observation"].get("tests", [])):
                raise ValueError("Fix capture runtime or interaction failures before camera fitting")
            plan = self.get(db, "plan", job["current_plan_id"])["data"]
            analysis = self.get(db, "analysis", job["analysis_id"])["data"]
            proposal = suggest_camera(plan, analysis, capture["observation"])
            validate_plan(proposal["proposed_plan"], analysis)
            return proposal | {"job_id": job_id, "capture_id": capture_id,
                               "base_revision": job["plan_revision"], "analysis_revision": job["analysis_revision"],
                               "base_build_id": job["current_build_id"]}

    def handoff(self, job_id):
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            result = {"job": job, "original_image_path": "/assets/"+job["source"]["asset_id"],
                      "analysis": self.get(db, "analysis", job["analysis_id"])["data"] if job.get("analysis_id") else None,
                      "plan": self.get(db, "plan", job["current_plan_id"])["data"] if job.get("current_plan_id") else None,
                      "audit": self.get(db, "audit", job["last_audit_id"]) if job.get("last_audit_id") else None}
            result["audit_matches_current_build"] = bool(result["audit"] and result["audit"]["build_id"] == job["current_build_id"])
            result["next_steps"] = ["Inspect original and current capture; never reuse findings from another build",
                                    "Annotate visible_polygons in ORIGINAL native coordinates for measurable silhouettes",
                                    "Use suggest_scene_camera for an advisory orthographic fit; validate and recapture after applying",
                                    "Use fit_scene_landmarks for source-measured point correspondences when camera angle or foreshortening differs",
                                    "Review every object and overall; diagnostics alone never certify completion"]
            result["reference_fidelity_contract"] = {
                "version": "source-led-3d-v1",
                "objective": "Match the original image, not the highest implemented preset",
                "new_scene_appearance_mode": "reference",
                "appearance_guidance": "Set appearance_mode=reference explicitly for new reconstructions. Quality controls sampling/budgets, not automatic material or lighting restyling. Recalibrate when migrating a legacy scene.",
                "repair_order": ["camera_and_composition", "silhouette_and_occlusion", "component_proportions",
                                 "geometry_details", "materials_and_lighting", "water_and_atmosphere"],
                "required_comparisons": ["same-native-size original and reference capture together",
                                         "roof and facade closeup", "vegetation and props closeup",
                                         "water and boat closeup", "interactive alternate views"],
                "preset_is_fidelity_evidence": False,
                "triangle_count_is_fidelity_evidence": False,
                "unseen_surfaces": "Explicitly inferred; never claimed to have been recovered from one image",
                "stop_condition": "Do not claim reference fidelity while major source-visible differences remain",
            }
            return result

    def landmark_proposal(self, job_id, source_sha256, landmarks, base_revision):
        """Advisory fit of explicitly supplied correspondences, never capture evidence."""
        from .landmark_fit import fit_orthographic_landmarks
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            self.writable(job)
            if source_sha256 != job["source"]["sha256"]:
                raise ValueError("Landmarks must refer to this job's original image SHA256")
            if type(base_revision) is not int or base_revision != job["plan_revision"]:
                raise Conflict("Stale plan revision for landmark fitting")
            if not job.get("current_plan_id"):
                raise ValueError("Validate a scene plan before fitting world landmarks")
            plan = self.get(db, "plan", job["current_plan_id"])["data"]
            analysis = self.get(db, "analysis", job["analysis_id"])["data"]
            if not isinstance(landmarks, list) or not 4 <= len(landmarks) <= 128:
                raise ValueError("Provide 4..128 source landmark correspondences")
            sx, sy, sw, sh = analysis["scene_box"]
            local = copy.deepcopy(landmarks)
            for mark in local:
                pixel = mark.get("pixel") if isinstance(mark, dict) else None
                if (not isinstance(pixel, list) or len(pixel) != 2 or
                        any(type(p) not in (int, float) for p in pixel) or
                        not sx <= pixel[0] <= sx+sw or not sy <= pixel[1] <= sy+sh):
                    raise ValueError("Landmark pixels must be native coordinates inside the original scene crop")
                mark["pixel"] = [pixel[0]-sx, pixel[1]-sy]
            proposal = fit_orthographic_landmarks([sw, sh], local, plan["camera"],
                                                  framing_aspect=sw/sh if plan.get("appearance_mode") == "reference" else 1.45)
            proposed_plan = copy.deepcopy(plan)
            proposed_plan["camera"] = proposal["suggested_camera"]
            validate_plan(proposed_plan, analysis)
            return proposal | {
                "job_id": job_id, "source_sha256": source_sha256,
                "base_revision": base_revision, "analysis_revision": job["analysis_revision"],
                "base_build_id": job["current_build_id"], "proposed_plan": proposed_plan,
                "source_landmarks": copy.deepcopy(landmarks), "scene_crop": analysis["scene_box"],
                "residual_coordinate_system": "scene-crop pixels",
                "applied": False, "workflow_complete": False,
                "evidence_type": "caller-measured correspondences; not an automatic detection or a signed capture",
                "next_action": "Review residuals, validate the proposed plan, rebuild and compare a fresh capture to the original",
            }

    def audit(self, job_id, capture_id, findings):
        if not isinstance(findings, list) or not 1 <= len(findings) <= 129:
            raise ValueError("Provide structured model findings, not an empty approval")
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            capture = self.checked_capture(db, job, capture_id)
            if db.execute("SELECT 1 FROM records WHERE kind='consumed_capture' AND id=?", (capture_id,)).fetchone():
                raise Conflict("Capture already audited; create fresh evidence for another iteration")
            for finding in findings:
                if (not isinstance(finding, dict) or set(finding) != {"object_id", "difference", "severity", "planned_fix"}
                        or finding["severity"] not in {"none", "minor", "major", "blocker"}
                        or any(not isinstance(v, str) or len(v) > 2000 for v in finding.values())
                        or not finding["difference"].strip()):
                    raise ValueError("Invalid model finding")
            plan = self.get(db, "plan", job["current_plan_id"])["data"]
            required = {n["id"] for n in plan["objects"]} | {"overall"}
            reviewed = [f["object_id"] for f in findings]
            if set(reviewed) != required or len(reviewed) != len(required):
                raise ValueError("Review every scene object and overall composition exactly once")
            analysis = self.get(db, "analysis", job["analysis_id"])["data"]
            targets = {r["id"]: r for r in analysis["regions"]}
            projected = capture["observation"]["reference"]["objects"]
            metrics = []
            for obj in plan["objects"]:
                if len(obj["region_ids"]) != 1 or obj["id"] not in projected:
                    continue
                target = targets[obj["region_ids"][0]]["box"]
                actual = projected[obj["id"]]["screen_box"]
                if actual is None:
                    metrics.append({"object_id": obj["id"], "visible": False})
                    continue
                x, y, w, h = target
                sx, sy, sw, sh = analysis["scene_box"]
                x, y = x-sx, y-sy
                a, b, c, d = actual
                diagonal = (sw**2+sh**2)**0.5
                metrics.append({"object_id": obj["id"], "center_error_ratio": ((x+w/2-a-c/2)**2+(y+h/2-b-d/2)**2)**0.5/diagonal,
                                "width_error_ratio": abs(c-w)/w, "height_error_ratio": abs(d-h)/h,
                                "measurement": "projected world AABB; not visible silhouette"})
            report = {"audit_id": uuid.uuid4().hex, "job_id": job_id, "build_id": capture["build_id"],
                      "capture_id": capture_id, "findings": findings, "object_metrics": metrics,
                      "requires_revision": True, "workflow_complete": False,
                      "blockers": ["silhouette_and_occlusion_gates_not_implemented", "thresholds_not_calibrated",
                                   "production_worker_attestation_not_implemented"],
                      "has_major_findings": any(f["severity"] in {"major", "blocker"} for f in findings)}
            report["visible_quality"] = visible_metrics(plan, analysis, self.root / "captures" / capture_id / "id.png",
                                                        capture["observation"].get("id_encoding"))
            report["failed_checks"] = [t["name"] for t in capture["observation"].get("tests", []) if not t["passed"]]
            report["analysis_id"] = job["analysis_id"]
            report["plan_id"] = job["current_plan_id"]
            report["previous_audit_id"] = job.get("last_audit_id")
            previous = self.get(db, "audit", job["last_audit_id"]) if job.get("last_audit_id") else None
            report["same_reference_annotations_as_previous"] = bool(previous and previous.get("analysis_id") == job["analysis_id"])
            self.put(db, "audit", report["audit_id"], report)
            self.put(db, "consumed_capture", capture_id, {"audit_id": report["audit_id"]})
            job.update(state="needs_revision", last_audit_id=report["audit_id"], next_action="Revise plan and recapture; full completion is not supported by this development service")
            self.put(db, "job", job_id, job)
        return report

    def cancel(self, job_id):
        with self.transaction() as db:
            job = self.get(db, "job", job_id)
            job.update(state="cancelled", next_action="Create a new job to resume; existing artifacts are retained")
            self.put(db, "job", job_id, job)
        return job

    def status(self):
        try:
            files = runtime_files()
            runtime = sha(canonical({k: sha(v) for k, v in files.items()}))
            ready, error = True, None
        except (ValueError, FileNotFoundError) as exc:
            ready, runtime, error = False, None, str(exc)
        disk_digest = sha(canonical({p.name: sha(p.read_bytes()) for p in sorted((REPO / "server3d").glob("*.py"))}))
        return {"name": "CanvasLab 3D", "server_version": VERSION, "contract_version": CONTRACT,
                "source_tree_digest": LOADED_SOURCE_DIGEST, "on_disk_source_digest": disk_digest,
                "restart_required": disk_digest != LOADED_SOURCE_DIGEST,
                "schema_digest": sha(canonical({"scene":ScenePlan.model_json_schema(), "analysis":Analysis.model_json_schema()})), "runtime_bundle_sha256": runtime,
                "runtime_ready": ready, "runtime_error": error, "gpu_required": False,
                "supported": ["original_upload", "versioned_analysis", "validated_scene_plan", "procedural_build",
                              "zip_export", "local_capture", "diagnostic_audit", "visible_silhouette_metrics",
                              "orthographic_camera_proposal", "manual_landmark_camera_fit", "source_led_fidelity_contract",
                              "versioned_scene_handoff", "blender_components", "registered_glb_composition",
                              "reference_appearance_mode", "bounded_embedded_png_components"],
                "unsupported": ["production_certificates", "remote_worker_registration", "silhouette_acceptance",
                                "automatic_image_to_mesh", "whole_scene_glb_export", "full_collision_detection"],
                "blender": self.components.status(), "workflow_complete": False}
