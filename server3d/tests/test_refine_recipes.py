import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from scripts3d.refine_build import resolved_recipes, run


class RefineRecipeTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"objects": [{"id": "cargo", "kind": "asset", "asset_id": "old"},
                                 {"id": "title", "kind": "sign"}]}
        self.components = {"old": {"recipe": {"template": "cargo_crate", "detail": 2}}}

    def test_default_keeps_template_and_sets_requested_detail(self):
        recipe = resolved_recipes(self.plan, self.components, 3)["cargo"]
        self.assertEqual(recipe["template"], "cargo_crate")
        self.assertEqual(recipe["detail"], 3)

    def test_explicit_source_component_override_does_not_mutate_previous_recipe(self):
        before = copy.deepcopy(self.components)
        recipe = resolved_recipes(self.plan, self.components, 3,
                                  {"cargo": {"template": "woven_basket", "detail": 3}})
        self.assertEqual(recipe["cargo"]["template"], "woven_basket")
        self.assertEqual(self.components, before)

    def test_unknown_node_nonasset_node_or_arbitrary_script_rejected(self):
        for value in [{"missing": {"template": "woven_basket"}},
                      {"title": {"template": "woven_basket"}},
                      {"cargo": {"template": "woven_basket", "python": "bad"}}]:
            with self.assertRaises(ValueError):
                resolved_recipes(self.plan, self.components, 3, value)


class RefineSourceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacing_both_image_and_analysis_cannot_change_verified_source(self):
        def png(color):
            output = io.BytesIO()
            Image.new("RGB", (16, 16), color).save(output, "PNG")
            return output.getvalue()

        original, replacement = png("blue"), png("red")
        original_sha = hashlib.sha256(original).hexdigest()
        replacement_sha = hashlib.sha256(replacement).hexdigest()
        self.assertNotEqual(original_sha, replacement_sha)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "verified-base"
            base.mkdir()
            artifacts = {
                base / "reference-annotations.json": json.dumps({"source_sha256": original_sha}).encode(),
                base / "scene.json": json.dumps({"objects": [{"id": "cargo", "kind": "asset", "asset_id": "old"}]}).encode(),
                base / "components.json": json.dumps({"old": {"recipe": {"template": "cargo_crate", "detail": 2}}}).encode(),
                root / "original.png": original,
                root / "replacement.png": replacement,
                root / "replacement-analysis.json": json.dumps({"source_sha256": replacement_sha}).encode(),
                root / "test-token": b"test-only-never-sent",
            }
            for path, payload in artifacts.items():
                with path.open("xb") as output:
                    output.write(payload)
            args = SimpleNamespace(
                base_build=base, image=root / "replacement.png",
                source_analysis=root / "replacement-analysis.json",
                scene_plan=None, component_recipes=None, detail=3, render_quality=None,
                token_file=root / "test-token", url="http://127.0.0.1:1", capture=False,
            )
            with patch("scripts3d.refine_build.verify_build", return_value={"source": {"sha256": original_sha}}) as verified:
                with patch("scripts3d.refine_build.httpx2.AsyncClient", side_effect=AssertionError("Network must not be reached")) as network:
                    with self.assertRaisesRegex(ValueError, "verified base build reference"):
                        await run(args)
                    network.assert_not_called()
                verified.assert_called_once_with(base)
            for path, payload in artifacts.items():
                self.assertEqual(path.read_bytes(), payload, f"Input artifact changed: {path.name}")


if __name__ == "__main__":
    unittest.main()
