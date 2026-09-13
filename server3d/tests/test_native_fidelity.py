"""Real raster comparisons on synthetic images; no browser/Blender claims."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from server3d.reference_metrics import measure_reference_appearance, compare_appearance, source_mask
from server3d.fidelity_evidence import reference_identity


class NativeFidelityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.png'
        self.capture = self.root/'capture.png'
        self.image = Image.new('RGB', (96, 96), '#808080')
        draw = ImageDraw.Draw(self.image)
        for x in range(20, 75, 8):
            draw.rectangle((x, 20, x+2, 74), fill='#101010')
        self.image.save(self.source)
        self.analysis = {'source_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         'scene_box':[16, 16, 64, 64], 'change_reason':'test', 'assumptions':[],
                         'regions':[{'id':'wall', 'label':'Wall', 'box':[20, 20, 55, 55],
                                     'critical':True, 'confidence':.95, 'evidence':'synthetic measured pattern',
                                     'visible_polygons':[[[20,20],[75,20],[75,75],[20,75]]]}]}
        self.image.crop((16, 16, 80, 80)).save(self.capture)

    def measure(self):
        return measure_reference_appearance(self.source, self.capture, self.analysis)

    def test_exact_native_crop_is_zero(self):
        result = self.measure()
        self.assertEqual(result['mean_region_loss'], 0)
        self.assertEqual(result['native_size'], [64, 64])
        self.assertFalse(result['acceptance_supported'])

    def test_blur_loses_detail_even_with_identical_silhouette(self):
        exact = self.measure()
        self.image.crop((16,16,80,80)).filter(ImageFilter.GaussianBlur(2)).save(self.capture)
        blurred = self.measure()
        self.assertGreater(blurred['regions'][0]['detail_error'], .001)
        self.assertGreater(blurred['regions'][0]['edge_error'], .001)
        self.assertEqual(compare_appearance(exact, blurred)['recommendation'], 'rollback_recommended')
        self.assertEqual(compare_appearance(blurred, exact)['recommendation'], 'prefer_candidate')

    def test_wrong_color_is_not_hidden_by_matching_outline(self):
        Image.new('RGB', (64,64), '#ff0000').save(self.capture)
        result = self.measure()
        self.assertGreater(result['regions'][0]['color_error'], .1)
        self.assertGreater(result['mean_region_loss'], .04)

    def test_shifted_fine_lines_are_not_automatically_registered(self):
        with Image.open(self.capture) as image:
            shifted = Image.new('RGB', image.size, '#808080')
            shifted.paste(image, (2, 0))
            shifted.save(self.capture)
        self.assertGreater(self.measure()['regions'][0]['edge_error'], .01)

    def test_worst_patch_coordinates_are_on_original_not_crop(self):
        image = self.image.crop((16,16,80,80))
        ImageDraw.Draw(image).rectangle((36,36,54,54), fill='white')
        image.save(self.capture)
        worst = self.measure()['worst_patches'][0]['source_box']
        self.assertGreaterEqual(worst[0], 48)
        self.assertGreaterEqual(worst[1], 48)

    def test_holes_subtract_original_pixels_not_generated_masks(self):
        region = self.analysis['regions'][0]
        region['visible_holes'] = [[[35,35],[55,35],[55,55],[35,55]]]
        mask = source_mask(region, self.analysis['scene_box'], (64,64))
        self.assertEqual(mask.getpixel((24,24)), 0)
        self.assertEqual(mask.getpixel((8,8)), 255)
        result = self.measure()
        self.assertLess(result['regions'][0]['source_visible_pixels'], 56*56)

    def test_missing_or_low_confidence_masks_not_pass(self):
        for change in ({'visible_polygons':[]}, {'confidence':.4}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.analysis)
                self.analysis['regions'][0].update(change)
                self.assertFalse(self.measure()['available'])
                self.analysis = original

    def test_wrong_capture_size_rejected(self):
        Image.new('RGB', (32,32)).save(self.capture)
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            self.measure()

    def test_changed_source_rejected(self):
        Image.new('RGB', (96,96)).save(self.source)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.measure()

    def test_transparency_requires_explicit_composite(self):
        Image.new('RGBA', (64,64), (1,2,3,0)).save(self.capture)
        with self.assertRaisesRegex(ValueError, 'opaque'):
            self.measure()

    def test_changed_crop_not_comparable(self):
        a = self.measure()
        b = copy.deepcopy(a)
        b['source_crop'][0] += 1
        with self.assertRaises(ValueError):
            compare_appearance(a,b)

    def test_reference_identity_ignores_narration_but_not_measurements(self):
        source = {'sha256':self.analysis['source_sha256'], 'width':96, 'height':96}
        original = reference_identity(self.analysis, source)
        updated = copy.deepcopy(self.analysis)
        updated['change_reason'] = 'new child job'
        updated['regions'][0]['label'] = 'Renamed wall'
        self.assertEqual(original, reference_identity(updated, source))
        for key, value in [('confidence', .9), ('critical', False), ('box', [20,20,56,55]),
                           ('visible_holes', [[[35,35],[55,35],[55,55],[35,55]]])]:
            test = copy.deepcopy(self.analysis)
            if key == 'critical':
                # Keep one critical source region so schema validation remains legal.
                extra = copy.deepcopy(test['regions'][0]);extra['id'] = 'another';test['regions'].append(extra)
                baseline = reference_identity(test, source)
            else:
                baseline = original
            test['regions'][0][key] = value
            self.assertNotEqual(baseline, reference_identity(test, source))

    def test_local_regression_cannot_hide_in_average(self):
        a = self.measure()
        b = copy.deepcopy(a)
        b['regions'][0]['detail_error'] += .02
        b['regions'][0]['loss'] -= .001
        result = compare_appearance(a,b)
        self.assertEqual(result['recommendation'], 'rollback_recommended')


if __name__ == '__main__':
    unittest.main()
