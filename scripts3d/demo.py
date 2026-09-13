"""Create an isolated development build. Original image bytes are never resized."""
import argparse
import json
from pathlib import Path

from benchmarks3d.scenes import analysis_for, pavilion, water_town
from server3d.jobs import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--data", type=Path, default=Path(".canvaslab3d"))
    parser.add_argument("--variant", choices=["water-town", "pavilion"], default="water-town")
    parser.add_argument("--source-layout", choices=["water-original", "unmeasured"], default="water-original")
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    store = Store(args.data)
    source = store.upload(args.image.read_bytes())
    plan = water_town() if args.variant == "water-town" else pavilion()
    job = store.submit(source["asset_id"], "Procedural development fixture; not certified reconstruction", f"demo-{args.variant}-{source['sha256']}")
    job = store.job(job["job_id"])
    analysis = store.save_analysis(job["job_id"], analysis_for(source, plan, args.source_layout), job["analysis_revision"])
    revision = store.validate(job["job_id"], plan, analysis["analysis_revision"], job["plan_revision"])
    build = store.build(job["job_id"], revision["plan_id"], f"build-{revision['plan_revision']}")
    result = {"job_id": job["job_id"], "build_id": build["build_id"], "directory": str(store.build_path(job["job_id"], build["build_id"])), "workflow_complete": False}
    if args.capture:
        capture = store.capture(job["job_id"], build["build_id"])
        result["capture_id"] = capture["capture_id"]
        result["checks"] = capture["observation"]["tests"]
        result["errors"] = capture["observation"]["errors"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
