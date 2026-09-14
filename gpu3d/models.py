"""Operator-only downloads of fixed safetensors; inference is strictly offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

MODELS = {
    "depth": {
        "repo": "depth-anything/Depth-Anything-V2-Small-hf",
        "revision": "5426e4f0f36572d16453bbda7a8389317b1bef99",
        "weight_sha256": "3152477ce0d8d6978d76b995120de97cb5b928701fd0f817769f59e249a16b70",
        "license": "Apache-2.0",
    },
    "segment": {
        "repo": "Zigeng/SlimSAM-uniform-77",
        "revision": "06892690452cd98a45dc633d3e8068e8b79bd8bb",
        "weight_sha256": "b0ee221e3d33935807a820c157fec81a537b3b9979edc88b148a8dae8bbdd5d2",
        "license": "Apache-2.0",
    },
}
FILES = ("config.json", "preprocessor_config.json", "model.safetensors", "README.md")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify(root: Path, operation: str) -> dict:
    if operation not in MODELS:
        raise ValueError("Unsupported GPU operation")
    folder = root / operation
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 16384:
        raise ValueError(f"Missing {operation} model; run python -m gpu3d.models download first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("model") != MODELS[operation] or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Model manifest does not match the fixed registry")
    for name in FILES:
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("Model file missing, symlinked or oversized")
        if digest(path) != manifest["files"][name]:
            raise ValueError(f"Model file changed: {operation}/{name}")
    if manifest["files"]["model.safetensors"] != MODELS[operation]["weight_sha256"]:
        raise ValueError("Checkpoint differs from publisher SHA256")
    return manifest


def download(root: Path, operations: list[str]) -> None:
    # No model URLs, revisions, code or credentials are accepted from an MCP task.
    from huggingface_hub import hf_hub_download
    root.mkdir(parents=True, exist_ok=True)
    for operation in operations:
        spec = MODELS[operation]
        target = root / operation
        if target.exists():
            verify(root, operation)
            continue
        stage = Path(tempfile.mkdtemp(prefix=".download-", dir=root))
        try:
            for name in FILES:
                source = hf_hub_download(spec["repo"], filename=name, revision=spec["revision"])
                shutil.copyfile(source, stage / name)
            manifest = {"model": spec, "files": {n: digest(stage / n) for n in FILES}}
            if manifest["files"]["model.safetensors"] != spec["weight_sha256"]:
                raise ValueError("Downloaded checkpoint SHA256 mismatch")
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
            stage.rename(target)
            verify(root, operation)
        finally:
            if stage.exists():
                shutil.rmtree(stage)  # Only our freshly created temporary directory.
        print(json.dumps({"operation": operation, "revision": spec["revision"], "verified": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "verify"))
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--operations", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    args = parser.parse_args()
    if args.action == "download":
        download(args.models.resolve(), args.operations)
    else:
        for op in args.operations:
            print(json.dumps(verify(args.models.resolve(), op)))
