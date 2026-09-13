"""Managed Blender component library. No caller-provided scripts or .blend files."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from .builder import REPO, canonical, sha
from .component_schema import ComponentRecipe, COMPONENT_TEMPLATES
from .glb_validation import glb_limits, validate_glb
from .scene_schema import primitive_triangle_budget, scene_budgets

WORKER = REPO / "scripts3d" / "blender_components.py"


def configured_blender():
    raw = os.getenv("CANVASLAB3D_BLENDER") or shutil.which("blender")
    if not raw or not Path(raw).is_file():
        raise ValueError("Blender unavailable. Install Blender and set CANVASLAB3D_BLENDER to the executable, then restart 3D MCP")
    return Path(raw).resolve()


class ComponentLibrary:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "components"
        self._blender_identity = None

    def engine_identity(self):
        executable = configured_blender()
        stat = executable.stat()
        key = (str(executable),stat.st_size,stat.st_mtime_ns)
        if self._blender_identity and self._blender_identity[0] == key:
            return self._blender_identity[1]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run([str(executable),"--version"],capture_output=True,text=True,
                                    encoding="utf-8",errors="replace",timeout=15,shell=False,creationflags=flags)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError("Unable to query Blender executable") from exc
        if result.returncode or not result.stdout.startswith("Blender "):
            raise ValueError("Configured executable did not identify as Blender")
        identity = {"version":result.stdout.splitlines()[0],"executable_sha256":sha(executable.read_bytes())}
        self._blender_identity = (key,identity)
        return identity

    def status(self):
        try:
            identity = self.engine_identity()
            return {"available":True,**identity,"recipe_worker_sha256":sha(WORKER.read_bytes()),
                    "templates":list(COMPONENT_TEMPLATES),
                    "arbitrary_script_input":False,"untrusted_model_import":False,"gpu_inference_required":False}
        except (ValueError,FileNotFoundError) as exc:
            return {"available":False,"reason":str(exc)}

    def get(self, asset_id, db=None):
        if not isinstance(asset_id,str) or not re.fullmatch(r"[a-f0-9]{64}",asset_id):
            raise ValueError("Invalid component asset ID")
        if db is None:
            with self.store.transaction() as connection:
                return self.get(asset_id,connection)
        record = self.store.get(db,"component",asset_id)
        directory = self.root / asset_id
        for name,digest in record["files"].items():
            if sha((directory/name).read_bytes()) != digest:
                raise ValueError("Registered component changed; restore immutable files from backup or use a new recipe/version after administrator recovery")
        return record

    def list(self):
        with self.store.transaction() as db:
            rows = db.execute("SELECT id FROM records WHERE kind='component' ORDER BY id").fetchall()
            return [self.get(row[0],db) for row in rows]

    def generate(self, recipe, idempotency_key):
        data = ComponentRecipe.model_validate(recipe).model_dump()
        budget_profile = "showcase" if data["detail"] == 3 else "standard"
        limits = glb_limits(budget_profile)
        blend_limit = (128 if data["detail"] == 3 else 64) * 1024 * 1024
        identity = self.engine_identity()
        worker_bytes = WORKER.read_bytes()
        fingerprint = sha(canonical([data,identity,sha(worker_bytes)]))
        lease = uuid.uuid4().hex
        with self.store.transaction() as db:
            prior = self.store.retry(db,"component",idempotency_key,fingerprint)
            if prior:
                return self.get(prior["asset_id"],db) | {"reused":True}
            cached = db.execute("SELECT payload FROM records WHERE kind='component_recipe' AND id=?",(fingerprint,)).fetchone()
            if cached:
                record = self.get(json.loads(cached[0])["asset_id"],db)
                self.store.retry(db,"component",idempotency_key,fingerprint,{"asset_id":record["asset_id"]})
                return record | {"reused":True}
            if db.execute("SELECT COUNT(*) FROM records WHERE kind='component'").fetchone()[0] >= 128:
                raise ValueError("Component library quota reached")
            work_root = self.store.root / "component-work"
            if work_root.exists() and sum(1 for p in work_root.iterdir() if p.is_dir()) >= 200:
                raise ValueError("Component work quota reached; archive managed diagnostics before retrying")
            active = db.execute("SELECT payload FROM records WHERE kind='lease' AND id='blender'").fetchone()
            if active and json.loads(active[0])["expires_at"] > time.time():
                raise ValueError("Blender component worker busy; retry later")
            self.store.put(db,"lease","blender",{"owner":lease,"expires_at":time.time()+240})
        # Unique managed workspace: failed work is retained for diagnosis, never
        # recursively deletes a user directory or executes a recipe as code.
        work = self.store.root / "component-work" / lease
        try:
            work.mkdir(parents=True,exist_ok=False)
            recipe_path = work / "recipe.json"
            recipe_path.write_bytes(canonical(data))
            fixed_script = work / "worker.py"
            fixed_script.write_bytes(worker_bytes)
            output = work / "output"
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            args = [str(configured_blender()),"--background","--factory-startup","--disable-autoexec",
                    "--threads","1","--python-exit-code","1","--python",str(fixed_script),"--",str(recipe_path),str(output)]
            env = os.environ.copy()
            for name in list(env):
                if name.startswith("PYTHON") or name.startswith("BLENDER_USER_") or name.startswith("BLENDER_SYSTEM_") or name in {"CANVASLAB3D_TOKEN"}:
                    env.pop(name,None)
            env["BLENDER_USER_CONFIG"] = str(work/"config")
            try:
                result = subprocess.run(args,capture_output=True,text=True,encoding="utf-8",errors="replace",
                                        timeout=180,shell=False,creationflags=flags,env=env)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Blender component generation timed out; failed job retained, no asset published") from exc
            (work/"worker.log").write_text(result.stdout+"\n"+result.stderr,encoding="utf-8")
            if result.returncode:
                raise ValueError("Blender generation failed; see managed worker.log. No component was published")
            required = ["component.glb","component.blend","metadata.json"]
            if any(not (output/n).is_file() for n in required):
                raise ValueError("Blender output is incomplete")
            if (output/"component.glb").stat().st_size > limits.bytes or (output/"component.blend").stat().st_size > blend_limit:
                raise ValueError("Blender output exceeds component size budget")
            payload = (output/"component.glb").read_bytes()
            measured = validate_glb(payload, profile=budget_profile)
            metadata = json.loads((output/"metadata.json").read_text(encoding="utf-8"))
            natural = metadata.get("natural_dimensions")
            if not isinstance(natural,list) or len(natural)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in natural):
                raise ValueError("Blender metadata is missing valid natural proportions")
            asset_id = sha(payload)
            files = {n:sha((output/n).read_bytes()) for n in required}
            record = {"asset_id":asset_id,"template":data["template"],"recipe":data,"recipe_fingerprint":fingerprint,
                      "blender":identity,"worker_sha256":sha(worker_bytes),"geometry":measured,"files":files,
                      "bytes":len(payload),"natural_dimensions":natural,"origin":"managed_blender_recipe","material_policy":"authored_opaque_pbr",
                      "coordinate_system":"y-up-front-positive-z-bottom-center-unit-bounds","preview_only":True,
                      "downloads":{"glb":f"/components/{asset_id}/glb","blend":f"/components/{asset_id}/blend"}}
            (output/"registry.json").write_bytes(canonical(record))
            with self.store.transaction() as db:
                existing = db.execute("SELECT 1 FROM records WHERE kind='component' AND id=?",(asset_id,)).fetchone()
                if existing:
                    record = self.get(asset_id,db)
                else:
                    self.root.mkdir(parents=True,exist_ok=True)
                    destination = self.root/asset_id
                    if destination.exists():
                        # Recover a previous rename whose SQLite commit failed.
                        # Never overwrite that directory or trust different recipes.
                        orphan = json.loads((destination/"registry.json").read_text(encoding="utf-8"))
                        if orphan.get("asset_id")!=asset_id or orphan.get("recipe_fingerprint")!=fingerprint or set(orphan.get("files",{}))!=set(required):
                            raise ValueError("Uncommitted component identity conflict; administrator recovery required")
                        for name,digest in orphan["files"].items():
                            if sha((destination/name).read_bytes())!=digest:
                                raise ValueError("Uncommitted component file changed; administrator recovery required")
                        validate_glb((destination/"component.glb").read_bytes(), profile=budget_profile)
                        record = orphan
                    else:
                        output.rename(destination)
                    self.store.put(db,"component",asset_id,record)
                self.store.put(db,"component_recipe",fingerprint,{"asset_id":asset_id})
                self.store.retry(db,"component",idempotency_key,fingerprint,{"asset_id":asset_id})
            return record | {"reused":False}
        finally:
            with self.store.transaction() as db:
                row = db.execute("SELECT payload FROM records WHERE kind='lease' AND id='blender'").fetchone()
                if row and json.loads(row[0])["owner"] == lease:
                    db.execute("DELETE FROM records WHERE kind='lease' AND id='blender'")

    def build_assets(self, plan, db):
        triangle_limit, download_limit = scene_budgets(plan.get("render_quality", "standard"))
        records = {}
        triangles = 0
        for node in plan["objects"]:
            if node["kind"] != "asset":
                triangles += primitive_triangle_budget(node["kind"],plan.get("render_quality","standard"))
                continue
            identity = node["asset_id"]
            record = records.get(identity) or self.get(identity,db)
            if node.get("movement") and record["template"] not in {"cargo_crate", "woven_boat", "rowing_boat"}:
                raise ValueError("Only registered cargo_crate and boat assets support movement")
            records[identity] = record
            triangles += record["geometry"]["triangles"]
        if triangles > triangle_limit:
            raise ValueError("Scene instantiated triangle budget exceeded")
        if sum(r["bytes"] for r in records.values()) > download_limit:
            raise ValueError("Scene component download budget exceeded")
        return {identity:{"record":record,"bytes":(self.root/identity/"component.glb").read_bytes()} for identity,record in records.items()}
