"""Malformed files are rejected before any browser or Blender consumes them."""
import copy
import json
import struct
import unittest

from server3d.glb_validation import MAX_GLB_BYTES, glb_limits, validate_glb


CUBE = [(-.5, 0., -.5), (.5, 0., -.5), (.5, 1., -.5), (-.5, 1., -.5),
        (-.5, 0., .5), (.5, 0., .5), (.5, 1., .5), (-.5, 1., .5)]
INDICES = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
           3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]


def fixture(*, indexed=True):
    points = CUBE if indexed else [CUBE[i] for i in INDICES]
    binary = b"".join(struct.pack("<3f", *p) for p in points)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary), "target": 34962}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": len(points), "type": "VEC3",
                  "min": [-.5, 0., -.5], "max": [.5, 1., .5]}]
    primitive = {"attributes": {"POSITION": 0}, "material": 0, "mode": 4}
    if indexed:
        index_bytes = struct.pack("<36H", *INDICES)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(index_bytes), "target": 34963})
        binary += index_bytes
        accessors.append({"bufferView": 1, "componentType": 5123, "count": len(INDICES), "type": "SCALAR"})
        primitive["indices"] = 1
    document = {"asset": {"version": "2.0", "generator": "unit fixture"}, "scene": 0,
                "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
                "meshes": [{"primitives": [primitive]}],
                "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [.5, .3, .1, 1.], "roughnessFactor": .8}}],
                "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors}
    return document, binary


def pack(document, binary, *, raw_json=None):
    js = json.dumps(document, separators=(",", ":")).encode() if raw_json is None else raw_json
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    content = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12+len(content)) + content


