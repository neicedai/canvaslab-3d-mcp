import importlib.util
from pathlib import Path
import unittest
from pydantic import ValidationError

from server3d.component_schema import ComponentRecipe

spec = importlib.util.spec_from_file_location("bounded_deformer", Path(__file__).parents[2] / "scripts3d" / "blender_deform.py")
deformer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deformer)


class DeformationRecipeTests(unittest.TestCase):
    def test_recipe_defaults_to_no_shape_edit(self):
        recipe = ComponentRecipe.model_validate({"template": "tiled_roof"})
        self.assertEqual(recipe.deformation_handles, [])

    def test_bounded_handle_round_trips_to_worker_validator(self):
        recipe = ComponentRecipe.model_validate({"template": "tiled_roof", "deformation_handles": [
            {"anchor": [.42, .82, .35], "offset": [0, .12, 0], "radius": .22, "strength": .7}
        ]}).model_dump()
        handles = deformer.validate_handles(recipe)
        self.assertEqual(len(handles), 1)
        self.assertAlmostEqual(handles[0]["offset"][1], .12)

    def test_schema_rejects_unbounded_or_zero_handles(self):
        invalid = [
            {"anchor": [.6,.5,0], "offset": [0,.1,0]},
            {"anchor": [0,1.1,0], "offset": [0,.1,0]},
            {"anchor": [0,.5,0], "offset": [.36,0,0]},
            {"anchor": [0,.5,0], "offset": [0,0,0]},
            {"anchor": [0,.5,0], "offset": [0,.1,0], "radius": .01},
        ]
        for handle in invalid:
            with self.subTest(handle=handle), self.assertRaises(ValidationError):
                ComponentRecipe.model_validate({"template":"broadleaf_tree","deformation_handles":[handle]})
        with self.assertRaises(ValidationError):
            ComponentRecipe.model_validate({"template":"broadleaf_tree","deformation_handles":[{"anchor":[0,.5,0],"offset":[0,.1,0]}]*17})

    def test_worker_validator_rejects_arbitrary_fields(self):
        with self.assertRaises(ValueError):
            deformer.validate_handles({"deformation_handles":[{"anchor":[0,.5,0],"offset":[0,.1,0],"python":"bad"}]})

    def test_compact_radial_basis_moves_nearby_not_distant_points(self):
        handles = deformer.validate_handles({"deformation_handles":[
            {"anchor":[0,.5,0],"offset":[.2,.1,0],"radius":.3,"strength":1}
        ]})
        self.assertEqual(deformer.deform_point((.4,.5,0), handles), (.4,.5,0))
        center = deformer.deform_point((0,.5,0), handles)
        self.assertAlmostEqual(center[0], .2)
        self.assertAlmostEqual(center[1], .6)

    def test_overlapping_handles_have_global_displacement_cap(self):
        handles = deformer.validate_handles({"deformation_handles":[
            {"anchor":[0,.5,0],"offset":[.35,.35,.35],"radius":.9,"strength":1},
            {"anchor":[0,.5,0],"offset":[.35,.35,.35],"radius":.9,"strength":1},
        ]})
        point = deformer.deform_point((0,.5,0), handles)
        delta = sum((point[i] - (0,.5,0)[i])**2 for i in range(3)) ** .5
        self.assertLessEqual(delta, deformer.MAX_TOTAL_DISPLACEMENT + 1e-12)

    def test_normalization_restores_required_unit_bounds(self):
        points=[(-.5,0,-.5),(.5,0,-.5),(-.5,1,.5),(.5,1,.5),(.3,.4,.1)]
        handles=deformer.validate_handles({"deformation_handles":[{"anchor":[.3,.4,.1],"offset":[.1,.2,-.1],"radius":.3}]})
        normalized=deformer._normalize([deformer.deform_point(p,handles) for p in points])
        self.assertEqual([min(p[i] for p in normalized) for i in range(3)],[-.5,0.0,-.5])
        self.assertEqual([max(p[i] for p in normalized) for i in range(3)],[.5,1.0,.5])


if __name__ == '__main__':
    unittest.main()
