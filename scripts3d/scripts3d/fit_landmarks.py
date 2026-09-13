"""Request a source-bound, read-only camera proposal from the actual 3D MCP."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    measurements = json.loads(args.measurements.read_text(encoding="utf-8"))
    token = args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=120) as http:
        async with Client(streamable_http_client(args.url + "/mcp-3d", http_client=http), cache=None) as client:
            async def call(name, **params):
                result = await client.call_tool(name, params)
                if result.is_error:
                    raise RuntimeError(str(result.content))
                return result.structured_content
            status = await call("scene_runtime_status")
            if status["restart_required"]:
                raise RuntimeError("Restart the updated 3D MCP before source fitting")
            job = await call("get_scene_job", job_id=args.job_id)
            proposal = await call("fit_scene_landmarks", job_id=args.job_id,
                                  source_sha256=measurements["source_sha256"],
                                  landmarks=measurements["landmarks"], base_revision=job["plan_revision"])
            if args.plan_output:
                args.plan_output.parent.mkdir(parents=True, exist_ok=True)
                with args.plan_output.open("x", encoding="utf-8") as output:
                    json.dump(proposal["proposed_plan"], output, ensure_ascii=False, indent=2)
            print(json.dumps({k: v for k, v in proposal.items() if k != "proposed_plan"},
                             ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--plan-output", type=Path, help="New proposal file only; no saved plan is modified")
    parser.add_argument("--url", default="http://127.0.0.1:8031")
    parser.add_argument("--token-file", type=Path, default=Path(".canvaslab3d/access.token"))
    asyncio.run(run(parser.parse_args()))
