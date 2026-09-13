import copy
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from server3d.visual_metrics import visible_metrics, validate_polygon


class VisibleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"id.png"
        self.plan = {"objects": [{"id":"a", "region_ids":["item"]}, {"id":"b", "region_ids":["item"]}]}
        self.analysis = {"scene_box":[10,20,100,100], "regions":[{
            "id":"item", "box":[20,30,40,40], "critical":True,"confidence":.9,
            "visible_polygons":[[[20,30],[60,30],[60,70],[20,70]]]}]}
        image=Image.new("RGB",(100,100)); draw=ImageDraw.Draw(image)
        draw.rectangle((10,10,30,50),fill=(0,0,1)); draw.rectangle((31,10,50,50),fill=(0,0,2))
        image.save(self.path)

    def tearDown(self): self.temp.cleanup()
    def metrics(self): return visible_metrics(self.plan,self.analysis,self.path,"rgb-index-v2-no-msaa")
    def test_exact_union_and_native_crop_offset(self):
        r=self.metrics()["regions"][0]
        self.assertEqual(r["silhouette_iou"],1)
        self.assertEqual(r["visible_box"],[10,10,51,51])
        self.assertTrue(r["requires_manual_review"])
    def test_wrong_shape_not_hidden_by_box(self):
        im=Image.new("RGB",(100,100)); ImageDraw.Draw(im).line((10,10,50,50),fill=(0,0,1)); im.save(self.path)
        r=self.metrics()["regions"][0]
        self.assertLess(r["silhouette_iou"],.05)
        self.assertTrue(r["below_provisional_target"])
    def test_missing_annotation_not_pass(self):
        self.analysis["regions"][0]["visible_polygons"]=[]
        self.assertIsNone(self.metrics()["regions"][0]["silhouette_iou"])
    def test_low_confidence_not_pass(self):
        self.analysis["regions"][0]["confidence"]=.4
        self.assertEqual(self.metrics()["regions"][0]["reason"],"low_confidence_original_annotation")
    def test_hidden_critical(self):
        Image.new("RGB",(100,100)).save(self.path)
        self.assertEqual(self.metrics()["regions"][0]["reason"],"critical_region_not_visible")
    def test_unknown_colors_rejected(self):
        Image.new("RGB",(100,100),(0,0,3)).save(self.path)
        with self.assertRaises(ValueError): self.metrics()
    def test_legacy_capture_cannot_measure_silhouette(self):
        self.assertFalse(visible_metrics(self.plan,self.analysis,self.path,None)["available"])
    def test_dimension_mismatch_rejected(self):
        Image.new("RGB",(50,50)).save(self.path)
        with self.assertRaises(ValueError): self.metrics()
    def test_polygon_validation(self):
        validate_polygon([[0,0],[40,0],[20,20],[40,40],[0,40]],[0,0,40,40])
        for p in [[[0,0],[1,0],[2,0]], [[0,0],[50,0],[0,10]],
                  [[0,0],[30,30],[0,30],[20,0]],[[0,0],[30,0],[0,30],[0,0]]]:
            with self.assertRaises(ValueError): validate_polygon(p,[0,0,40,40])
