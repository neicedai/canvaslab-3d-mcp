import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from benchmarks3d.scenes import water_town
from server3d.jobs import Conflict, Store


class LandmarkServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        image = io.BytesIO()
        Image.new("RGB", (1200, 900)).save(image, "PNG")
        self.source = self.store.upload(image.getvalue())
        self.job = self.store.submit(self.source["asset_id"], "reference fit", "first")
        self.plan = water_town()
        self.analysis = {
            "source_sha256": self.source["sha256"], "scene_box": [100, 50, 1000, 800],
            "regions": [{"id": n["id"], "label": n["id"], "box": [100, 50, 1000, 800],
                         "critical": True, "confidence": .5, "evidence": "test fixture"}
                        for n in self.plan["objects"]],
            "assumptions": ["test"], "change_reason": "fixture",
        }
        self.store.save_analysis(self.job["job_id"], self.analysis, 0)
        self.store.validate(self.job["job_id"], self.plan, 1, 0)
        self.marks = [{"id": f"corner-{i}", "world": w, "pixel": p,
                       "measurement": "measured", "evidence": "source plane corner"}
                      for i, (w, p) in enumerate([
                          ([-1, 0, -1], [300, 200]), ([1, 0, -1], [700, 300]),
                          ([1, 0, 1], [600, 700]), ([-1, 0, 1], [200, 600])])]

    def propose(self, **changes):
        args = dict(job_id=self.job["job_id"], source_sha256=self.source["sha256"],
                    landmarks=self.marks, base_revision=1)
        args.update(changes)
        return self.store.landmark_proposal(**args)

    def test_fitting_is_source_revision_bound(self):
        with self.assertRaisesRegex(ValueError, "original image SHA256"):
            self.propose(source_sha256="a" * 64)
        for revision in [0, True, 1.0]:
            with self.assertRaises(Conflict):
                self.propose(base_revision=revision)

    def test_crop_offset_and_read_only_proposal(self):
        original = copy.deepcopy(self.marks)
        before = self.store.job(self.job["job_id"])
        with patch("server3d.landmark_fit.fit_orthographic_landmarks",
                   return_value={"suggested_camera": self.plan["camera"], "certified": False}) as fitted:
            result = self.propose()
        size, marks, camera = fitted.call_args.args
        self.assertEqual(size, [1000, 800])
        self.assertEqual(marks[0]["pixel"], [200, 150])
        self.assertEqual(self.marks, original)
        self.assertEqual(result["source_landmarks"], original)
        self.assertFalse(result["applied"])
        self.assertFalse(result["workflow_complete"])
        self.assertFalse(result["certified"])
        self.assertEqual(self.store.job(self.job["job_id"]), before)

    def test_outside_crop_or_invalid_pixels_rejected(self):
        for pixel in [[99, 200], [200, 851], [float("nan"), 200], [True, 200], "200,200"]:
            marks = copy.deepcopy(self.marks)
            marks[0]["pixel"] = pixel
            with self.assertRaisesRegex(ValueError, "native coordinates"):
                self.propose(landmarks=marks)

    def test_cancelled_job_cannot_fit(self):
        self.store.cancel(self.job["job_id"])
        with self.assertRaises(Conflict):
            self.propose()

    def test_fidelity_contract_does_not_equate_detail_with_quality(self):
        contract = self.store.handoff(self.job["job_id"])["reference_fidelity_contract"]
        self.assertFalse(contract["preset_is_fidelity_evidence"])
        self.assertFalse(contract["triangle_count_is_fidelity_evidence"])
        self.assertEqual(contract["repair_order"][0], "camera_and_composition")


if __name__ == "__main__":
    unittest.main()
