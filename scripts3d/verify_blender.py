"""Real HTTP MCP Blender -> immutable component -> scene -> browser integration."""
import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import uuid
import zipfile
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from benchmarks3d.scenes import water_town,analysis_for
from benchmarks3d.blender_scene import component_water_town


async def run(args):
    secret=args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+secret},timeout=230) as http:
        async with Client(streamable_http_client(args.url+"/mcp-3d",http_client=http),cache=None) as client:
            async def call(name,arguments):
                result=await client.call_tool(name,arguments)
                if result.is_error: raise RuntimeError(str(result.content))
                return result.structured_content
            status=await call("scene_runtime_status",{})
            if status["restart_required"] or not status["blender"]["available"]:
                raise RuntimeError("Restart/configure current Blender MCP before testing")
            components={}
            for template in ["open_gate","tiled_roof","railing","cargo_crate"]:
                recipe={"template":template,"detail":2 if template=="open_gate" else 1,
                        "primary_color":"#566452" if template=="tiled_roof" else "#89623e",
                        "secondary_color":"#475543" if template in {"open_gate","tiled_roof"} else "#b68a50"}
                key=uuid.uuid4().hex
                asset=await call("generate_scene_component",{"recipe":recipe,"idempotency_key":key})
                same=await call("generate_scene_component",{"recipe":recipe,"idempotency_key":key})
                assert same["asset_id"]==asset["asset_id"] and same["reused"]
                other_request=await call("generate_scene_component",{"recipe":recipe,"idempotency_key":uuid.uuid4().hex})
                assert other_request["asset_id"]==asset["asset_id"] and other_request["reused"]
                checked=await call("get_scene_component",{"asset_id":asset["asset_id"]})
                assert checked["files"]==asset["files"] and checked["recipe"]==asset["recipe"]
                for kind in ["glb","blend"]:
                    response=await http.get(args.url+asset["downloads"][kind]);response.raise_for_status()
                    assert hashlib.sha256(response.content).hexdigest()==asset["files"]["component."+kind]
                components[template]=asset
            library=await call("list_scene_components",{})
            assert {v["asset_id"] for v in components.values()} <= {v["asset_id"] for v in library["components"]}
            source_response=await http.post(args.url+"/assets",content=args.image.read_bytes());source_response.raise_for_status()
            source=source_response.json()
            job=await call("submit_scene_reference",{"asset_id":source["asset_id"],"requirements":"Blender component integration, original water-town reference; not certified fidelity","idempotency_key":uuid.uuid4().hex})
            jid=job["job_id"]
            await call("save_scene_analysis",{"job_id":jid,"analysis":analysis_for(source,water_town()),"base_revision":0})
            plan=component_water_town({k:v["asset_id"] for k,v in components.items()})
            revision=await call("validate_scene_plan",{"job_id":jid,"plan":plan,"analysis_revision":1,"base_revision":0})
            build=await call("build_threejs_scene",{"job_id":jid,"plan_id":revision["plan_id"],"idempotency_key":"first"})
            retry=await call("build_threejs_scene",{"job_id":jid,"plan_id":revision["plan_id"],"idempotency_key":"first"})
            assert retry["build_id"]==build["build_id"] and retry["zip_sha256"]==build["zip_sha256"]
            export=await call("export_scene_project",{"job_id":jid,"build_id":build["build_id"]})
            response=await http.get(args.url+export["download_path"]);response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                for asset in components.values():
                    assert hashlib.sha256(archive.read(f"component-{asset['asset_id']}.glb")).hexdigest()==asset["asset_id"]
            capture=await call("capture_scene_views",{"job_id":jid,"build_id":build["build_id"]}) if args.capture else None
            print(json.dumps({"runtime":status,"job_id":jid,"build_id":build["build_id"],"components":components,"capture":capture,"workflow_complete":False},ensure_ascii=True,indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("image",type=Path);p.add_argument("--capture",action="store_true")
    p.add_argument("--url",default="http://127.0.0.1:8031")
    p.add_argument("--token-file",type=Path,default=Path(".canvaslab3d/access.token"))
    asyncio.run(run(p.parse_args()))