class GLBValidationTests(unittest.TestCase):
    def reject(self, document, binary, message=None):
        with self.assertRaisesRegex(ValueError, message or "Invalid managed GLB"):
            validate_glb(pack(document, binary))

    def test_measures_binary_geometry(self):
        d, b = fixture()
        result = validate_glb(pack(d, b))
        self.assertEqual(result["triangles"], 12)
        self.assertEqual(result["vertices"], 8)
        self.assertEqual(result["mesh_count"], 1)
        self.assertEqual(result["material_count"], 1)
        self.assertEqual(result["bounds"], {"min": [-.5, 0., -.5], "max": [.5, 1., .5]})
        self.assertEqual(len(result["sha256"]), 64)

    def test_nonindexed_triangle_mesh(self):
        self.assertEqual(validate_glb(pack(*fixture(indexed=False)))["vertices"], 36)

    def test_header_truncation_extra_chunk_and_size_budget(self):
        payload = pack(*fixture())
        variants = [b"bad", payload[:-1], b"BAD!" + payload[4:], payload + b"\0"*4,
                    payload[:4] + struct.pack("<I", 1) + payload[8:], b"\0" * (MAX_GLB_BYTES+1)]
        extra = payload + struct.pack("<I4s", 4, b"BIN\0") + b"\0"*4
        variants.append(extra[:8] + struct.pack("<I", len(extra)) + extra[12:])
        for value in variants:
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError):
                    validate_glb(value)

    def test_json_duplicates_nesting_and_nan(self):
        d, b = fixture()
        for raw in [b'{"asset":{},"asset":{}}', b"["*1000 + b"0" + b"]"*1000]:
            with self.assertRaises(ValueError):
                validate_glb(pack(d, b, raw_json=raw))
        d["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = float("nan")
        self.reject(d, b, "non-finite")

    def test_uri_extensions_and_nonstatic_features_rejected(self):
        for key, value in [("animations", []), ("skins", []), ("cameras", []), ("images", []),
                           ("textures", []), ("extensionsUsed", []), ("extensions", {}),
                           ("extras", {"nested": {"uri": "https://attacker.invalid/model.bin"}})]:
            with self.subTest(key=key):
                d, b = fixture()
                d[key] = value
                self.reject(d, b)
        d, b = fixture()
        d["buffers"][0]["uri"] = "data:application/octet-stream;base64,AAAA"
        self.reject(d, b, "URIs")

    def test_buffer_chunk_padding_and_ranges(self):
        for mutate in [lambda d: d["buffers"][0].update(byteLength=99999),
                       lambda d: d["bufferViews"][0].update(byteLength=99999),
                       lambda d: d["bufferViews"][0].update(buffer=1),
                       lambda d: d["bufferViews"][0].update(byteOffset=-1)]:
            d, b = fixture()
            mutate(d)
            self.reject(d, b)
        d, b = fixture()
        d["buffers"][0]["byteLength"] -= 1
        self.reject(d, b[:-1] + b"\x01", "zero padding")

    def test_accessor_ranges_count_alignment_stride_and_sparse(self):
        changes = [{"byteOffset": 2}, {"count": 0}, {"count": 300001}, {"count": True},
                   {"count": 100}, {"componentType": 5123}, {"normalized": True},
                   {"sparse": {}}, {"bufferView": 100}]
        for change in changes:
            with self.subTest(change=change):
                d, b = fixture()
                d["accessors"][0].update(change)
                self.reject(d, b)
        for stride in [3, 8, 256]:
            d, b = fixture()
            d["bufferViews"][0]["byteStride"] = stride
            self.reject(d, b)

    def test_actual_positions_not_declared_bounds_are_inspected(self):
        for bad in [float("nan"), float("inf"), 20001.]:
            d, b = fixture()
            b = struct.pack("<f", bad) + b[4:]
            self.reject(d, b, "POSITION")
        d, b = fixture()
        b = struct.pack("<f", -1.) + b[4:]
        self.reject(d, b, "bounds")

    def test_indices_checked_against_positions_and_cannot_forge_bounds(self):
        d, b = fixture()
        b = b[:96] + struct.pack("<H", 8) + b[98:]
        self.reject(d, b, "index exceeds")
        d, b = fixture()
        b = b[:96] + struct.pack("<36H", *[0 if i == 7 else i for i in INDICES])
        self.reject(d, b)
        d, b = fixture()
        d["accessors"][1]["count"] = 35
        self.reject(d, b, "form triangles")
        d, b = fixture()
        d["bufferViews"][1]["byteStride"] = 4
        self.reject(d, b)

    def test_material_factor_ranges_and_no_texture_or_alpha(self):
        for key, value in [("roughnessFactor", -1), ("roughnessFactor", 10**400), ("metallicFactor", float("inf")),
                           ("baseColorFactor", [1, 1, 1, .5]), ("baseColorTexture", {"index": 0})]:
            d, b = fixture()
            d["materials"][0]["pbrMetallicRoughness"][key] = value
            self.reject(d, b)
        for alpha in ["BLEND", "MASK"]:
            d, b = fixture()
            d["materials"][0]["alphaMode"] = alpha
            self.reject(d, b, "opaque")

    def test_only_supported_primitive_attributes_and_triangle_mode(self):
        for mutate in [lambda p: p.update(mode=1), lambda p: p.update(material=2),
                       lambda p: p.update(targets=[]),
                       lambda p: p["attributes"].update(TEXCOORD_0=0),
                       lambda p: p["attributes"].update(JOINTS_0=0)]:
            d, b = fixture()
            mutate(d["meshes"][0]["primitives"][0])
            self.reject(d, b)

    def test_normal_accessor_finite_and_normalized(self):
        d, b = fixture()
        normal_bytes = b"".join(struct.pack("<3f", 0., 1., 0.) for _ in CUBE)
        d["bufferViews"].append({"buffer": 0, "byteOffset": len(b), "byteLength": len(normal_bytes), "target": 34962})
        b += normal_bytes
        d["buffers"][0]["byteLength"] = len(b)
        d["accessors"].append({"bufferView": 2, "componentType": 5126, "type": "VEC3", "count": 8})
        d["meshes"][0]["primitives"][0]["attributes"]["NORMAL"] = 2
        for profile in ("standard", "showcase"):
            validate_glb(pack(d, b), profile=profile)
            for value in (float("nan"), 2.):
                with self.subTest(profile=profile, value=value), self.assertRaisesRegex(ValueError, "NORMAL"):
                    validate_glb(pack(d, b[:-4] + struct.pack("<f", value)), profile=profile)

    def test_interleaved_float_attributes(self):
        d, old = fixture()
        b = b"".join(struct.pack("<6f", *p, 0., 1., 0.) for p in CUBE) + old[96:]
        d["buffers"][0]["byteLength"] = len(b)
        d["bufferViews"][0].update(byteLength=192, byteStride=24)
        d["bufferViews"][1]["byteOffset"] = 192
        d["accessors"].append({"bufferView": 0, "byteOffset": 12, "componentType": 5126, "type": "VEC3", "count": 8})
        d["meshes"][0]["primitives"][0]["attributes"]["NORMAL"] = 2
        self.assertEqual(validate_glb(pack(d, b))["triangles"], 12)

    def test_nested_transforms_use_world_coordinates(self):
        d, b = fixture()
        d["nodes"] = [{"children": [1], "translation": [1., 2., 3.]},
                      {"mesh": 0, "translation": [-1., -2., -3.], "scale": [-1., 1., 1.]}]
        self.assertEqual(validate_glb(pack(d, b))["bounds"]["max"], [.5, 1., .5])
        d["nodes"][1]["translation"][0] = 0
        self.reject(d, b, "world bounds")

    def test_column_major_affine_matrix(self):
        d, b = fixture()
        d["nodes"][0]["matrix"] = [-1., 0., 0., 0., 0., -1., 0., 0., 0., 0., 1., 0., 0., 1., 0., 1.]
        validate_glb(pack(d, b))
        d["nodes"][0]["matrix"][3] = 1
        self.reject(d, b, "affine")

    def test_invalid_transforms_and_shear(self):
        for change in [{"rotation": [0, 0, 0, 0]}, {"scale": [0, 1, 1]},
                       {"translation": [float("inf"), 0, 0]},
                       {"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "scale": [1, 1, 1]},
                       {"matrix": [1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}]:
            d, b = fixture()
            d["nodes"][0].update(change)
            self.reject(d, b)

    def test_scene_cycles_duplicate_parents_or_unreachable_mesh(self):
        variants = [([{ "mesh": 0, "children": [0]}], [0]),
                    ([{"children": [1, 1]}, {"mesh": 0}], [0]),
                    ([{"children": [2]}, {"children": [2]}, {"mesh": 0}], [0, 1]),
                    ([{"mesh": 0}, {"children": [2]}, {"children": [1]}], [0]),
                    ([{"mesh": 0}], [0, 0])]
        for nodes, roots in variants:
            d, b = fixture()
            d["nodes"], d["scenes"][0]["nodes"] = nodes, roots
            self.reject(d, b)
        d, b = fixture()
        d["meshes"].append(copy.deepcopy(d["meshes"][0]))
        self.reject(d, b, "all nodes and meshes")

    def test_instancing_cannot_evade_triangle_budget(self):
        d, b = fixture()
        primitive = d["meshes"][0]["primitives"][0]
        d["meshes"][0]["primitives"] = [copy.deepcopy(primitive) for _ in range(64)]
        d["nodes"] = [{"mesh": 0} for _ in range(256)]
        d["scenes"][0]["nodes"] = list(range(256))
        self.reject(d, b, "instantiated geometry budget")

    def test_showcase_requires_explicit_profile_and_keeps_instanced_cap(self):
        d, b = fixture()
        primitive = d["meshes"][0]["primitives"][0]
        d["meshes"][0]["primitives"] = [copy.deepcopy(primitive) for _ in range(64)]
        d["nodes"] = [{"mesh": 0} for _ in range(256)]
        d["scenes"][0]["nodes"] = list(range(256))
        payload = pack(d, b)
        with self.assertRaisesRegex(ValueError, "instantiated geometry budget"):
            validate_glb(payload)
        result = validate_glb(payload, profile="showcase")
        self.assertEqual(result["triangles"], 12 * 64 * 256)
        self.assertEqual(result["budget_profile"], "showcase")
        # Double binary indices per primitive: stored geometry remains small,
        # but all 256 instances together now exceed the showcase triangle cap.
        indices = b[96:] * 2
        b = b[:96] + indices
        d["buffers"][0]["byteLength"] = len(b)
        d["bufferViews"][1]["byteLength"] = len(indices)
        d["accessors"][1]["count"] = 72
        with self.assertRaisesRegex(ValueError, "instantiated geometry budget"):
            validate_glb(pack(d, b), profile="showcase")

    def test_showcase_does_not_relax_geometry_and_resource_checks(self):
        for mutation in (lambda d: d["buffers"][0].update(uri="https://invalid.example/model"),
                         lambda d: d["meshes"][0]["primitives"][0]["attributes"].update(TEXCOORD_0=0),
                         lambda d: d["materials"][0].update(alphaMode="BLEND"),
                         lambda d: d["accessors"][0].update(count=1_000_001)):
            d, b = fixture()
            mutation(d)
            with self.assertRaises(ValueError):
                validate_glb(pack(d, b), profile="showcase")
        d, b = fixture()
        b = b[:96] + struct.pack("<36H", *([0, 0, 0] + INDICES[3:]))
        with self.assertRaisesRegex(ValueError, "degenerate triangles"):
            validate_glb(pack(d, b), profile="showcase")

    def test_profiles_preserve_default_limits_and_reject_unknown_values(self):
        self.assertEqual((glb_limits().bytes, glb_limits().vertices, glb_limits().triangles),
                         (16 * 1024 * 1024, 200_000, 100_000))
        self.assertEqual(glb_limits("showcase").bytes, 64 * 1024 * 1024)
        for profile in (True, None, 3, "", "ultra", "SHOWCASE"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_glb(pack(*fixture()), profile=profile)

    def test_showcase_accepts_valid_buffer_offsets_above_standard_file_budget(self):
        d, b = fixture()
        offset = 17 * 1024 * 1024
        # A legal BIN gap exercises byte-offset validation separately from the
        # outer file-size gate; actual geometry and references are unchanged.
        binary = b[:96] + bytes(offset - 96) + b[96:]
        d["bufferViews"][1]["byteOffset"] = offset
        d["buffers"][0]["byteLength"] = len(binary)
        payload = pack(d, binary)
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            validate_glb(payload)
        self.assertEqual(validate_glb(payload, profile="showcase")["triangles"], 12)

    def test_vertex_and_scene_node_budgets(self):
        d, b = fixture()
        d["accessors"][0]["count"] = 10**30
        self.reject(d, b, "accessor count")
        d, b = fixture()
        d["nodes"] = [{"mesh": 0} for _ in range(257)]
        self.reject(d, b, "nodes must contain")

    def test_planar_card_cannot_claim_3d_via_tilted_bounds(self):
        d, b = fixture()
        # x=y-.5 forms a diagonal plane, although all three AABB extents are 1.
        points = [(p[1]-.5, p[1], p[2]) for p in CUBE]
        b = b"".join(struct.pack("<3f", *p) for p in points) + b[96:]
        self.reject(d, b, "non-coplanar|degenerate triangles")

    def test_unused_accessor_view_rejected(self):
        d, b = fixture()
        d["accessors"].append(copy.deepcopy(d["accessors"][0]))
        self.reject(d, b, "unused accessors")

    def test_declared_position_bounds_must_match_binary(self):
        d, b = fixture()
        d["accessors"][0]["max"][0] = .2
        self.reject(d, b, "declared POSITION bounds")
        d, b = fixture()
        del d["accessors"][0]["min"]
        self.reject(d, b, "requires declared")

    def test_degenerate_triangles_cannot_fake_volumetric_vertices(self):
        d, b = fixture()
        b = b[:96] + struct.pack("<36H", *([0, 0, 0] + INDICES[3:]))
        self.reject(d, b, "degenerate triangles")

    def test_supported_unsigned_index_types(self):
        for component, fmt in [(5121, "B"), (5125, "I")]:
            d, b = fixture()
            data = struct.pack("<36" + fmt, *INDICES)
            b = b[:96] + data
            d["accessors"][1]["componentType"] = component
            d["bufferViews"][1]["byteLength"] = len(data)
            d["buffers"][0]["byteLength"] = len(b)
            self.assertEqual(validate_glb(pack(d, b))["triangles"], 12)


if __name__ == "__main__":
    unittest.main()
