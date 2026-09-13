"""Real GLB binary/PNG fixtures; no Blender, image decoder, GPU or network needed."""
import copy
import hashlib
import json
import struct
import unittest
import zlib
from unittest.mock import patch

from server3d.glb_validation import validate_glb
from server3d.texture_validation import inspect_png

CUBE = [(-.5, 0., -.5), (.5, 0., -.5), (.5, 1., -.5), (-.5, 1., -.5),
        (-.5, 0., .5), (.5, 0., .5), (.5, 1., .5), (-.5, 1., .5)]
INDICES = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
           3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png(*, width=2, height=2, color=2, depth=8, interlace=0, raw=None, extra=b""):
    channels = 4 if color == 6 else 3
    raw = (b"\0" + bytes([190, 70, 30, 255][:channels]) * width) * height if raw is None else raw
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
            + extra + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def fixture(*, textured=True, image=None):
    binary = b"".join(struct.pack("<3f", *p) for p in CUBE) + struct.pack("<36H", *INDICES)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": 96, "target": 34962},
             {"buffer": 0, "byteOffset": 96, "byteLength": 72, "target": 34963}]
    accessors = [{"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 8,
                  "min": [-.5, 0., -.5], "max": [.5, 1., .5]},
                 {"bufferView": 1, "componentType": 5123, "type": "SCALAR", "count": 36}]
    primitive = {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0}], "meshes": [{"primitives": [primitive]}],
                "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1., 1., 1., 1.], "roughnessFactor": .8}}],
                "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors}
    if textured:
        uv = b"".join(struct.pack("<2f", i % 2, (i // 2) % 2) for i in range(8))
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(uv), "target": 34962})
        binary += uv
        accessors.append({"bufferView": 2, "componentType": 5126, "type": "VEC2", "count": 8})
        primitive["attributes"]["TEXCOORD_0"] = 2
        image = png() if image is None else image
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(image)})
        binary += image
        document["images"] = [{"mimeType": "image/png", "bufferView": 3}]
        document["samplers"] = [{"wrapS": 33071, "wrapT": 33071}]
        document["textures"] = [{"source": 0, "sampler": 0}]
        document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0, "texCoord": 0}
    document["buffers"][0]["byteLength"] = len(binary)
    return document, binary


def pack(document, binary):
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def validate(document, binary):
    return validate_glb(pack(document, binary), texture_profile="embedded-png-v1")


