"""Joint projection/field tests. No Blender process or final normalization implied."""
import copy
import unittest
import numpy as np
from server3d.deformation_fit import project_local_point, suggest_deformation_handles, apply_handle_field


def fixture():
    plan={'appearance_mode':'reference','camera':{'projection':'orthographic','position':[8.,10.,8.],
          'target':[0.,0.,0.],'vertical_span':10.,'fov':40.}}
    analysis={'scene_box':[100,50,800,600]}
    obj={'id':'roof','kind':'asset','asset_id':'a'*64,'position':[0.,0.,0.],
         'dimensions':[4.,2.,3.],'rotation':[0.,0.,0.,1.]}
    return plan, analysis, obj, {'template':'tiled_roof','detail':2}


class JointDeformationTests(unittest.TestCase):
    def setUp(self):
        self.plan,self.analysis,self.obj,self.recipe=fixture()

    def point(self, anchor, delta, name='eave', measurement='measured', radius=.4):
        pixel=project_local_point(self.plan,self.obj,anchor,[800,600],framing_aspect=4/3)+delta+[100,50]
        return {'id':name,'anchor':anchor,'pixel':pixel.tolist(),'measurement':measurement,
                'radius':radius,'evidence':'synthetic source feature'}

    def solve(self, points):
        return suggest_deformation_handles(self.plan,self.analysis,self.obj,self.recipe,points)

    def test_single_measured_shift_is_accurate(self):
        result=self.solve([self.point([.35,.78,.22],[24,-18])])
        self.assertLess(result['linearized_after_rms_px'], .01)

    def test_estimated_point_attenuated_once_and_prediction_matches_field(self):
        anchor=[0.,.5,0.]
        point=self.point(anchor,[24,-18],measurement='estimated')
        result=self.solve([point]);handles=result['proposed_recipe']['deformation_handles']
        actual=project_local_point(self.plan,self.obj,apply_handle_field(anchor,handles),[800,600],framing_aspect=4/3)
        before=project_local_point(self.plan,self.obj,anchor,[800,600],framing_aspect=4/3)
        np.testing.assert_allclose(actual-before, np.asarray([24,-18])*.4, atol=.001)
        np.testing.assert_allclose(actual,result['landmark_diagnostics'][0]['predicted_crop_pixel'],atol=1e-9)

    def test_overlapping_opposite_moves_jointly_fit(self):
        points=[self.point([-.08,.5,0.],[-5,0],'left'),self.point([.08,.5,0.],[5,0],'right')]
        result=self.solve(points)
        self.assertLess(result['linearized_after_rms_px'], .02)
        for p,d in zip(points,result['landmark_diagnostics']):
            actual=project_local_point(self.plan,self.obj,apply_handle_field(p['anchor'],result['proposed_recipe']['deformation_handles']),[800,600],framing_aspect=4/3)
            np.testing.assert_allclose(actual,d['predicted_crop_pixel'],atol=1e-9)

    def test_fixed_landmark_stays_fixed_beside_moving_point(self):
        result=self.solve([self.point([-.08,.5,0.],[0,0],'fixed'),self.point([.08,.5,0.],[5,0],'moving')])
        self.assertLess(result['landmark_diagnostics'][0]['linearized_after_error_px'], .02)

    def test_noop_returns_unchanged_recipe_instead_of_invalid_zero_handle(self):
        result=self.solve([self.point([0.,.5,0.],[0,0])])
        self.assertTrue(result['no_op'])
        self.assertEqual(result['proposed_recipe']['deformation_handles'],[])

    def test_duplicate_anchors_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate component anchors'):
            self.solve([self.point([0.,.5,0.],[5,0],'a'),self.point([0.,.5,0.],[-5,0],'b')])

    def test_large_moves_remain_bounded(self):
        result=self.solve([self.point([0.,.5,0.],[250,100])])
        for h in result['proposed_recipe']['deformation_handles']:
            self.assertLessEqual(max(abs(v) for v in h['offset']),.35)
            self.assertLessEqual(np.linalg.norm(h['offset']),.45000001)

    def test_no_input_mutation(self):
        values=copy.deepcopy((self.plan,self.analysis,self.obj,self.recipe))
        self.solve([self.point([0.,.5,0.],[5,0])])
        self.assertEqual(values,(self.plan,self.analysis,self.obj,self.recipe))

    def test_invalid_transform_evidence_or_existing_deformation_rejected(self):
        point=self.point([0.,.5,0.],[5,0])
        with self.assertRaises(ValueError):self.solve([{**point,'evidence':'   '}])
        for key,value in [('dimensions',[0.,1.,1.]),('position',[float('nan'),0.,0.]),('rotation',[0.,0.,0.,0.])]:
            old=self.obj[key];self.obj[key]=value
            with self.assertRaises(ValueError):self.solve([point])
            self.obj[key]=old
        self.recipe['deformation_handles']=[{'anchor':[0.,.5,0.],'offset':[.1,0.,0.]}]
        with self.assertRaises(ValueError):self.solve([point])

    def test_total_displacement_cap_matches_worker_contract(self):
        h={'anchor':[0.,.5,0.],'offset':[.35,.35,.35],'strength':1.,'radius':.5}
        self.assertAlmostEqual(np.linalg.norm(apply_handle_field([0.,.5,0.],[h,h])-[0.,.5,0.]),.45)


if __name__=='__main__':unittest.main()
