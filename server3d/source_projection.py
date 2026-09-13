"""Package verified native source pixels for explicit mesh-surface projection.

This preserves source-lit appearance, not albedo or unseen geometry. The original
is never embedded as a foreground plane. Masks come from immutable annotations.
"""
from __future__ import annotations

import hashlib
import io
import json

from PIL import Image, ImageFilter, UnidentifiedImageError

from .reference_metrics import source_mask
from .scene_schema import ScenePlan, validate_analysis

MAX_PIXELS = 4_194_304
MAX_SIDE = 4096
MAX_BYTES = 24 * 1024 * 1024


def _png(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def projection_assets(plan, source, analysis, payload):
    """Fail closed on missing/changed bytes; output only fixed local asset names."""
    parsed = ScenePlan.model_validate(plan)
    if parsed.reference_projection is None:
        return {}
    analysis = validate_analysis(analysis, source).model_dump()
    if (type(payload) is not bytes or not payload or len(payload) > 20*1024*1024
            or hashlib.sha256(payload).hexdigest() != source["sha256"]):
        raise ValueError("Projection requires exact original image bytes, not a preview or different export")
    crop = analysis["scene_box"]
    if any(float(v) != int(v) for v in crop):
        raise ValueError("Source projection requires an integer native crop; no resampling fallback")
    x, y, w, h = map(int, crop)
    if w*h > MAX_PIXELS or max(w, h) > MAX_SIDE:
        raise ValueError("Source projection exceeds native texture budget; no thumbnail fallback")
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            if (opened.format not in {"PNG", "JPEG", "WEBP"} or opened.size != (source["width"], source["height"])
                    or opened.width*opened.height > 16_777_216 or max(opened.size) > 8192
                    or getattr(opened, "n_frames", 1) != 1 or opened.getexif().get(274, 1) != 1):
                raise ValueError("Unsupported projection source dimensions, orientation or image format")
            if opened.convert("RGBA").getchannel("A").getextrema()[0] != 255:
                raise ValueError("Source projection requires an explicitly opaque source")
            # RGB code values are retained; no guessed ICC/exposure transformation.
            if opened.info.get("icc_profile"):
                raise ValueError("Projection requires an explicitly sRGB source without an ambiguous ICC profile")
            image = opened.convert("RGB").crop((x, y, x+w, y+h))
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid projection source image") from exc
    regions = {r["id"]: r for r in analysis["regions"]}
    objects = {o.id: o for o in parsed.objects}
    files = {"reference-source.png": _png(image)}
    mapping = []
    for identity in parsed.reference_projection.object_ids:
        region = regions[objects[identity].region_ids[0]]
        if not region["visible_polygons"] or region["confidence"] < .8:
            raise ValueError("Projected objects need reliable ORIGINAL visible polygons and holes")
        # Protect silhouette/occlusion boundaries; do not spread source pixels
        # outside their measured owner or fill annotated openings.
        mask = source_mask(region, crop, (w, h)).filter(ImageFilter.MinFilter(5))
        if mask.getbbox() is None:
            raise ValueError("Source projection mask has no reliable interior pixels")
        name = f"reference-mask-{identity}.png"
        files[name] = _png(mask.convert("RGB"))
        mapping.append({"object_id": identity, "region_id": region["id"], "mask": name})
    metadata = {"kind": "source-appearance-projection-v1", "source_sha256": source["sha256"],
                "scene_box": crop, "width": w, "height": h, "source": "reference-source.png",
                "objects": mapping, "mask_inset_pixels": 2, "resampled": False,
                "appearance_contains_original_lighting": True, "albedo_recovered": False,
                "unseen_surfaces": "authored material fallback; not recovered from original"}
    files["reference-projection.json"] = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError("Source projection assets exceed encoded byte budget")
    return files