class EmbeddedTextureTests(unittest.TestCase):
    def test_legacy_gate_still_requires_explicit_opt_in(self):
        d, b = fixture()
        with self.assertRaises(ValueError):
            validate_glb(pack(d, b))
        for quality in ("standard", "showcase"):
            result = validate_glb(pack(d, b), profile=quality, texture_profile="embedded-png-v1")
            self.assertEqual(result["validation_profile"], "canvaslab-static-component-v2")
            self.assertEqual(result["texture_pixels"], 4)
            self.assertEqual(result["texture_count"], 1)
            self.assertEqual(result["triangles"], 12)
            self.assertEqual(result["sha256"], hashlib.sha256(pack(d, b)).hexdigest())

    def test_legacy_metadata_is_unchanged_in_opt_in_path(self):
        d, b = fixture(textured=False)
        self.assertEqual(validate_glb(pack(d, b)), validate(d, b))

    def test_external_urls_and_extensions_are_rejected(self):
        for change in [lambda d: d["images"][0].update(uri="https://example.invalid/p.png"),
                       lambda d: d["images"][0].update(uri="data:image/png;base64,AAAA"),
                       lambda d: d["images"][0].update(uri="blob:untrusted"),
                       lambda d: d.update(extensionsUsed=["KHR_texture_basisu"]),
                       lambda d: d["textures"][0].update(extensions={})]:
            d, b = fixture();change(d)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(d, b)

    def test_wrong_mime_range_target_and_duplicate_image_view(self):
        for change in [lambda d: d["images"][0].update(mimeType="image/jpeg"),
                       lambda d: d["images"][0].update(bufferView=100),
                       lambda d: d["bufferViews"][3].update(byteLength=9999),
                       lambda d: d["bufferViews"][3].update(byteStride=8),
                       lambda d: d["bufferViews"][3].update(target=34962),
                       lambda d: d["images"].append(copy.deepcopy(d["images"][0]))]:
            d, b = fixture();change(d)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(d, b)

    def test_missing_wrong_or_unbounded_uvs(self):
        for change in [lambda d: d["meshes"][0]["primitives"][0]["attributes"].pop("TEXCOORD_0"),
                       lambda d: d["accessors"][2].update(count=7),
                       lambda d: d["accessors"][2].update(componentType=5123),
                       lambda d: d["accessors"][2].update(normalized=True),
                       lambda d: d["accessors"][2].update(byteOffset=2),
                       lambda d: d["accessors"][2].update(sparse={})]:
            d, b = fixture();change(d)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(d, b)
        for value in [float("nan"), float("inf"), 10001.]:
            d, b = fixture();b = b[:168] + struct.pack("<f", value) + b[172:]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "TEXCOORD_0"):
                validate(d, b)

    def test_only_uv_zero_base_color_and_opaque_materials(self):
        for change in [lambda d: d["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"].update(texCoord=1),
                       lambda d: d["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"].update(index=True),
                       lambda d: d["materials"][0].update(normalTexture={"index": 0}),
                       lambda d: d["materials"][0].update(alphaMode="BLEND"),
                       lambda d: d["samplers"][0].update(wrapS=123)]:
            d, b = fixture();change(d)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(d, b)

    def test_unused_texture_and_sampler_rejected(self):
        for field, value in [("textures", {"source": 0}), ("samplers", {})]:
            d, b = fixture();d[field].append(value)
            with self.assertRaisesRegex(ValueError, "unused"):
                validate(d, b)

    def test_png_crc_truncation_and_trailing_data(self):
        good = png()
        for image in [good[:-1], good + b"x", good[:30] + b"X" + good[31:], b"<svg/>"]:
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                validate(*fixture(image=image))

    def test_png_dimensions_metadata_format_and_scanline_limits(self):
        for image in [png(width=4097, height=1, raw=b""), png(width=0, raw=b""),
                      png(depth=16), png(interlace=1), png(color=3),
                      png(extra=chunk(b"acTL", b"\0"*8)), png(extra=chunk(b"tEXt", b"data")),
                      png(raw=b"\0" * 50000), png(raw=b"\0"), png(raw=(b"\x05" + b"\0"*6)*2)]:
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                inspect_png(image)

    def test_rgb_rgba_srgb_and_png_filters(self):
        for image in [png(), png(color=6), png(extra=chunk(b"sRGB", b"\0")),
                      png(raw=(b"\x04" + b"\0"*6)*2)]:
            self.assertEqual(validate(*fixture(image=image))["images"][0]["width"], 2)

    def test_texture_budget_is_charged_per_texture(self):
        d, b = fixture()
        d["textures"].append({"source": 0})
        d["materials"].append(copy.deepcopy(d["materials"][0]))
        d["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 1
        d["meshes"][0]["primitives"].append(copy.deepcopy(d["meshes"][0]["primitives"][0]))
        d["meshes"][0]["primitives"][1]["material"] = 1
        self.assertEqual(validate(d, b)["texture_pixels"], 8)
        with patch("server3d.texture_validation.MAX_TEXTURE_PIXELS", 7), self.assertRaisesRegex(ValueError, "pixel budget"):
            validate(d, b)

    def test_textures_cannot_bypass_geometry_checks(self):
        d, b = fixture();d["nodes"][0]["scale"] = [2., 1., 1.]
        with self.assertRaisesRegex(ValueError, "world bounds"):
            validate(d, b)
        d, b = fixture();b = b[:96] + struct.pack("<H", 900) + b[98:]
        with self.assertRaisesRegex(ValueError, "index exceeds"):
            validate(d, b)

    def test_image_views_cannot_overlap_geometry(self):
        d, b = fixture();d["bufferViews"][2]["byteLength"] += 4
        with self.assertRaisesRegex(ValueError, "overlaps"):
            validate(d, b)

    def test_unknown_profile_fails_closed(self):
        for profile in ["jpeg", "", None, True, {}, []]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_glb(pack(*fixture()), texture_profile=profile)


if __name__ == "__main__":
    unittest.main()
