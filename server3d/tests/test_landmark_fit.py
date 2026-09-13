import copy
import unittest

import numpy as np

from server3d.landmark_fit import fit_orthographic_landmarks, project_orthographic_points


class LandmarkFitTests(unittest.TestCase):
    size = [1536, 1024]
    initial = {"projection": "orthographic", "position": [11.5, 10.7, 18.5], "target": [0., 2.7, .45], "vertical_span": 13.3}
    actual = {"projection": "orthographic", "position": [11.2, 15.3, 17.4], "target": [.3, 2.1, .2], "vertical_span": 14.1}
    world = [[-5., 0., 4.], [5., 0., 4.], [5., 0., -4.], [-5., 0., -4.], [1., 4., -1.], [-3., 2., 0.]]

    def points(self, camera=None, world=None, size=None):
        world = world if world is not None else self.world
        pixels = project_orthographic_points(size or self.size, world, camera or self.actual)
        return [{"id": f"point-{i}", "world": w, "pixel": p, "measurement": "measured", "evidence": "Synthetic known correspondence for algorithm regression."}
                for i, (w, p) in enumerate(zip(world, pixels))]

    def test_fits_angle_scale_and_translation_with_actual_reprojection(self):
        points = self.points()
        result = fit_orthographic_landmarks(self.size, points, self.initial)
        self.assertLess(result["rms_px"], 1e-4)
        self.assertGreater(result["initial_rms_px"], 20)
        self.assertFalse(result["certified"])
        np.testing.assert_allclose(project_orthographic_points(self.size, self.world, result["suggested_camera"]), [p["pixel"] for p in points], atol=1e-4)

    def test_coplanar_ground_points_fit_but_explicitly_warn(self):
        result = fit_orthographic_landmarks(self.size, self.points(world=self.world[:4]), self.initial)
        self.assertLess(result["rms_px"], 1e-4)
        self.assertTrue(any("coplanar" in warning for warning in result["warnings"]))

    def test_estimated_points_remain_labeled_and_inconsistent_geometry_is_reported(self):
        points = self.points(); points[-1]["measurement"] = "estimated"; points[-1]["pixel"][0] += 160
        result = fit_orthographic_landmarks(self.size, points, self.initial)
        self.assertGreater(result["rms_px"], 20)
        self.assertEqual(result["residuals"][-1]["measurement"], "estimated")
        self.assertIn("estimate", " ".join(result["warnings"]).lower())
        self.assertFalse(result["certified"])

    def test_native_mobile_aspect_uses_runtime_camera_fit_rule(self):
        size = [390, 844]
        result = fit_orthographic_landmarks(size, self.points(size=size), self.initial)
        self.assertLess(result["rms_px"], 1e-4)
        self.assertAlmostEqual(result["suggested_camera"]["vertical_span"], self.actual["vertical_span"], places=5)

    def test_rejects_invalid_evidence_coordinates_counts_and_degenerate_points(self):
        for change in ({"measurement": "automatic"}, {"evidence": " "}, {"pixel": [1600., 20.]}, {"world": [0., float("nan"), 1.]}, {"extra": 1}):
            points = self.points(); points[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                fit_orthographic_landmarks(self.size, points, self.initial)
        for points in (self.points()[:3], self.points() * 30,
                       [{**point, "world": [0., 0., 0.]} for point in self.points()]):
            with self.assertRaises(ValueError):
                fit_orthographic_landmarks(self.size, points, self.initial)
        with self.assertRaises(ValueError):
            fit_orthographic_landmarks([0, 1024], self.points(), self.initial)
        with self.assertRaises(ValueError):
            fit_orthographic_landmarks(self.size, self.points(), {**self.initial, "projection": "perspective"})

    def test_input_data_is_not_modified(self):
        points = self.points(); before = copy.deepcopy(points); camera = copy.deepcopy(self.initial)
        fit_orthographic_landmarks(self.size, points, camera)
        self.assertEqual(points, before)
        self.assertEqual(camera, self.initial)


if __name__ == "__main__":
    unittest.main()
