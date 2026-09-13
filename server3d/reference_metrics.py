"""Source-owned, native-resolution appearance diagnostics; no image registration.

Masks come only from original annotations, never from the generated object.
Metrics operate on decoded RGB code values, not a calibrated perceptual score.
Original bytes are preserved; only an explicitly fractional crop is resampled.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

METHOD = "native-source-appearance-v1"
MAX_PIXELS = 8_388_608


def source_mask(region, scene_box, size):
    sx, sy, sw, sh = scene_box
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    transform = lambda p: ((p[0]-sx)*size[0]/sw, (p[1]-sy)*size[1]/sh)
    for polygon in region.get("visible_polygons", []):
        draw.polygon([transform(p) for p in polygon], fill=255)
    for hole in region.get("visible_holes", []):
        draw.polygon([transform(p) for p in hole], fill=0)
    return mask


def _open_rgb(path, pixel_limit):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 32*1024*1024:
        raise ValueError("Appearance evidence is missing or exceeds its byte budget")
    with Image.open(path) as image:
        if (image.format not in {"PNG", "JPEG", "WEBP"} or image.width*image.height > pixel_limit
                or max(image.size) > 8192 or getattr(image, "n_frames", 1) != 1):
            raise ValueError("Unsupported appearance image or decoded size")
        if image.convert("RGBA").getchannel("A").getextrema()[0] != 255:
            raise ValueError("Appearance comparison requires an explicitly opaque source and capture")
        return image.convert("RGB")


def measure_reference_appearance(source_path, capture_path, analysis):
    """Compare a hash-checked original against its exact signed reference-view PNG."""
    source_path = Path(source_path)
    if source_path.stat().st_size > 20*1024*1024:
        raise ValueError("Original exceeds source upload budget")
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != analysis["source_sha256"]:
        raise ValueError("Original image bytes do not match source SHA256")
    sx, sy, sw, sh = analysis["scene_box"]
    if (not all(type(v) in (int, float) and math.isfinite(v) for v in (sx, sy, sw, sh))
            or min(sx, sy) < 0 or min(sw, sh) < 1):
        raise ValueError("Invalid source crop")
    size = (int(sw+.5), int(sh+.5))
    if size[0]*size[1] > MAX_PIXELS or max(size) > 4096:
        raise ValueError("Native comparison exceeds the capture budget; no thumbnail fallback")
    original = _open_rgb(source_path, 16_777_216)
    captured = _open_rgb(capture_path, MAX_PIXELS)
    if sx+sw > original.width or sy+sh > original.height or captured.size != size:
        raise ValueError("Capture dimensions or source crop do not match native evidence")
    extent = (sx, sy, sx+sw, sy+sh)
    fractional = any(float(v) != int(v) for v in (sx, sy, sw, sh))
    reference = (original.transform(size, Image.Transform.EXTENT, extent, Image.Resampling.BICUBIC)
                 if fractional else original.crop(tuple(int(v) for v in extent)))
    del original
    a = np.asarray(reference, dtype=np.float32)/255
    b = np.asarray(captured, dtype=np.float32)/255
    pixel_error = np.mean(np.abs(a-b), axis=2)
    del a, b
    # Coarse color errors are kept distinct from native detail/edge errors.
    color_error = np.mean(np.abs(np.asarray(reference.filter(ImageFilter.GaussianBlur(4)), dtype=np.float32)
                                - np.asarray(captured.filter(ImageFilter.GaussianBlur(4)), dtype=np.float32)), axis=2)/255
    gray_a, gray_b = reference.convert("L"), captured.convert("L")
    a, b = np.asarray(gray_a, dtype=np.float32)/255, np.asarray(gray_b, dtype=np.float32)/255
    difference = a-b
    edge_error = np.zeros_like(a)
    edge_error[:, 1:] += np.abs(np.diff(difference, axis=1))/4
    edge_error[1:, :] += np.abs(np.diff(difference, axis=0))/4
    detail_error = np.zeros_like(a)
    for radius, weight in ((1, .6), (2, .3), (4, .1)):
        blur_a = np.asarray(gray_a.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)/255
        blur_b = np.asarray(gray_b.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)/255
        detail_error += weight*np.abs(difference-(blur_a-blur_b))/2
    del a, b, difference, blur_a, blur_b
    maps = {"color_error":color_error, "edge_error":edge_error, "detail_error":detail_error,
            "pixel_error":pixel_error}
    combined = .4*color_error + .35*edge_error + .25*detail_error
    rows, patches = [], []
    for region in analysis["regions"]:
        row = {"region_id":region["id"], "critical":region["critical"], "available":False}
        if not region.get("visible_polygons") or region["confidence"] < .8:
            row["reason"] = "missing_or_low_confidence_original_silhouette"
            rows.append(row)
            continue
        mask_image = source_mask(region, analysis["scene_box"], size)
        mask = np.asarray(mask_image) > 0
        count = int(np.count_nonzero(mask))
        if count < 8:
            row["reason"] = "insufficient_original_visible_pixels"
            rows.append(row)
            continue
        row.update(available=True, source_visible_pixels=count, loss=float(np.mean(combined[mask])))
        row.update({key:float(np.mean(value[mask])) for key, value in maps.items()})
        left, top, right, bottom = mask_image.getbbox()
        # At most ~4096 tiles per region; do not resize either reference image.
        tile = max(32, math.ceil(math.sqrt((right-left)*(bottom-top)/4096)))
        local = []
        for y in range(top, bottom, tile):
            for x in range(left, right, tile):
                x2, y2 = min(x+tile, right), min(y+tile, bottom)
                visible = mask[y:y2, x:x2]
                if np.count_nonzero(visible) < 8:
                    continue
                values = {key:float(np.mean(value[y:y2, x:x2][visible])) for key, value in maps.items()}
                score = float(np.mean(combined[y:y2, x:x2][visible]))
                local.append({"region_id":region["id"], "loss":score, **values,
                              "source_box":[sx+x*sw/size[0], sy+y*sh/size[1],
                                            (x2-x)*sw/size[0], (y2-y)*sh/size[1]]})
        local.sort(key=lambda item: item["loss"], reverse=True)
        row["worst_patch_loss"] = local[0]["loss"] if local else row["loss"]
        patches.extend(local[:3])
        rows.append(row)
    scored = [row for row in rows if row["available"]]
    patches.sort(key=lambda item: item["loss"], reverse=True)
    return {"method":METHOD, "available":bool(scored), "source_sha256":analysis["source_sha256"],
            "source_crop":analysis["scene_box"], "native_size":list(size), "fractional_crop_resampled":fractional,
            "regions":rows, "mean_region_loss":sum(r["loss"] for r in scored)/len(scored) if scored else None,
            "worst_patches":patches[:12], "lower_is_better":True, "acceptance_supported":False,
            "measurement":"RGB code-value, edge and 1/2/4-pixel high-pass differences on ORIGINAL masks",
            "warning":"No registration, exposure fit or generated-mask exclusion. Lighting affects these diagnostics; they are not a fidelity percentage."}


def compare_appearance(baseline, candidate):
    """Do not hide critical local/detail regression behind better average color."""
    for key in ("method", "source_sha256", "source_crop", "native_size"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError("Appearance evidence is not comparable: " + key)
    a = {row["region_id"]:row for row in baseline["regions"]}
    b = {row["region_id"]:row for row in candidate["regions"]}
    if set(a) != set(b) or not a:
        raise ValueError("Appearance regions differ")
    regressions, unscored, rows = [], [], []
    for key in sorted(a):
        before, after = a[key], b[key]
        if before["critical"] != after["critical"]:
            raise ValueError("Appearance region criticality changed")
        if not before["available"] or not after["available"]:
            if before["critical"]:
                unscored.append(key)
            continue
        deltas = {name:after[name]-before[name] for name in
                  ("loss", "color_error", "edge_error", "detail_error", "worst_patch_loss")}
        tolerance = .003 if before["critical"] else .015
        bad = [name for name, delta in deltas.items() if delta > (tolerance*3 if name == "worst_patch_loss" else tolerance)]
        if bad:
            regressions.append({"region_id":key, "metrics":bad})
        rows.append({"region_id":key, "delta":deltas})
    delta = sum(row["delta"]["loss"] for row in rows)/len(rows) if rows else None
    return {"comparable":bool(rows), "regressions":regressions, "unscored_critical_regions":unscored,
            "mean_loss_delta":delta, "regions":rows, "automatic_acceptance":False,
            "recommendation":("rollback_recommended" if regressions else "inconclusive" if unscored or delta is None
                              else "prefer_candidate" if delta <= -.001 else "inconclusive"),
            "thresholds":"diagnostic: critical metric +0.003 / other +0.015; worst tile 3x; mean improvement 0.001"}
