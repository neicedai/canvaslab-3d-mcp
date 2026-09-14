"""Real MCP SDK / authenticated output access. No GPU or model inference implied."""
import tempfile
import unittest
from pathlib import Path
from mcp import Client
from fastapi.testclient import TestClient
from server3d.jobs import Store
from server3d.mcp_server import create_mcp
from server3d.api import create_app


class GPUMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_tools_have_real_structured_output_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            async with Client(create_mcp(Store(Path(tmp)))) as client:
                tools=await client.list_tools()
                names={t.name:t for t in tools.tools}
                for name in ['scene_gpu_status','submit_scene_gpu_task','get_scene_gpu_task','cancel_scene_gpu_task']:
                    self.assertIn(name,names);self.assertIsNotNone(names[name].output_schema)
                status=await client.call_tool('scene_gpu_status',{})
                self.assertFalse(status.is_error)
                self.assertEqual(status.structured_content['workers'],[])
                contract=await client.call_tool('get_scene_contract',{})
                self.assertIn('gpu_request_schema',contract.structured_content)

    async def test_unknown_task_is_a_tool_error_not_fake_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            async with Client(create_mcp(Store(Path(tmp)))) as client:
                result=await client.call_tool('get_scene_gpu_task',{'task_id':'0'*32})
                self.assertTrue(result.is_error)

    async def test_gpu_artifacts_require_existing_api_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=TestClient(create_app(Store(Path(tmp)), 'test-secret-'*4))
            self.assertEqual(client.get('/gpu-tasks/'+'0'*32+'/depth.npy').status_code,401)
            reply=client.get('/gpu-tasks/'+'0'*32+'/depth.npy',headers={'Authorization':'Bearer '+'test-secret-'*4})
            self.assertEqual(reply.status_code,400)

if __name__ == '__main__':unittest.main()
