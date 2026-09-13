import unittest

from pydantic import ValidationError

from scripts3d.blender_components import validate_recipe_data
from server3d.component_schema import ComponentRecipe, COMPONENT_TEMPLATES
from server3d.scene_schema import primitive_triangle_budget


class ComponentSchemaTests(unittest.TestCase):
    def test_water_budget_includes_showcase_lily_geometry(self):
        self.assertEqual(primitive_triangle_budget("water"),2500)
        self.assertEqual(primitive_triangle_budget("water","showcase"),5000)
        self.assertEqual(primitive_triangle_budget("sign","showcase"),14)

    def test_bounded_templates_and_defaults_match_worker(self):
        for template in COMPONENT_TEMPLATES:
            recipe = {"template": template}
            self.assertEqual(ComponentRecipe.model_validate(recipe).model_dump(), validate_recipe_data(recipe))

    def test_recipe_rejects_execution_and_paths(self):
        for extra in ({"python": "print(1)"}, {"path": "../../x.glb"}, {"url": "https://example.org/model"}):
            recipe = {"template": "open_gate", **extra}
            with self.assertRaises(ValidationError):
                ComponentRecipe.model_validate(recipe)
            with self.assertRaises(ValueError):
                validate_recipe_data(recipe)

    def test_worker_and_schema_reject_invalid_values(self):
        cases = [{"template": "custom"}, {}, {"template": "open_gate", "detail": True},
                 {"template": "open_gate", "detail": 1.0}, {"template": "open_gate", "detail": 0},
                 {"template": "open_gate", "detail": 4}, {"template": "open_gate", "primary_color": "red"},
                 {"template": "open_gate", "secondary_color": "#ffffff00"}]
        for recipe in cases:
            with self.subTest(recipe=recipe):
                with self.assertRaises(ValidationError):
                    ComponentRecipe.model_validate(recipe)
                with self.assertRaises(ValueError):
                    validate_recipe_data(recipe)

    def test_detail_two_custom_palette(self):
        recipe = {"template": "tiled_roof", "detail": 2,
                  "primary_color": "#abCD12", "secondary_color": "#00ff33"}
        self.assertEqual(ComponentRecipe.model_validate(recipe).model_dump(), validate_recipe_data(recipe))

    def test_showcase_recipes_match_fixed_worker(self):
        for template in COMPONENT_TEMPLATES:
            recipe = {"template": template, "detail": 3}
            with self.subTest(template=template):
                self.assertEqual(ComponentRecipe.model_validate(recipe).model_dump(), validate_recipe_data(recipe))


if __name__ == "__main__":
    unittest.main()
