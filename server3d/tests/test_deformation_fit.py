import unittest
import numpy as np
from server3d.deformation_fit import project_local_point,suggest_deformation_handles


def fixture():
    plan={"appearance_mode":"reference","camera":{"projection":"orthographic","position":[8.,10.,8.],"target":[0.,0.,0.],"vertical_span":10.,"fov":40}}
    analysis={"scene_box":[100,50,800,600]}
    obj={"id":"roof","kind":"asset","asset_id":"a"*64,"position":[0.,0.,0.],"dimensions":[4.,2.,3.],"rotation":[0.,0.,0.,1.]}
    recipe={"template":"tiled_roof","detail":2,"primary_color":"#75513c","secondary_color":"#397e73"}
    return plan,analysis,obj,recipe


class DeformationFitTests(unittest.TestCase):
    def test_exact_pixel_shift_produces_bounded_handle_that_improves_projection(self):
        plan,analysis,obj,recipe=fixture();anchor=[.35,.78,.22]
        current=project_local_point(plan,obj,anchor,[800,600],framing_aspect=800/600)
        target=current+np.array([24.,-18.])
        result=suggest_deformation_handles(plan,analysis,obj,recipe,[{"id":"eave","anchor":anchor,"pixel":[target[0]+100,target[1]+50],"measurement":"measured","radius":.22,"evidence":"synthetic measured eave target"}])
        self.assertLess(result["linearized_after_rms_px"],result["before_rms_px"]*.05)
        offset=result["proposed_recipe"]["deformation_handles"][0]["offset"]
        self.assertTrue(all(abs(v)<=.35 for v in offset))

    def test_estimated_landmark_is_less_aggressive(self):
        plan,analysis,obj,recipe=fixture();anchor=[0,.5,0]
        current=project_local_point(plan,obj,anchor,[800,600],framing_aspect=800/600);target=current+[40,20]
        def solve(measurement):
            return suggest_deformation_handles(plan,analysis,obj,recipe,[{"id":"p","anchor":anchor,"pixel":[target[0]+100,target[1]+50],"measurement":measurement,"evidence":"synthetic"}])["proposed_recipe"]["deformation_handles"][0]
        measured,estimated=solve("measured"),solve("estimated")
        # Blender applies offset * strength. Test the effective move, not a raw
        # offset that would require confidence to be incorrectly applied twice.
        self.assertLess(np.linalg.norm(estimated["offset"])*estimated["strength"],
                        np.linalg.norm(measured["offset"])*measured["strength"])
        self.assertEqual(estimated["strength"],.4)

    def test_rejects_parented_perspective_existing_deformed_and_outside_source(self):
        plan,analysis,obj,recipe=fixture();landmark={"id":"p","anchor":[0,.5,0],"pixel":[400,300],"evidence":"synthetic"}
        for change in ("parent","perspective","existing"):
            p=dict(plan);o=dict(obj);r=dict(recipe)
            if change=="parent":o["parent_id"]="root"
            if change=="perspective":p={**plan,"camera":{**plan["camera"],"projection":"perspective"}}
            if change=="existing":r["deformation_handles"]=[{"anchor":[0,.5,0],"offset":[.1,0,0]}]
            with self.subTest(change=change),self.assertRaises(ValueError):suggest_deformation_handles(p,analysis,o,r,[landmark])
        with self.assertRaises(ValueError):suggest_deformation_handles(plan,analysis,obj,recipe,[{**landmark,"pixel":[20,20]}])

    def test_rotation_and_dimensions_affect_projection_jacobian(self):
        plan,analysis,obj,recipe=fixture();p=[.2,.7,-.1]
        base=project_local_point(plan,obj,p,[800,600],framing_aspect=4/3)
        altered={**obj,"dimensions":[8.,1.,1.],"rotation":[0.,.3826834324,0.,.9238795325]}
        other=project_local_point(plan,altered,p,[800,600],framing_aspect=4/3)
        self.assertGreater(np.linalg.norm(base-other),1.)


if __name__=='__main__':unittest.main()
