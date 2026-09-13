"""Submit human/model-authored findings against a real, unconsumed capture."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    findings = json.loads(args.findings.read_text(encoding="utf-8"))
    secret = args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + secret}, timeout=60) as http:
        async with Client(streamable_http_client(args.url + "/mcp-3d", http_client=http), cache=None) as client:
            result = await client.call_tool("audit_scene_views", {
                "job_id": args.job_id, "capture_id": args.capture_id, "model_findings": findings,
            })
            if result.is_error:
                raise RuntimeError(str(result.content))
            print(json.dumps(result.structured_content, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("capture_id")
    parser.add_argument("findings", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8031")
    parser.add_argument("--token-file", type=Path, default=Path(".canvaslab3d/access.token"))
    asyncio.run(run(parser.parse_args()))
