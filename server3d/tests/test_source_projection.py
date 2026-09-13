"""Native texture packaging; these are not GPU or Blender tests."""
import copy
import hashlib
import io
import json
import unittest
from PIL import Image
from server3d.scene_schema import ScenePlan
from server3d.source_projection import projection_assets


def fixture():
    image=Image.new('RGB',(100,100),'#8d4d1a');image.putpixel((25,30),(5,41,201))
    stream=io.BytesIO();image.save(stream,'PNG');payload=stream.getvalue();digest=hashlib.sha256(payload).hexdigest()
    source={'sha256':digest,'width':100,'height':100}
    analysis={'source_sha256':digest,'scene_box':[10,10,80,80],'regions':[{'id':'surface','label':'Test','box':[10,10,80,80],
        'confidence':1.,'evidence':'Synthetic','visible_polygons':[[[10,10],[89,10],[89,89],[10,89]]],
        'visible_holes':[[[40,40],[50,40],[50,50],[40,50]]]}],'assumptions':[],'change_reason':'Test'}
    plan={'title':'Projection','appearance_mode':'reference','reference_lighting':{'tone_mapping':'none','exposure':1.},
        'reference_projection':{'object_ids':['crate']},'camera':{'position':[5.,5.,5.],'target':[0.,1.,0.]},
        'materials':[{'id':'wood','color':'#775544'}],'objects':[{'id':'crate','kind':'asset','asset_id':'a'*64,'label':'Crate',
        'region_ids':['surface'],'position':[0.,0.,0.],'dimensions':[2.,2.,2.],'material_id':'wood','inferred_surfaces':'Not visible'}],
        'assumptions':['Back inferred']}
    return plan,source,analysis,payload


class SourceProjectionTests(unittest.TestCase):
    def test_native_crop_exact_pixels_hole_and_metadata(self):
        p,s,a,b=fixture();files=projection_assets(p,s,a,b)
        with Image.open(io.BytesIO(files['reference-source.png'])) as im:
            self.assertEqual(im.size,(80,80));self.assertEqual(im.getpixel((15,20)),(5,41,201))
        with Image.open(io.BytesIO(files['reference-mask-crate.png'])) as im:
            self.assertEqual(im.getpixel((35,35)),(0,0,0));self.assertEqual(im.getpixel((10,10)),(255,255,255))
        meta=json.loads(files['reference-projection.json']);self.assertFalse(meta['resampled']);self.assertFalse(meta['albedo_recovered'])
        self.assertEqual(meta['source_sha256'],hashlib.sha256(b).hexdigest())

    def test_missing_or_changed_original_fails(self):
        p,s,a,b=fixture()
        for bad in (None,b'bad',b+b' '):
            with self.subTest(value=type(bad)),self.assertRaises(ValueError):projection_assets(p,s,a,bad)

    def test_source_ownership_and_confidence_required(self):
        for variant in ('no_mask','confidence','wrong_source'):
            p,s,a,b=fixture()
            if variant=='no_mask':a['regions'][0].update(visible_polygons=[],visible_holes=[])
            elif variant=='confidence':a['regions'][0]['confidence']=.6
            else:a['source_sha256']='b'*64
            with self.subTest(variant=variant),self.assertRaises(ValueError):projection_assets(p,s,a,b)

    def test_integer_crop_no_silent_resampling(self):
        p,s,a,b=fixture();a['scene_box'][2]=80.5
        with self.assertRaisesRegex(ValueError,'integer native crop'):projection_assets(p,s,a,b)

    def test_no_exposure_or_style_change_silently_allowed(self):
        p,*_=fixture()
        for update in ({'appearance_mode':'legacy'},{'reference_lighting':None},
                       {'reference_lighting':{'tone_mapping':'aces'}},{'reference_lighting':{'tone_mapping':'none','exposure':2.}}):
            with self.subTest(update=update),self.assertRaises(ValueError):ScenePlan.model_validate({**p,**update})

    def test_unknown_duplicate_parented_or_nonasset_target_rejected(self):
        for variant in ('unknown','duplicate','parent','box','perspective'):
            p,*_=fixture()
            if variant=='unknown':p['reference_projection']['object_ids']=['unknown']
            elif variant=='duplicate':p['reference_projection']['object_ids']=['crate','crate']
            elif variant=='parent':p['objects'][0]['parent_id']='crate'
            elif variant=='perspective':p['camera']['projection']='perspective'
            else:p['objects'][0].update(kind='box',asset_id=None)
            with self.subTest(variant=variant),self.assertRaises(ValueError):ScenePlan.model_validate(p)

    def test_transparency_and_orientation_rejected(self):
        for mode in ('transparent','exif'):
            p,s,a,b=fixture();im=Image.new('RGBA' if mode=='transparent' else 'RGB',(100,100))
            stream=io.BytesIO()
            if mode=='exif':exif=Image.Exif();exif[274]=6;im.save(stream,'PNG',exif=exif)
            else:im.save(stream,'PNG')
            b=stream.getvalue();s['sha256']=a['source_sha256']=hashlib.sha256(b).hexdigest()
            with self.subTest(mode=mode),self.assertRaises(ValueError):projection_assets(p,s,a,b)

    def test_repeatable_and_no_input_mutation(self):
        p,s,a,b=fixture();before=copy.deepcopy((p,s,a))
        self.assertEqual(projection_assets(p,s,a,b),projection_assets(p,s,a,b));self.assertEqual((p,s,a),before)

    def test_legacy_no_original_packaged(self):
        p,s,a,b=fixture();p.pop('reference_projection')
        self.assertEqual(projection_assets(p,s,a,None),{})

    def test_encoded_budget_does_not_drop_original(self):
        from unittest.mock import patch
        p,s,a,b=fixture()
        with patch('server3d.source_projection.MAX_BYTES',1),self.assertRaisesRegex(ValueError,'encoded byte'):projection_assets(p,s,a,b)

    def test_build_binds_source_and_masks_to_manifest(self):
        from unittest.mock import patch
        from server3d.builder import build_files,sha
        p,s,a,b=fixture()
        with patch('server3d.builder.runtime_files',return_value={'index.html':b'test'}):
            _,files,manifest=build_files(p,s,a,'job','plan',source_bytes=b)
        self.assertEqual(manifest['files']['reference-source.png'],sha(files['reference-source.png']))
        self.assertIn('reference-mask-crate.png',manifest['files'])
        self.assertIn('reference-projection.json',manifest['files'])
