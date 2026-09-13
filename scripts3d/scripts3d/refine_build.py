"""Refine managed components through MCP while preserving a verified composition."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from server3d.builder import verify_build
from server3d.component_schema import ComponentRecipe


def resolved_recipes(plan, components, detail, overrides=None):
    """Explicit per-node recipe replacement; never reinterpret another template."""
    overrides = {} if overrides is None else overrides
    nodes = {n["id"] for n in plan["objects"] if n["kind"] == "asset"}
    if not isinstance(overrides, dict) or set(overrides) - nodes:
        raise ValueError("Component recipe overrides must name existing asset nodes in the supplied plan")
    recipes = {}
    for node in plan["objects"]:
        if node["kind"] != "asset":
            continue
        if node["id"] in overrides:
            recipe = overrides[node["id"]]
        else:
            recipe = {**components[node["asset_id"]]["recipe"], "detail": detail}
        recipes[node["id"]] = ComponentRecipe.model_validate(recipe).model_dump()
    return recipes


async def run(args):
    verify_build(args.base_build)
    base_analysis = json.loads((args.base_build / "reference-annotations.json").read_text(encoding="utf-8"))
    plan_path = args.scene_plan or args.base_build / "scene.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # Quality is an explicit, signed part of the scene, never a query-string
    # override or a mutation of the source build. Detail 3 opts into the bounded
    # showcase budget/rendering path; lower detail keeps its existing profile.
    if args.render_quality:
        plan["render_quality"] = args.render_quality
    elif args.detail == 3:
        plan["render_quality"] = "showcase"
    analysis_path = getattr(args, "source_analysis", None) or args.base_build / "reference-annotations.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    components = json.loads((args.base_build / "components.json").read_text(encoding="utf-8"))
    override_path = getattr(args, "component_recipes", None)
    overrides = json.loads(override_path.read_text(encoding="utf-8")) if override_path else None
    recipes = resolved_recipes(plan, components, args.detail, overrides)
    image = args.image.read_bytes()
    if (analysis["source_sha256"] != base_analysis["source_sha256"]
            or hashlib.sha256(image).hexdigest() != base_analysis["source_sha256"]):
        raise ValueError("Image does not match the verified base build reference")
    secret = args.token_file.read_text(encoding="utf-8").strip()
    async with httpx2.AsyncClient(headers={"Authorization":"Bearer " + secret},timeout=240) as http:
        async with Client(streamable_http_client(args.url + "/mcp-3d",http_client=http),cache=None) as client:
            async def call(name, **kwargs):
                result = await client.call_tool(name,kwargs)
                if result.is_error: raise RuntimeError(str(result.content))
                return result.structured_content
            status = await call("scene_runtime_status")
            if not status["runtime_ready"] or status["restart_required"]:
                raise RuntimeError("Restart/build the current 3D service before refinement")
            replacements = {}
            for node in plan["objects"]:
                if node["kind"] != "asset": continue
                recipe = recipes[node["id"]]
                recipe_key = json.dumps(recipe, sort_keys=True)
                if recipe_key not in replacements:
                    asset = await call("generate_scene_component",recipe=recipe,idempotency_key=uuid.uuid4().hex)
                    replacements[recipe_key] = asset["asset_id"]
                    print(json.dumps({"template":recipe["template"],"detail":recipe["detail"],"triangles":asset["geometry"]["triangles"]}),flush=True)
                node["asset_id"] = replacements[recipe_key]
            response = await http.post(args.url + "/assets",content=image)
            response.raise_for_status()
            source = response.json()
            job = await call("submit_scene_reference",asset_id=source["asset_id"],requirements="Refine component geometry and rendering against the same original; explicit supplied plan may adjust measured attachments",idempotency_key=uuid.uuid4().hex)
            jid = job["job_id"]
            analysis["change_reason"] = "Component refinement from verified build " + args.base_build.name
            await call("save_scene_analysis",job_id=jid,analysis=analysis,base_revision=0)
            validated = await call("validate_scene_plan",job_id=jid,plan=plan,analysis_revision=1,base_revision=0)
            build = await call("build_threejs_scene",job_id=jid,plan_id=validated["plan_id"],idempotency_key="refined")
            print(json.dumps({"job_id":jid,"build_id":build["build_id"]}),flush=True)
            if args.capture:
                captured = await call("capture_scene_views",job_id=jid,build_id=build["build_id"])
                print(json.dumps({"capture_id":captured["capture_id"],"passed":sum(t["passed"] for t in captured["tests"]),"total":len(captured["tests"])}),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base_build",type=Path)
    parser.add_argument("image",type=Path)
    parser.add_argument("--detail",type=int,choices=(1,2,3),default=2)
    parser.add_argument("--render-quality",choices=("standard","showcase"))
    parser.add_argument("--scene-plan",type=Path,help="Optional complete declarative plan using this base build's asset IDs; still validated by MCP against the original")
    parser.add_argument("--component-recipes",type=Path,help="Explicit node-ID to bounded recipe mapping, e.g. replace a mistaken crate with a woven_basket; does not alter old builds or templates")
    parser.add_argument("--source-analysis",type=Path,help="Optional revised native source annotations for the SAME exact original image, validated and saved to a new job")
    parser.add_argument("--url",default="http://127.0.0.1:8031")
    parser.add_argument("--token-file",type=Path,default=Path(".canvaslab3d/access.token"))
    parser.add_argument("--capture",action="store_true")
    asyncio.run(run(parser.parse_args()))
