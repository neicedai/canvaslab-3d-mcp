"""Exercise actual authenticated HTTP + MCP calls, not direct Store methods."""
import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from benchmarks3d.scenes import analysis_for, water_town


async def run(args):
    secret = args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer "+secret}, timeout=150) as http:
        response = await http.post(args.url+"/assets", content=args.image.read_bytes())
        response.raise_for_status(); source = response.json()
        async with Client(streamable_http_client(args.url+"/mcp-3d", http_client=http), cache=None) as client:
            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                if result.is_error:
                    raise RuntimeError(f"{name}: {result.content}")
                return result.structured_content
            tools = await client.list_tools()
            runtime = await call("scene_runtime_status", {})
            if runtime.get("restart_required"):
                raise RuntimeError("Server process is stale: restart before this verification")
            plan = water_town()
            job = await call("submit_scene_reference", {"asset_id": source["asset_id"], "requirements": "HTTP integration smoke test; preview only", "idempotency_key": uuid.uuid4().hex})
            identity = job["job_id"]
            await call("save_scene_analysis", {"job_id": identity, "analysis": analysis_for(source, plan), "base_revision": 0})
            revision = await call("validate_scene_plan", {"job_id": identity, "plan": plan, "analysis_revision": 1, "base_revision": 0})
            build = await call("build_threejs_scene", {"job_id": identity, "plan_id": revision["plan_id"], "idempotency_key": "first-build"})
            export = await call("export_scene_project", {"job_id": identity, "build_id": build["build_id"]})
            download = await http.get(args.url+export["download_path"]); download.raise_for_status()
            capture = await call("capture_scene_views", {"job_id": identity, "build_id": build["build_id"]}) if args.capture else None
            complete = await call("complete_scene_review", {"job_id": identity})
            assert complete["workflow_complete"] is False and complete["completion_certificate"] is None
            print(json.dumps({"protocol": client.protocol_version, "tools": [t.name for t in tools.tools],
                              "runtime": runtime, "job_id": identity, "build_id": build["build_id"],
                              "download_bytes": len(download.content), "capture": capture,
                              "completion_correctly_blocked": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8031")
    parser.add_argument("--token-file", type=Path, default=Path(".canvaslab3d/access.token"))
    parser.add_argument("--capture", action="store_true")
    asyncio.run(run(parser.parse_args()))
