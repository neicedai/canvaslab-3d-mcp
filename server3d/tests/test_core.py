import copy
import io
import json
import tempfile
import time
import hmac
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient

from benchmarks3d.scenes import water_town
from server3d.api import create_app
from server3d.builder import canonical, sha, verify_build
from server3d.jobs import Store, Conflict
from server3d.scene_schema import ScenePlan, validate_plan


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        buf = io.BytesIO(); Image.new("RGB", (1200, 900), "white").save(buf, "PNG")
        self.image = buf.getvalue(); self.source = self.store.upload(self.image)
        self.job = self.store.submit(self.source["asset_id"], "test scene", "first")
        self.plan = water_town()
        self.analysis = {"source_sha256": self.source["sha256"], "scene_box": [0, 0, 1200, 900],
                         "regions": [{"id": n["id"], "label": n["id"], "box": [0, 0, 1200, 900],
                                      "critical": True, "confidence": .5, "evidence": "test only"} for n in self.plan["objects"]],
                         "assumptions": ["test"], "change_reason": "initial"}

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        a = self.store.save_analysis(self.job["job_id"], self.analysis, 0)
        return self.store.validate(self.job["job_id"], self.plan, a["analysis_revision"], 0)

    def build(self):
        p = self.prepare()
        with patch("server3d.builder.runtime_files", return_value={"index.html": b"test", "scene.js": b"// runtime"}):
            return self.store.build(self.job["job_id"], p["plan_id"], "build-1")

    def test_original_bytes_retained(self):
        self.assertEqual((self.store.root / "assets" / self.source["asset_id"]).read_bytes(), self.image)

    def test_submit_idempotence(self):
        self.assertEqual(self.store.submit(self.source["asset_id"], "test scene", "first"), self.job)
        with self.assertRaises(Conflict): self.store.submit(self.source["asset_id"], "other", "first")

    def test_persistent_restart(self):
        other = Store(self.store.root)
        self.assertEqual(other.job(self.job["job_id"]), self.job)

    def test_unknown_code_rejected(self):
        for key in ["js", "shader", "url"]:
            plan = copy.deepcopy(self.plan); plan[key] = "alert(1)"
            with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_bad_geometry_rejected(self):
        for value in [0, -1, float("nan"), float("inf"), 500]:
            plan = copy.deepcopy(self.plan); plan["objects"][0]["dimensions"][0] = value
            with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_full_image_billboard_rejected(self):
        plan = copy.deepcopy(self.plan); plan["objects"][0]["kind"] = "billboard"
        with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_jiangnan_presentation_and_declared_sign(self):
        plan = copy.deepcopy(self.plan)
        plan["presentation"] = "jiangnan"
        node = plan["objects"][0]
        node.update(kind="sign", text="青崖渡")
        self.assertEqual(ScenePlan.model_validate(plan).presentation, "jiangnan")
        for text in [None, "", "字" * 25]:
            node["text"] = text
            with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_text_and_presentation_cannot_inject_custom_runtime(self):
        plan = copy.deepcopy(self.plan)
        plan["presentation"] = "<script>"
        with self.assertRaises(ValueError): ScenePlan.model_validate(plan)
        plan.pop("presentation")
        plan["objects"][0]["text"] = "青崖渡"
        with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_material_parent_and_id_validation(self):
        for field, value in [("parent_id", "absent"), ("parent_id", "water"), ("material_id", "absent"), ("id", "../../x")]:
            plan = copy.deepcopy(self.plan); plan["objects"][0][field] = value
            with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_cycle_rejected(self):
        plan = copy.deepcopy(self.plan); plan["objects"][0]["parent_id"] = "quay"; plan["objects"][1]["parent_id"] = "water"
        with self.assertRaises(ValueError): ScenePlan.model_validate(plan)

    def test_missing_critical_region(self):
        self.plan["objects"].pop()
        with self.assertRaises(ValueError): validate_plan(self.plan, self.analysis)

    def test_native_region_and_source_identity(self):
        for field, value in [("source_sha256", "a"*64), ("scene_box", [0, 0, 1500, 900])]:
            a = copy.deepcopy(self.analysis); a[field] = value
            with self.assertRaises(ValueError): self.store.save_analysis(self.job["job_id"], a, 0)

    def test_home_outside_drag_bounds(self):
        self.plan["objects"][-1]["position"][0] = 80
        with self.assertRaises(ValueError): ScenePlan.model_validate(self.plan)

    def test_stale_revisions(self):
        self.prepare()
        with self.assertRaises(Conflict): self.store.save_analysis(self.job["job_id"], self.analysis, 0)
        with self.assertRaises(Conflict): self.store.validate(self.job["job_id"], self.plan, 1, 0)

    def test_analysis_change_invalidates_plan(self):
        p = self.prepare(); self.store.save_analysis(self.job["job_id"], self.analysis, 1)
        with self.assertRaises(Conflict): self.store.build(self.job["job_id"], p["plan_id"], "bad")

    def test_immutable_build_and_tamper(self):
        b = self.build(); directory = self.store.build_path(self.job["job_id"], b["build_id"])
        self.assertFalse(b["workflow_complete"])
        self.assertEqual(verify_build(directory)["build_id"], b["build_id"])
        (directory / "scene.js").write_bytes(b"// changed")
        with self.assertRaises(ValueError): self.store.build_path(self.job["job_id"], b["build_id"])

    def retry_build(self, record):
        with patch("server3d.builder.runtime_files", return_value={"index.html": b"test", "scene.js": b"// runtime"}):
            return self.store.build(self.job["job_id"], record["plan_id"], "build-1")

    def test_build_retry_verifies_intact_artifacts_without_nested_transaction(self):
        record = self.build()
        self.assertEqual(self.retry_build(record), record)
        self.assertEqual(self.store.job(self.job["job_id"])["current_build_id"], record["build_id"])

    def test_build_retry_rejects_changed_runtime_asset(self):
        record = self.build()
        directory = self.store.build_path(self.job["job_id"], record["build_id"])
        (directory / "scene.js").write_bytes(b"// tampered after successful build")
        with self.assertRaisesRegex(ValueError, "asset changed"):
            self.retry_build(record)

    def test_build_retry_rejects_changed_export_archive(self):
        record = self.build()
        directory = self.store.build_path(self.job["job_id"], record["build_id"])
        (directory / "project.zip").write_bytes(b"tampered archive")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            self.retry_build(record)

    def test_build_retry_rejects_missing_artifact(self):
        record = self.build()
        directory = self.store.build_path(self.job["job_id"], record["build_id"])
        # Only this test-owned temporary artifact is removed.
        (directory / "scene.js").unlink()
        with self.assertRaises(FileNotFoundError):
            self.retry_build(record)

    def test_new_build_retry_key_cannot_reapprove_changed_export(self):
        record = self.build()
        directory = self.store.build_path(self.job["job_id"], record["build_id"])
        (directory / "project.zip").write_bytes(b"tampered archive")
        with patch("server3d.builder.runtime_files", return_value={"index.html": b"test", "scene.js": b"// runtime"}):
            with self.assertRaisesRegex(ValueError, "archive changed"):
                self.store.build(self.job["job_id"], record["plan_id"], "new-key")
        with self.store.transaction() as db:
            saved = self.store.get(db, "build", record["build_id"])
        self.assertEqual(saved["zip_sha256"], record["zip_sha256"])

    def test_path_and_cross_job_build(self):
        b = self.build()
        for identity in ["../../", "a"*32]:
            with self.assertRaises(ValueError): self.store.build_path(identity, b["build_id"])

    def test_export_tamper_rejected(self):
        b = self.build()
        directory = self.store.build_path(self.job["job_id"], b["build_id"])
        (directory / "project.zip").write_bytes(b"tampered archive")
        with self.assertRaises(ValueError): self.store.build_path(self.job["job_id"], b["build_id"])

    def test_build_idempotence(self):
        p = self.prepare()
        with patch("server3d.builder.runtime_files", return_value={"index.html": b"test"}):
            a = self.store.build(self.job["job_id"], p["plan_id"], "same")
            b = self.store.build(self.job["job_id"], p["plan_id"], "same")
        self.assertEqual(a, b)

    def test_cancel_retains_source(self):
        self.store.cancel(self.job["job_id"])
        self.assertTrue((self.store.root / "assets" / self.source["asset_id"]).exists())
        with self.assertRaises(Conflict): self.store.save_analysis(self.job["job_id"], self.analysis, 0)

    def test_cannot_upload_observation_as_audit(self):
        self.build()
        with self.assertRaises(ValueError): self.store.audit(self.job["job_id"], "fake", [{"object_id": "overall"}])

    def test_authentication(self):
        with self.assertRaises(RuntimeError): create_app(self.store, "short")
        client = TestClient(create_app(self.store, "test-secret-"*4))
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.post("/assets", content=self.image).status_code, 401)
        response = client.post("/assets", content=self.image, headers={"Authorization": "Bearer "+"test-secret-"*4})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["asset_id"], self.source["asset_id"])

    def test_invalid_image_rejected(self):
        with self.assertRaises(ValueError): self.store.upload(b"not an image")

    def test_global_worker_lease(self):
        b = self.build()
        with self.store.transaction() as db:
            self.store.put(db, "lease", "capture", {"owner": "other-process", "expires_at": time.time()+60})
        other = Store(self.store.root)
        with self.assertRaises(Conflict): other.capture(self.job["job_id"], b["build_id"])
        with self.store.transaction() as db:
            self.assertEqual(self.store.get(db, "lease", "capture")["owner"], "other-process")

    def fake_capture_fixture(self):
        # Test-only evidence signed by test Store. No public API imports a claim
        # like this; production callers must go through the worker subprocess.
        b = self.build(); capture_id = "a"*32
        root = self.store.root / "captures" / capture_id; root.mkdir(parents=True)
        (root / "reference.png").write_bytes(self.image)
        record = {"capture_id": capture_id, "job_id": self.job["job_id"], "build_id": b["build_id"],
                  "created_at": time.time(), "files": {"reference.png": sha(self.image)},
                  "observation": {"reference": {"objects": {}}}}
        record["signature"] = hmac.new(self.store.secret, canonical(record), hashlib.sha256).hexdigest()
        with self.store.transaction() as db:
            self.store.put(db, "capture", capture_id, record)
        findings = [{"object_id": n["id"], "difference": "test observation", "severity": "none", "planned_fix": ""} for n in self.plan["objects"]]
        findings.append({"object_id": "overall", "difference": "test", "severity": "none", "planned_fix": ""})
        return capture_id, findings

    def test_audit_is_not_completion_and_cannot_replay(self):
        capture, findings = self.fake_capture_fixture()
        result = self.store.audit(self.job["job_id"], capture, findings)
        self.assertFalse(result["workflow_complete"])
        self.assertTrue(result["requires_revision"])
        with self.assertRaises(Conflict): self.store.audit(self.job["job_id"], capture, findings)

    def test_missing_object_review_rejected(self):
        capture, findings = self.fake_capture_fixture()
        with self.assertRaises(ValueError): self.store.audit(self.job["job_id"], capture, findings[:-1])

    def test_capture_tamper_rejected(self):
        capture, findings = self.fake_capture_fixture()
        (self.store.root / "captures" / capture / "reference.png").write_bytes(b"changed")
        with self.assertRaises(ValueError): self.store.audit(self.job["job_id"], capture, findings)

    def test_camera_cannot_use_fake_capture(self):
        self.build()
        with self.assertRaises(ValueError): self.store.camera_proposal(self.job["job_id"], "fake")

    def test_camera_cannot_use_expired_capture(self):
        capture, _ = self.fake_capture_fixture()
        with patch("server3d.jobs.time.time", return_value=time.time()+2000):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.store.camera_proposal(self.job["job_id"], capture)

    def test_camera_cannot_use_changed_capture(self):
        capture, _ = self.fake_capture_fixture()
        (self.store.root / "captures" / capture / "reference.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.camera_proposal(self.job["job_id"], capture)

    def test_audit_rejects_build_modified_after_capture(self):
        capture, findings = self.fake_capture_fixture()
        job = self.store.job(self.job["job_id"])
        directory = self.store.root / "builds" / job["job_id"] / job["current_build_id"]
        (directory / "scene.js").write_bytes(b"changed since capture")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.audit(job["job_id"], capture, findings)

    def test_camera_rejects_stale_build(self):
        capture, _ = self.fake_capture_fixture()
        self.store.validate(self.job["job_id"],self.plan,1,1)
        with self.assertRaises(Conflict): self.store.camera_proposal(self.job["job_id"],capture)

    def test_handoff_marks_previous_audit_stale(self):
        capture, findings = self.fake_capture_fixture()
        self.store.audit(self.job["job_id"], capture, findings)
        self.assertTrue(self.store.handoff(self.job["job_id"])["audit_matches_current_build"])
        self.store.validate(self.job["job_id"], self.plan, 1, 1)
        handoff = self.store.handoff(self.job["job_id"])
        self.assertFalse(handoff["audit_matches_current_build"])
        self.assertIsNotNone(handoff["audit"])


if __name__ == "__main__":
    unittest.main()
