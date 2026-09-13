"""Use the actual MCP for advisory camera fitting and optional versioned rebuild."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    secret = args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+secret}, timeout=150) as http:
        async with Client(streamable_http_client(args.url+"/mcp-3d", http_client=http), cache=None) as client:
            async def call(name, arguments):
                result = await client.call_tool(name,arguments)
                if result.is_error: raise RuntimeError(str(result.content))
                return result.structured_content
            runtime = await call("scene_runtime_status", {})
            if runtime["restart_required"]: raise RuntimeError("Restart the stale MCP process before fitting")
            proposal = await call("suggest_scene_camera", {"job_id":args.job_id,"capture_id":args.capture_id})
            report = {k:v for k,v in proposal.items() if k != "proposed_plan"}
            report["camera"] = proposal["proposed_plan"]["camera"]
            if args.apply and proposal["improved"]:
                revision = await call("validate_scene_plan", {"job_id":args.job_id,"plan":proposal["proposed_plan"],
                    "analysis_revision":proposal["analysis_revision"],"base_revision":proposal["base_revision"]})
                build = await call("build_threejs_scene", {"job_id":args.job_id,"plan_id":revision["plan_id"],
                    "idempotency_key":"camera-"+revision["plan_id"]})
                capture = await call("capture_scene_views", {"job_id":args.job_id,"build_id":build["build_id"]})
                measured = await call("suggest_scene_camera", {"job_id":args.job_id,"capture_id":capture["capture_id"]})
                report.update(applied=True,build_id=build["build_id"],capture_id=capture["capture_id"],
                              measured_after_loss=measured["before_loss"], tests=capture["tests"],
                              workflow_complete=False)
            print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("job_id"); parser.add_argument("capture_id")
    parser.add_argument("--apply",action="store_true",help="Persist a new plan, build and capture; never edit old generated files")
    parser.add_argument("--url",default="http://127.0.0.1:8031")
    parser.add_argument("--token-file",type=Path,default=Path(".canvaslab3d/access.token"))
    asyncio.run(run(parser.parse_args()))
