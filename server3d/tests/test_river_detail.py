"""CPU geometry regressions for the bounded riverside detail levels."""
import math
import struct
import unittest
from collections import Counter

from scripts3d.blender_components import RiverGeometry, validate_recipe_data
from server3d.component_schema import ComponentRecipe


class RiverDetailTests(unittest.TestCase):
    color = "#7e914c"
    center = (1.5, -2.0, 0.75)
    radii = (0.8, 1.2, 0.45)

    def ball(self, detail):
        geometry = RiverGeometry(detail=detail)
        geometry.ball(self.center, self.radii, self.color)
        treatment = "smooth" if detail == 2 else "flat"
        return geometry.parts[(self.color, treatment)]

    def test_finer_ball_reduces_polygonal_silhouette(self):
        coarse_points, coarse_faces = self.ball(1)
        fine_points, fine_faces = self.ball(2)
        self.assertGreater(len(fine_faces), len(coarse_faces))

        def longest_normalized_edge(points, faces):
            normalized = [tuple((point[i] - self.center[i]) / self.radii[i]
                                for i in range(3)) for point in points]
            return max(math.dist(normalized[a], normalized[b])
                       for face in faces
                       for a, b in zip(face, face[1:] + face[:1]))

        self.assertLess(longest_normalized_edge(fine_points, fine_faces),
                        longest_normalized_edge(coarse_points, coarse_faces))

    def test_both_balls_are_finite_closed_nondegenerate_ellipsoids(self):
        for detail in (1, 2):
            with self.subTest(detail=detail):
                points, faces = self.ball(detail)
                edges = Counter()
                for point in points:
                    self.assertTrue(all(math.isfinite(value) for value in point))
                    radius_squared = sum(((point[i] - self.center[i]) / self.radii[i]) ** 2
                                         for i in range(3))
                    self.assertAlmostEqual(radius_squared, 1.0, places=10)
                for face in faces:
                    self.assertEqual(len(face), 3)
                    self.assertEqual(len(set(face)), 3)
                    self.assertTrue(all(0 <= index < len(points) for index in face))
                    a, b, c = [points[index] for index in face]
                    u = [b[i] - a[i] for i in range(3)]
                    v = [c[i] - a[i] for i in range(3)]
                    cross = (u[1] * v[2] - u[2] * v[1],
                             u[2] * v[0] - u[0] * v[2],
                             u[0] * v[1] - u[1] * v[0])
                    self.assertGreater(sum(value * value for value in cross), 1e-16)
                    edges.update(tuple(sorted((x, y)))
                                 for x, y in zip(face, face[1:] + face[:1]))
                self.assertTrue(all(count == 2 for count in edges.values()))

    def test_default_detail_preserves_coarse_ball(self):
        default = RiverGeometry()
        explicit = RiverGeometry(detail=1)
        for geometry in (default, explicit):
            geometry.ball(self.center, self.radii, self.color)
        self.assertEqual(default.parts, explicit.parts)

    def test_small_rotated_leaf_is_closed_finite_and_nondegenerate(self):
        for length, yaw, tilt in ((0.06, 0, 0), (0.06, 1.73, -0.81),
                                  (0.22, -2.4, math.pi / 2)):
            with self.subTest(length=length, yaw=yaw, tilt=tilt):
                geometry = RiverGeometry(detail=2)
                geometry.leaf(self.center, length, self.color, yaw, tilt, 0.4)
                points, faces = geometry.parts[(self.color, "smooth")]
                self.assertEqual(len(faces), 24)
                edges = Counter()
                for face in faces:
                    self.assertEqual(len(face), 3)
                    self.assertEqual(len(set(face)), 3)
                    self.assertTrue(all(0 <= index < len(points) for index in face))
                    edges.update((a, b) for a, b in zip(face, face[1:] + face[:1]))
                self.assertTrue(all(count == 1 and edges[(b, a)] == 1
                                    for (a, b), count in edges.items()))
                # GLB uses float32: small petals must remain real geometry after export.
                exported = [struct.unpack("<3f", struct.pack("<3f", *point))
                            for point in points]
                for vertices in (points, exported):
                    self.assertTrue(all(math.isfinite(value)
                                        for point in vertices for value in point))
                    volume_six = 0.0
                    for face in faces:
                        a, b, c = [tuple(vertices[index][i] - self.center[i]
                                         for i in range(3)) for index in face]
                        u = [b[i] - a[i] for i in range(3)]
                        v = [c[i] - a[i] for i in range(3)]
                        cross = (u[1] * v[2] - u[2] * v[1],
                                 u[2] * v[0] - u[0] * v[2],
                                 u[0] * v[1] - u[1] * v[0])
                        self.assertGreater(sum(value * value for value in cross), 1e-16)
                        volume_six += sum(a[i] * cross[i] for i in range(3))
                    self.assertGreater(abs(volume_six), 1e-12)

    def test_foliage_smoothing_does_not_round_same_color_thin_frames(self):
        fine = RiverGeometry(detail=2)
        fine.ball(self.center, self.radii, self.color)
        fine.box((0, 0, 0), (1, 0.02, 1), self.color)
        fine.box((0, 0, 0), (1, 1, 1), self.color)
        self.assertIn((self.color, "smooth"), fine.parts)
        self.assertIn((self.color, "flat"), fine.parts)
        self.assertIn((self.color, "bevel"), fine.parts)
        self.assertEqual(len(fine.parts[(self.color, "flat")][1]), 6)
        coarse = RiverGeometry(detail=1)
        coarse.ball(self.center, self.radii, self.color)
        coarse.box((0, 0, 0), (1, 1, 1), self.color)
        self.assertEqual(set(coarse.parts), {(self.color, "flat")})

    def test_fixed_seed_keeps_small_foliage_cluster_deterministic(self):
        for detail in (1, 2):
            with self.subTest(detail=detail):
                outputs = []
                for _ in range(2):
                    geometry = RiverGeometry(detail=detail)
                    for _ in range(12):
                        center = tuple(geometry.rng.uniform(-2, 2) for _ in range(3))
                        radii = tuple(geometry.rng.uniform(0.05, 0.5) for _ in range(3))
                        color = geometry.rng.choice((self.color, "#9d9f55", "#5c753f"))
                        geometry.ball(center, radii, color)
                    outputs.append(geometry.parts)
                self.assertEqual(*outputs)

    def test_refinement_remains_within_existing_recipe_contract(self):
        for detail in (1, 2, 3):
            recipe = {"template": "broadleaf_tree", "detail": detail}
            self.assertEqual(ComponentRecipe.model_validate(recipe).model_dump(exclude={"deformation_handles"}),
                             validate_recipe_data(recipe))
        for detail in (0, 4, True, 2.0, "2"):
            recipe = {"template": "broadleaf_tree", "detail": detail}
            with self.subTest(detail=detail):
                with self.assertRaises(ValueError):
                    ComponentRecipe.model_validate(recipe)
                with self.assertRaises(ValueError):
                    validate_recipe_data(recipe)


if __name__ == "__main__":
    unittest.main()
