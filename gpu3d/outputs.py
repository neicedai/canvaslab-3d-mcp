"""Bounded native-coordinate prediction artifacts, never acceptance evidence."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from gpu3d import CONTRACT
from gpu3d.models import MODELS, digest


def edges(image):
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    gy, gx = np.gradient(gray)
    magnitude = np.hypot(gx, gy)
    scale = max(float(np.percentile(magnitude, 99)), 1.)
    return np.clip(magnitude * (255 / scale), 0, 255).astype(np.uint8)


def write_outputs(folder: Path, task: dict, prediction, image, metadata: dict):
    folder.mkdir(parents=True, exist_ok=False)
    analysis = task["snapshot"]["analysis"]
    x, y, w, h = map(int, analysis["scene_box"])
    if prediction.shape != (h, w) or not np.isfinite(prediction).all():
        raise ValueError("Invalid prediction dimensions or nonfinite values")
    op = task["request"]["operation"]
    result = {"contract": CONTRACT, "task_id": task["task_id"], "operation": op,
              "source_sha256": task["source_sha256"], "analysis_id": task["analysis_id"],
              "crop": [x,y,w,h], "model": MODELS[op], "execution": task["request"]["execution"],
              "advisory_only": True, "metric_depth": False, "automatic_scene_mutation": False,
              "metadata": metadata, "region_summaries": []}
    Image.fromarray(edges(image)).save(folder / "edges.png")
    if op == "depth":
        prediction = np.asarray(prediction, dtype=np.float32)
        with (folder / "depth.npy").open("xb") as stream:
            np.save(stream, prediction, allow_pickle=False)
        low, high = float(prediction.min()), float(prediction.max())
        preview = np.round((prediction-low)/max(high-low, 1e-8)*65535).astype(np.uint16)
        Image.fromarray(preview).save(folder / "depth.png")
        result.update(semantics="relative_inverse_depth_larger_usually_nearer_not_meters", range=[low,high])
        for region in analysis["regions"]:
            # Summaries use source-owned polygons where provided; otherwise a box
            # can contain several objects and is labeled explicitly as such.
            mask = Image.new("L", (w,h))
            draw = ImageDraw.Draw(mask)
            polygons = region.get("visible_polygons", [])
            if polygons:
                for p in polygons:
                    draw.polygon([(a-x,b-y) for a,b in p], fill=255)
                for p in region.get("visible_holes", []):
                    draw.polygon([(a-x,b-y) for a,b in p], fill=0)
            else:
                rx,ry,rw,rh = region["box"]
                draw.rectangle((rx-x,ry-y,rx+rw-x-1,ry+rh-y-1), fill=255)
            sample = prediction[np.asarray(mask) > 0]
            if sample.size:
                result["region_summaries"].append({"region_id": region["id"],
                    "support": "source_polygon" if polygons else "mixed_region_box",
                    "pixels": int(sample.size), "p10": float(np.percentile(sample,10)),
                    "median": float(np.median(sample)), "p90": float(np.percentile(sample,90)),
                    "inferred": True})
    else:
        if not np.isin(prediction, [0,1,False,True]).all():
            raise ValueError("Segmentation must be binary")
        mask = prediction.astype(np.uint8)*255
        Image.fromarray(mask).save(folder / "mask.png")
        bounds = Image.fromarray(mask).getbbox()
        result.update(semantics="model_predicted_mask_requires_source_review",
            region_id=task["request"]["region_id"], foreground_pixels=int((mask > 0).sum()),
            native_box=[bounds[0]+x,bounds[1]+y,bounds[2]+x,bounds[3]+y] if bounds else None)
    (folder / "result.json").write_text(json.dumps(result, allow_nan=False, indent=2)+"\n", encoding="utf-8")
    return result


def validate_outputs(folder: Path, task: dict):
    from server3d.gpu_jobs import OUTPUTS
    op = task["request"]["operation"]
    required = OUTPUTS[op]
    if not folder.is_dir() or {p.name for p in folder.iterdir()} != required:
        raise ValueError("Missing or unexpected GPU output")
    paths = [folder / name for name in required]
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size > 24*1024*1024 for p in paths):
        raise ValueError("GPU output file unsafe or oversized")
    if sum(p.stat().st_size for p in paths) > 40*1024*1024 or (folder/"result.json").stat().st_size > 256*1024:
        raise ValueError("GPU output exceeds budget")
    result = json.loads((folder/"result.json").read_text(encoding="utf-8"))
    expected = {"contract": CONTRACT, "task_id": task["task_id"], "operation": op,
        "analysis_id": task["analysis_id"], "source_sha256": task["source_sha256"],
        "crop": task["snapshot"]["analysis"]["scene_box"], "model": MODELS[op],
        "execution": task["request"]["execution"], "advisory_only": True,
        "metric_depth": False, "automatic_scene_mutation": False}
    if any(result.get(k) != v for k,v in expected.items()):
        raise ValueError("GPU result source, model or execution identity mismatch")
    # Reject NaN/Infinity even when a serializer accepted nonstandard JSON.
    json.dumps(result, allow_nan=False)
    w,h = map(int, expected["crop"][2:])
    for name in required:
        if name.endswith(".png"):
            with Image.open(folder/name) as im:
                if im.format != "PNG" or im.size != (w,h):
                    raise ValueError("GPU raster dimensions mismatch")
                im.load()
                if name == "mask.png" and (im.mode != "L" or not np.isin(np.asarray(im),[0,255]).all()):
                    raise ValueError("Invalid binary mask")
    if op == "depth":
        with (folder/"depth.npy").open("rb") as stream:
            version = np.lib.format.read_magic(stream)
            if version != (1, 0):
                raise ValueError("Unsupported depth array header")
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
            if shape != (h,w) or fortran or dtype != np.dtype("float32"):
                raise ValueError("Unsafe depth array shape or dtype")
        array = np.load(folder/"depth.npy", allow_pickle=False)
        if array.dtype != np.float32 or array.shape != (h,w) or not np.isfinite(array).all():
            raise ValueError("Invalid native float32 depth")
    return result, {p.name: digest(p) for p in paths}
