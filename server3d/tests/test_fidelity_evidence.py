"""Real Store/manifests/signatures with explicitly synthetic capture images."""
import copy
import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFilter

from server3d.builder import canonical, sha
from server3d.jobs import Store
from server3d.fidelity_compare import evaluate_scene_candidate, measure_scene_fidelity


class EvidenceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        # Synthetic runtime bytes only. These tests do not run Chrome or Blender.
        runtime = patch('server3d.builder.runtime_files', return_value={'index.html':b'synthetic test runtime'})
        runtime.start();self.addCleanup(runtime.stop)
        self.image = Image.new('RGB', (96,96), '#808080')
        draw = ImageDraw.Draw(self.image)
        for x in range(20,75,8):draw.rectangle((x,20,x+2,74),fill='#101010')
        output=io.BytesIO();self.image.save(output,'PNG')
        self.source=self.store.upload(output.getvalue())
        self.analysis={'source_sha256':self.source['sha256'],'scene_box':[16,16,64,64],
                       'assumptions':[],'change_reason':'fixture',
                       'regions':[{'id':'wall','label':'Wall','box':[20,20,55,55],'critical':True,
                                   'confidence':.95,'evidence':'synthetic measured source',
                                   'visible_polygons':[[[20,20],[75,20],[75,75],[20,75]]]}]}
        self.plan={'title':'Fixture','appearance_mode':'reference',
                   'camera':{'position':[8.,10.,8.],'target':[0.,0.,0.],'vertical_span':10.},
                   'materials':[{'id':'wood','color':'#684832'}],
                   'objects':[{'id':'wall','label':'Wall','kind':'box','region_ids':['wall'],
                               'position':[0.,0.,0.],'dimensions':[4.,2.,3.],'material_id':'wood',
                               'inferred_surfaces':'Synthetic back face.'}],
                   'assumptions':['Synthetic; no real rendering evidence.']}
        self.exact=self.image.crop((16,16,80,80))
        self.blur=self.exact.filter(ImageFilter.GaussianBlur(2))

    def capture(self, job_id, build_id, image, *, failed=False, age=0):
        job=self.store.job(job_id)
        with self.store.transaction() as db:
            plan=self.store.get(db,'plan',job['current_plan_id'])['data']
            analysis=self.store.get(db,'analysis',job['analysis_id'])['data']
        cid=uuid.uuid4().hex;folder=self.store.root/'captures'/cid;folder.mkdir(parents=True)
        image.save(folder/'reference.png')
        ids=Image.new('RGB',(64,64));ImageDraw.Draw(ids).rectangle((4,4,59,59),fill=(0,0,1));ids.save(folder/'id.png')
        observation={'native_size':[64,64],'id_encoding':'rgb-index-v2-no-msaa','build_id':build_id,
                     'reference':{'build_id':build_id,'playing':False,'dusk':False,'time':0,
                                  'objects':{o['id']:{'screen_box':[4,4,56,56]} for o in plan['objects']}},
                     'tests':[{'name':'synthetic_capture_fixture','passed':not failed}], 'errors':[]}
        (folder/'observation.json').write_bytes(canonical(observation))
        capture={'capture_id':cid,'job_id':job_id,'build_id':build_id,'created_at':time.time()-age,
                 'nonce':uuid.uuid4().hex,'worker':'synthetic-test-only','production_attestation':False,
                 'files':{p.name:sha(p.read_bytes()) for p in folder.iterdir()},'observation':observation}
        self.resign(capture)
        return capture

    def resign(self, capture):
        value={k:v for k,v in capture.items() if k!='signature'}
        capture['signature']=hmac.new(self.store.secret,canonical(value),hashlib.sha256).hexdigest()
        with self.store.transaction() as db:self.store.put(db,'capture',capture['capture_id'],capture)

    def make(self, image, *, analysis=None, source=None, failed=False):
        source=source or self.source
        job=self.store.submit(source['asset_id'],'Synthetic fidelity test',uuid.uuid4().hex)
        jid=job['job_id']
        self.store.save_analysis(jid,copy.deepcopy(analysis or self.analysis),0)
        validated=self.store.validate(jid,copy.deepcopy(self.plan),1,0)
        build=self.store.build(jid,validated['plan_id'],uuid.uuid4().hex)
        capture=self.capture(jid,build['build_id'],image,failed=failed)
        findings=[{'object_id':o['id'],'difference':'synthetic test','severity':'minor','planned_fix':'synthetic'} for o in self.plan['objects']]
        findings.append({'object_id':'overall','difference':'synthetic test','severity':'minor','planned_fix':'synthetic'})
        audit=self.store.audit(jid,capture['capture_id'],findings)
        return jid, build, capture, audit


