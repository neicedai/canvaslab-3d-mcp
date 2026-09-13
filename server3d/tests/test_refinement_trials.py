"""Real child-job transactions with stubbed generation/capture, NOT an E2E render."""
import copy
import unittest
from unittest.mock import patch

from server3d.refinement import refine_component_candidates, install_refinement_tools
from server3d.deformation_fit import project_local_point
from server3d.tests.test_fidelity_evidence import EvidenceFixture


class RefinementTrialTests(EvidenceFixture):
    def setUp(self):
        super().setUp()
        self.plan['objects'][0].update(kind='asset', asset_id='a'*64, rotation=[0.,0.,0.,1.])
        assets=patch.object(self.store.components,'build_assets',return_value={})
        assets.start();self.addCleanup(assets.stop)
        getter=patch.object(self.store.components,'get',return_value={'recipe':{'template':'tiled_roof','detail':2}})
        getter.start();self.addCleanup(getter.stop)
        status=patch.object(self.store,'status',return_value={'runtime_ready':True,'restart_required':False,'blender':{'available':True}})
        status.start();self.addCleanup(status.stop)
        self.base=self.make(self.blur)
        self.original=copy.deepcopy(self.store.job(self.base[0]))
        anchor=[0.,.5,0.]
        # Plan's orthographic default is made explicit for this pure math helper.
        plan=copy.deepcopy(self.plan);plan['camera']['projection']='orthographic'
        current=project_local_point(plan,self.plan['objects'][0],anchor,[64,64],framing_aspect=1.)
        self.landmarks=[{'id':'corner','anchor':anchor,'pixel':(current+[18,16]).tolist(),'evidence':'synthetic target'}]

    def run_trial(self, images, max_candidates=1):
        counter=iter(images)
        def capture(jid,bid):
            image=next(counter)
            if isinstance(image,Exception):raise image
            return self.capture(jid,bid,image)
        with patch.object(self.store.components,'generate',return_value={'asset_id':'b'*64}) as generate:
            with patch.object(self.store,'capture',side_effect=capture):
                result=refine_component_candidates(self.store,self.base[0],'wall',self.source['sha256'],
                                                   self.landmarks,1,self.base[3]['audit_id'],max_candidates)
        self.assertEqual(self.original,self.store.job(self.base[0]))
        self.assertFalse(result['automatic_acceptance'])
        with self.store.transaction() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM records WHERE kind='lease' AND id=?",('refinement:'+self.base[0],)).fetchone())
        return result,generate.call_count

    def test_improving_candidate_selected_without_changing_original(self):
        result,count=self.run_trial([self.exact])
        self.assertEqual(count,1)
        self.assertEqual(result['outcome'],'candidate_available_for_review')
        self.assertNotEqual(result['recommended_job_id'],self.base[0])
        self.assertTrue(result['candidates'][0]['comparison']['cross_job'])

    def test_regression_keeps_original(self):
        from PIL import Image
        result,_=self.run_trial([Image.new('RGB',(64,64),'red')])
        self.assertEqual(result['outcome'],'retained_original')
        self.assertEqual(result['recommended_build_id'],self.original['current_build_id'])

    def test_failed_capture_recorded_and_original_retained(self):
        result,_=self.run_trial([ValueError('synthetic capture failure')])
        self.assertEqual(result['candidates'][0]['status'],'failed')
        self.assertEqual(result['outcome'],'retained_original')
        self.assertIn('job_id',result['candidates'][0])

    def test_multiple_strengths_and_partial_failure_retain_best_evidence(self):
        result,count=self.run_trial([self.exact,ValueError('second failed')],2)
        self.assertEqual(count,2)
        self.assertEqual(result['outcome'],'candidate_available_for_review')
        self.assertEqual(result['recommended_job_id'],result['candidates'][0]['job_id'])
        self.assertEqual([x['gain'] for x in result['candidates']],[.5,1.])

    def test_invalid_limits_stale_revision_or_wrong_source_do_not_generate(self):
        with patch.object(self.store.components,'generate') as generate:
            for n in (0,4,True):
                with self.assertRaises(ValueError):
                    refine_component_candidates(self.store,self.base[0],'wall',self.source['sha256'],self.landmarks,1,self.base[3]['audit_id'],n)
            for digest,revision in [('0'*64,1),(self.source['sha256'],0)]:
                with self.assertRaises(ValueError):
                    refine_component_candidates(self.store,self.base[0],'wall',digest,self.landmarks,revision,self.base[3]['audit_id'])
            generate.assert_not_called()

    def test_missing_runtime_stops_before_generation(self):
        with patch.object(self.store,'status',return_value={'runtime_ready':False}),patch.object(self.store.components,'generate') as generate:
            with self.assertRaisesRegex(ValueError,'Rebuild'):
                refine_component_candidates(self.store,self.base[0],'wall',self.source['sha256'],self.landmarks,1,self.base[3]['audit_id'])
            generate.assert_not_called()

    def test_new_tools_are_registered_by_name(self):
        class Registry:
            def __init__(self):self.tools={}
            def tool(self,**kwargs):
                def wrap(function):self.tools[function.__name__]=function;return function
                return wrap
        registry=Registry()
        install_refinement_tools(registry,self.store,lambda f,*a:f(*a))
        self.assertEqual(set(registry.tools),{'measure_scene_fidelity','refine_scene_component'})


if __name__=='__main__':unittest.main()
