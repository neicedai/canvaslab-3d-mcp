"""Reference appearance is explicit, bounded and independent of render quality."""
import copy
import unittest

from pydantic import ValidationError
from server3d.scene_schema import ReferenceLighting, ScenePlan


def plan():
    return {"title": "Reference test", "camera": {"position": [8., 10., 8.], "target": [0., 0., 0.]},
            "materials": [{"id": "wood", "color": "#684832"}],
            "objects": [{"id": "house", "label": "House", "kind": "box", "region_ids": ["house"],
                         "position": [0., 0., 0.], "dimensions": [1., 1., 1.], "material_id": "wood",
                         "inferred_surfaces": "Back face is not visible in the source."}],
            "assumptions": ["Test fixture, not fidelity evidence."]}


class ReferenceAppearanceTests(unittest.TestCase):
    def test_omitted_mode_preserves_legacy(self):
        for quality in ("standard", "showcase"):
            value = ScenePlan.model_validate(plan() | {"render_quality": quality})
            self.assertEqual(value.appearance_mode, "legacy")
            self.assertIsNone(value.reference_lighting)

    def test_reference_at_both_quality_levels_preserves_authored_fields(self):
        data = plan() | {"appearance_mode": "reference", "reference_lighting": {"sun_color": "#ebdfc4", "sun_intensity": 1.3}}
        before = copy.deepcopy(data)
        standard = ScenePlan.model_validate(data | {"render_quality": "standard"})
        showcase = ScenePlan.model_validate(data | {"render_quality": "showcase"})
        self.assertEqual(standard.materials, showcase.materials)
        self.assertEqual(standard.objects, showcase.objects)
        self.assertEqual(standard.reference_lighting, showcase.reference_lighting)
        self.assertEqual(data, before)

    def test_legacy_cannot_silently_ignore_reference_lighting(self):
        with self.assertRaisesRegex(ValidationError, "requires appearance_mode"):
            ScenePlan.model_validate(plan() | {"reference_lighting": {}})

    def test_lighting_is_bounded_and_rejects_arbitrary_fields(self):
        for data in [{"sun_intensity": float("nan")}, {"exposure": 0.}, {"sun_intensity": 9.},
                     {"hemisphere_intensity": -1.}, {"sky_color": "white"}, {"shader": "run()"},
                     {"tone_mapping": "auto"}, {"sun_position": [0., 0., 0.]},
                     {"sun_position": [1001., 0., 0.]}, {"sun_intensity": "2.0"}]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ReferenceLighting.model_validate(data)

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValidationError):
            ScenePlan.model_validate(plan() | {"appearance_mode": "creative"})

    def test_lighting_defaults_are_independent_objects(self):
        first, second = ReferenceLighting(), ReferenceLighting()
        first.sun_position[0] = 9.
        self.assertEqual(second.sun_position, [-8., 14., 8.])


if __name__ == "__main__":
    unittest.main()