class FidelityEvidenceTests(EvidenceFixture):
    def test_cross_job_same_measured_source_recomputes_baseline(self):
        a=self.make(self.blur)
        altered=copy.deepcopy(self.analysis);altered['change_reason']='new child, identical measurements'
        b=self.make(self.exact,analysis=altered)
        # Forged cached score must not be used; source pixels and IDs are authoritative.
        with self.store.transaction() as db:
            audit=self.store.get(db,'audit',a[3]['audit_id']);audit['visible_quality']={'available':False}
            self.store.put(db,'audit',audit['audit_id'],audit)
        result=evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])
        self.assertTrue(result['cross_job']);self.assertTrue(result['baseline_recomputed'])
        self.assertEqual(result['recommendation'],'prefer_candidate')
        self.assertFalse(result['automatic_acceptance'])

    def test_old_audited_baseline_allowed_but_new_capture_must_be_fresh(self):
        a=self.make(self.blur);b=self.make(self.exact)
        a[2]['created_at']-=3600;self.resign(a[2])
        evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])
        b[2]['created_at']-=3600;self.resign(b[2])
        with self.assertRaisesRegex(ValueError,'expired'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_changed_measurements_confidence_or_holes_are_not_comparable(self):
        a=self.make(self.blur)
        for change in ({'confidence':.9}, {'visible_holes':[[[35,35],[55,35],[55,55],[35,55]]]},
                       {'visible_polygons':[[[21,20],[75,20],[75,75],[20,75]]]}):
            data=copy.deepcopy(self.analysis);data['regions'][0].update(change)
            b=self.make(self.exact,analysis=data)
            with self.assertRaisesRegex(ValueError,'differ'):
                evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_source_sha_change_rejected_even_same_size(self):
        a=self.make(self.blur)
        image=Image.new('RGB',(96,96),'red');buf=io.BytesIO();image.save(buf,'PNG')
        source=self.store.upload(buf.getvalue());data=copy.deepcopy(self.analysis);data['source_sha256']=source['sha256']
        b=self.make(self.exact,analysis=data,source=source)
        with self.assertRaisesRegex(ValueError,'differ'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_bad_baseline_signature_rejected(self):
        a=self.make(self.blur);b=self.make(self.exact)
        with self.store.transaction() as db:
            record=self.store.get(db,'capture',a[2]['capture_id']);record['signature']='0'*64
            self.store.put(db,'capture',record['capture_id'],record)
        with self.assertRaisesRegex(ValueError,'signature'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_changed_baseline_image_rejected(self):
        a=self.make(self.blur);b=self.make(self.exact)
        self.exact.save(self.store.root/'captures'/a[2]['capture_id']/'reference.png')
        with self.assertRaisesRegex(ValueError,'changed'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_failed_baseline_checks_rejected(self):
        a=self.make(self.blur,failed=True);b=self.make(self.exact)
        with self.assertRaisesRegex(ValueError,'passing'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_failed_candidate_checks_rejected(self):
        a=self.make(self.blur);b=self.make(self.exact,failed=True)
        with self.assertRaisesRegex(ValueError,'passing'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_changed_build_bytes_rejected(self):
        a=self.make(self.blur);b=self.make(self.exact)
        path=self.store.root/'builds'/a[0]/a[1]['build_id']/'scene.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):
            evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])

    def test_fine_detail_regression_rejects_equal_silhouette(self):
        a=self.make(self.exact);b=self.make(self.blur)
        result=evaluate_scene_candidate(self.store,b[0],b[2]['capture_id'],a[3]['audit_id'])
        self.assertEqual(result['silhouette_recommendation'],'inconclusive')
        self.assertEqual(result['recommendation'],'rollback_recommended')

    def test_measurement_returns_source_patch_coordinates_and_unchanged_job(self):
        a=self.make(self.blur);before=self.store.job(a[0])
        result=measure_scene_fidelity(self.store,a[0],a[2]['capture_id'])
        self.assertTrue(result['appearance']['worst_patches'])
        self.assertEqual(before,self.store.job(a[0]))


if __name__=='__main__':unittest.main()
