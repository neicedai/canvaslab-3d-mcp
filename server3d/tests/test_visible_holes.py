import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from server3d.scene_schema import validate_analysis
from server3d.visual_metrics import validate_hole, visible_metrics


OUTER = [[20,30],[60,30],[60,70],[20,70]]
HOLE = [[35,45],[45,45],[45,55],[35,55]]


class VisibleHoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "id.png"
        self.plan = {"objects": [{"id":"building", "region_ids":["facade"]}]}
        self.analysis = {"scene_box":[10,20,100,100], "regions":[{
            "id":"facade", "box":[20,30,40,40], "critical":True, "confidence":.95,
            "visible_polygons":[OUTER], "visible_holes":[HOLE]}]}

    def tearDown(self):
        self.temp.cleanup()

    def draw_actual(self, *, fill_hole=False):
        image=Image.new("RGB",(100,100));draw=ImageDraw.Draw(image)
        # Native source outer becomes crop-local [10,10]..[50,50].
        draw.rectangle((10,10,50,50),fill=(0,0,1))
        if not fill_hole:
            draw.rectangle((25,25,35,35),fill=(0,0,0))
        image.save(self.path)

    def test_exact_visible_opening_reaches_perfect_iou(self):
        self.draw_actual()
        result=visible_metrics(self.plan,self.analysis,self.path,"rgb-index-v2-no-msaa")["regions"][0]
        self.assertEqual(result["silhouette_iou"],1)
        self.assertEqual(result["source_hole_count"],1)

    def test_filling_a_source_opening_is_penalized(self):
        self.draw_actual(fill_hole=True)
        result=visible_metrics(self.plan,self.analysis,self.path,"rgb-index-v2-no-msaa")["regions"][0]
        self.assertLess(result["silhouette_iou"],1)

    def test_hole_must_be_strictly_inside_one_outer_polygon(self):
        validate_hole(HOLE,[OUTER],[20,30,40,40])
        for bad in ([[15,45],[25,45],[25,55],[15,55]],
                    [[20,45],[30,45],[30,55],[20,55]]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate_hole(bad,[OUTER],[10,30,60,40])

    def test_source_analysis_rejects_invalid_hole_before_persistence(self):
        source={"sha256":"a"*64,"width":200,"height":200}
        base={"source_sha256":"a"*64,"scene_box":[0,0,100,100],"assumptions":[],"change_reason":"test",
              "regions":[{"id":"facade","label":"facade","box":[20,30,40,40],"critical":True,
                          "confidence":.95,"evidence":"manual source annotation",
                          "visible_polygons":[OUTER],"visible_holes":[[[20,45],[30,45],[30,55],[20,55]]]}]}
        with self.assertRaises(ValueError):
            validate_analysis(base,source)


if __name__ == "__main__":
    unittest.main()
