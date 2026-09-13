import unittest

from server3d.fidelity_compare import compare_visible_quality


def quality(values, *, unscored=()):
    return {"available": True, "regions": [
        {"region_id": identity, "critical": identity != "background",
         "silhouette_iou": None if identity in unscored else value}
        for identity, value in values.items()
    ]}


class FidelityCompareTests(unittest.TestCase):
    def test_prefers_meaningful_improvement_without_regression(self):
        result = compare_visible_quality(
            quality({"roof": .60, "tree": .70, "background": .80}),
            quality({"roof": .63, "tree": .72, "background": .81}))
        self.assertEqual(result["recommendation"], "prefer_candidate")
        self.assertGreater(result["mean_delta_iou"], 0)
        self.assertFalse(result["automatic_acceptance"])

    def test_critical_regression_forces_rollback_even_when_global_mean_rises(self):
        result = compare_visible_quality(
            quality({"roof": .80, "tree": .40, "background": .40}),
            quality({"roof": .79, "tree": .80, "background": .80}))
        self.assertEqual(result["recommendation"], "rollback_recommended")
        self.assertIn("roof", result["critical_regressions"])

    def test_major_noncritical_regression_also_rolls_back(self):
        result = compare_visible_quality(
            quality({"roof": .80, "tree": .80, "background": .80}),
            quality({"roof": .81, "tree": .81, "background": .70}))
        self.assertEqual(result["recommendation"], "rollback_recommended")
        self.assertIn("background", result["major_regressions"])

    def test_small_change_is_inconclusive(self):
        result = compare_visible_quality(quality({"roof": .80, "tree": .80}),
                                         quality({"roof": .802, "tree": .802}))
        self.assertEqual(result["recommendation"], "inconclusive")

    def test_unscored_critical_region_blocks_preference(self):
        result = compare_visible_quality(
            quality({"roof": .80, "background": .50}, unscored={"roof"}),
            quality({"roof": .90, "background": .80}, unscored={"roof"}))
        self.assertEqual(result["recommendation"], "inconclusive")
        self.assertIn("roof", result["unscored_critical_regions"])

    def test_mismatched_regions_are_rejected(self):
        with self.assertRaises(ValueError):
            compare_visible_quality(quality({"roof": .8}), quality({"tree": .8}))

    def test_unavailable_evidence_is_inconclusive(self):
        result = compare_visible_quality({"available": False}, quality({"roof": .8}))
        self.assertFalse(result["comparable"])
        self.assertEqual(result["recommendation"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
