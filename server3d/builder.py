"""Content-addressed builds from a pinned, precompiled runtime; no arbitrary code."""
from __future__ import annotations

import hashlib
import json
import zipfile
import uuid
from pathlib import Path

from . import CONTRACT, VERSION

REPO = Path(__file__).resolve().parents[1]
GENERATOR_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def runtime_files(runtime: Path | None = None) -> dict[str, bytes]:
    runtime = runtime or REPO / "runtime3d"
    stamp = json.loads((runtime / "dist/runtime-manifest.json").read_text(encoding="utf-8"))
    for name, digest in stamp["sources"].items():
        if sha((runtime / name).read_bytes()) != digest:
            raise ValueError("Runtime source changed: run npm run build inside runtime3d")
    files = {}
    for name, digest in stamp["files"].items():
        payload = (runtime / "dist" / name).read_bytes()
        if sha(payload) != digest:
            raise ValueError("Runtime distribution digest mismatch")
        files[name] = payload
    return files


def build_files(plan: dict, source: dict, analysis: dict, job_id: str, plan_id: str, components: dict | None = None, *, source_bytes: bytes | None = None) -> tuple[str, dict, dict]:
    files = dict(runtime_files())
    components = components or {}
    for asset_id, asset in components.items():
        if len(asset_id) != 64 or any(c not in "0123456789abcdef" for c in asset_id) or sha(asset["bytes"]) != asset_id:
            raise ValueError("Component content hash mismatch")
        files[f"component-{asset_id}.glb"] = asset["bytes"]
    if components:
        files["components.json"] = canonical({identity:value["record"] for identity,value in components.items()})
    if plan.get("reference_projection"):
        from .source_projection import projection_assets
        files.update(projection_assets(plan, source, analysis, source_bytes))
    files["scene.json"] = canonical(plan)
    files["reference-annotations.json"] = canonical(analysis)
    manifest = {
        "kind": "canvaslab-3d-build-v1", "server_version": VERSION, "contract_version": CONTRACT,
        "generator_digest": GENERATOR_DIGEST,
        "job_id": job_id, "plan_id": plan_id, "source": source,
        "component_ids": sorted(components),
        "files": {name: sha(payload) for name, payload in sorted(files.items())},
        "preview_only": True, "workflow_complete": False,
    }
    build_id = sha(canonical(manifest))
    files["build-manifest.json"] = canonical({**manifest, "build_id": build_id})
    return build_id, files, manifest


def write_build(directory: Path, files: dict[str, bytes]) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = directory.parent / f".{directory.name}-{uuid.uuid4().hex}.staging"
    staging.mkdir(exist_ok=False)
    for name, payload in files.items():
        (staging / name).write_bytes(payload)
    with zipfile.ZipFile(staging / "project.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    staging.rename(directory)


def verify_build(directory: Path) -> dict:
    manifest = json.loads((directory / "build-manifest.json").read_text(encoding="utf-8"))
    build_id = manifest.pop("build_id")
    if sha(canonical(manifest)) != build_id or directory.name != build_id:
        raise ValueError("Build identity mismatch")
    for name, digest in manifest["files"].items():
        if "/" in name or "\\" in name or name.startswith(".") or sha((directory / name).read_bytes()) != digest:
            raise ValueError("Build asset changed")
    return {**manifest, "build_id": build_id}
