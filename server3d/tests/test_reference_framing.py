"""Camera fitting and browser framing must use the same original crop aspect."""
import copy
import itertools
import unittest
import numpy as np
from server3d.scene_schema import Camera
from server3d.camera_fit import project_box, suggest_camera
from server3d.landmark_fit import project_orthographic_points, fit_orthographic_landmarks


class ReferenceFramingTests(unittest.TestCase):
    def setUp(self):
        self.camera = Camera(position=[8., 10., 8.], target=[0., 0., 0.], vertical_span=12.).model_dump()
        self.world = [list(p) for p in itertools.product([-2., 2.], [0., 2.], [-2., 2.])]

    def test_aabb_and_landmark_projections_agree_at_portrait_square_landscape(self):
        for size in ([600,1200], [900,900], [1200,600]):
            for aspect in (1.45, size[0]/size[1]):
                points=np.asarray(project_orthographic_points(size,self.world,self.camera,framing_aspect=aspect))
                expected=[*points.min(axis=0),*(points.max(axis=0)-points.min(axis=0))]
                actual=project_box(self.camera,[[-2.,0.,-2.],[2.,2.,2.]],size,framing_aspect=aspect)
                np.testing.assert_allclose(actual,expected,atol=1e-8)

    def test_legacy_projection_default_is_unchanged(self):
        self.assertEqual(project_orthographic_points([600,1200],self.world,self.camera),
                         project_orthographic_points([600,1200],self.world,self.camera,framing_aspect=1.45))

    def test_reference_landmark_fit_recovers_scale_without_legacy_inflation(self):
        size=[600,1200];aspect=.5
        pixels=project_orthographic_points(size,self.world,self.camera,framing_aspect=aspect)
        landmarks=[{"id":f"p{i}","world":p,"pixel":q,"measurement":"measured","evidence":"Synthetic exact projection; not source-image evidence."} for i,(p,q) in enumerate(zip(self.world,pixels))]
        initial=copy.deepcopy(self.camera);initial["vertical_span"]=15.
        result=fit_orthographic_landmarks(size,landmarks,initial,framing_aspect=aspect)
        self.assertLess(result["rms_px"],1e-5)
        self.assertAlmostEqual(result["suggested_camera"]["vertical_span"],12.,places=5)
        self.assertEqual(initial["vertical_span"],15.)

    def test_aabb_advisory_verifies_reference_capture_in_portrait_mode(self):
        size=[600,1200];aspect=.5
        worlds=[[[-3.,0.,-3.],[-2.,1.,-2.]],[[2.,0.,2.],[3.,1.,3.]],[[2.,4.,-3.],[3.,5.,-2.]]]
        objects=[];regions=[];captured={}
        for i,world in enumerate(worlds):
            identity=f"object{i}";box=project_box(self.camera,world,size,framing_aspect=aspect)
            objects.append({"id":identity,"region_ids":[identity]})
            regions.append({"id":identity,"confidence":1.,"box":box})
            captured[identity]={"world_box":world,"screen_box":box}
        plan={"camera":self.camera,"objects":objects,"appearance_mode":"reference"}
        result=suggest_camera(plan,{"scene_box":[0,0,*size],"regions":regions},{"native_size":size,"reference":{"objects":captured}})
        self.assertAlmostEqual(result["before_loss"],0.)
        self.assertFalse(result["applied"])
        with self.assertRaisesRegex(ValueError,"does not match"):
            suggest_camera(plan|{"appearance_mode":"legacy"},{"scene_box":[0,0,*size],"regions":regions},{"native_size":size,"reference":{"objects":captured}})
