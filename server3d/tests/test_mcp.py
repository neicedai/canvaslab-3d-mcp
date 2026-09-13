import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from mcp import Client
from server3d.jobs import Store
from server3d.mcp_server import create_mcp


class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_tools_and_fail_closed_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            image = io.BytesIO(); Image.new("RGB", (64, 64)).save(image, "PNG")
            asset = store.upload(image.getvalue())
            async with Client(create_mcp(store)) as client:
                tools = await client.list_tools()
                names = [tool.name for tool in tools.tools]
                self.assertEqual(len(names), len(set(names)))
                self.assertTrue({"measure_scene_fidelity", "refine_scene_component",
                                 "suggest_component_deformation", "compare_scene_candidate"}.issubset(names))
                self.assertIn("fit_scene_landmarks", [tool.name for tool in tools.tools])
                self.assertTrue(all(t.output_schema is not None for t in tools.tools))
                state = await client.call_tool("scene_runtime_status", {})
                self.assertFalse(state.is_error)
                self.assertFalse(state.structured_content["workflow_complete"])
                result = await client.call_tool("submit_scene_reference", {"asset_id": asset["asset_id"], "requirements": "test", "idempotency_key": "first"})
                identity = result.structured_content["job_id"]
                finish = await client.call_tool("complete_scene_review", {"job_id": identity})
                self.assertFalse(finish.structured_content["workflow_complete"])
                self.assertIsNone(finish.structured_content["completion_certificate"])
                invalid = await client.call_tool("validate_scene_plan", {"job_id": identity, "plan": {"js": "evil"}, "analysis_revision": 0, "base_revision": 0})
                self.assertTrue(invalid.is_error)
                self.assertIn("Save source analysis", str(invalid.content))
