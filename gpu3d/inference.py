"""Fixed FP32 inference subprocess. Inputs are managed task snapshots, never code/URLs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from gpu3d.models import verify


def probe(device: str) -> dict:
    import torch
    if device == "cpu":
        return {"execution": "cpu", "device": "cpu", "device_key": "cpu",
                "torch": torch.__version__, "cuda": None, "precision": "float32",
                "p4_hardware_test": False}
    if not device.startswith("cuda:") or not device[5:].isdigit():
        raise ValueError("device must be cpu or cuda:<index>")
    if not torch.cuda.is_available():
        raise ValueError("CUDA unavailable; CPU fallback is NOT automatic")
    index = int(device[5:])
    props = torch.cuda.get_device_properties(index)
    arch = torch.cuda.get_arch_list()
    cc = torch.cuda.get_device_capability(index)
    if cc == (6,1) and not any(a in arch for a in ("sm_60","sm_61","compute_60","compute_61")):
        raise ValueError("This PyTorch wheel lacks Pascal support; use the pinned cu118 worker environment")
    stable_id = str(getattr(props,"uuid", ""))
    if not stable_id:
        raise ValueError("PyTorch did not expose a stable GPU UUID; cannot enforce per-device admission")
    torch.cuda.set_device(index)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.enable_flash_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.backends.cuda.enable_math_sdp(True)
    torch.cuda.set_per_process_memory_fraction(.8, index)
    # Actual kernel test: architecture numbers and nominal VRAM alone are insufficient.
    with torch.inference_mode():
        x = torch.ones((16,16), device=device, dtype=torch.float32)
        assert float((x@x).mean().cpu()) == 16.
        torch.nn.functional.conv2d(torch.ones((1,1,8,8),device=device), torch.ones((1,1,3,3),device=device))
        torch.cuda.synchronize(index)
    return {"execution":"cuda", "device":device, "device_key":stable_id,
            "name":props.name, "compute_capability":list(cc), "vram_bytes":props.total_memory,
            "torch":torch.__version__, "cuda":torch.version.cuda, "compiled_arches":arch,
            "precision":"float32", "memory_fraction":.8, "kernel_smoke_passed":True,
            "p4_hardware_test":cc == (6,1) and "P4" in props.name}


def run(task: dict, image_path: Path, output: Path, models: Path, device: str):
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation, SamModel, SamProcessor
    from gpu3d.outputs import write_outputs
    from server3d.builder import sha
    identity = probe(device)
    if identity["execution"] != task["request"]["execution"]:
        raise ValueError("Execution mode differs from the admitted task")
    manifest = verify(models, task["request"]["operation"])
    if sha(image_path.read_bytes()) != task["input_sha256"]:
        raise ValueError("Managed GPU input changed")
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    sx,sy,sw,sh = task["snapshot"]["analysis"]["scene_box"]
    if image.size != (int(sw),int(sh)):
        raise ValueError("Managed crop dimensions changed")
    local = str(models/task["request"]["operation"])
    options = {"local_files_only":True, "trust_remote_code":False, "use_safetensors":True,
               "torch_dtype":torch.float32, "attn_implementation":"eager"}
    started = time.monotonic()
    torch.set_num_threads(2)
    if device != "cpu":
        torch.cuda.reset_peak_memory_stats()
    metadata = {"runtime":identity, "model_manifest":manifest, "source_crop_size":list(image.size),
                "preprocessing_is_not_new_source_evidence":True, "normal_estimation":False}
    with torch.inference_mode():
        if task["request"]["operation"] == "depth":
            processor = AutoImageProcessor.from_pretrained(local, local_files_only=True, trust_remote_code=False, use_fast=False)
            model = AutoModelForDepthEstimation.from_pretrained(local, **options).to(device).eval()
            side = task["request"]["depth_input_side"]
            ratio = side/max(image.size)
            size = tuple(max(14, min(side, int(v*ratio)//14*14)) for v in image.size)
            resized = image.resize(size, Image.Resampling.BICUBIC)
            inputs = processor(images=resized, do_resize=False, return_tensors="pt").to(device)
            prediction = model(**inputs).predicted_depth
            native = torch.nn.functional.interpolate(prediction.unsqueeze(1), size=(image.height,image.width),
                        mode="bicubic", align_corners=False)[0,0].float().cpu().numpy()
            metadata.update(inference_input_size=list(size), output_resampled_to_native=True,
                            depth_units="relative_inverse_depth_not_meters")
        else:
            processor = SamProcessor.from_pretrained(local, local_files_only=True, trust_remote_code=False)
            model = SamModel.from_pretrained(local, **options).to(device).eval()
            region = next(r for r in task["snapshot"]["analysis"]["regions"] if r["id"] == task["request"]["region_id"])
            x,y,w,h = region["box"]
            box = [x-sx,y-sy,x+w-sx,y+h-sy]
            prompts = {"input_boxes":[[box]]}
            points = task["request"]["points"]
            if points:
                prompts.update(input_points=[[[p["x"]-sx,p["y"]-sy] for p in points]],
                               input_labels=[[p["label"] for p in points]])
            inputs = processor(images=image, **prompts, return_tensors="pt").to(device)
            out = model(**inputs, multimask_output=False)
            masks = processor.image_processor.post_process_masks(out.pred_masks.cpu(),
                       inputs["original_sizes"].cpu(), inputs["reshaped_input_sizes"].cpu())
            native = masks[0][0,0].numpy().astype(bool)
            metadata.update(inference_input_size=list(inputs["pixel_values"].shape[-2:][::-1]),
                            mask_score=float(out.iou_scores[0,0,0].cpu()),
                            score_is_not_calibrated_confidence=True, output_resampled_to_native=True)
    metadata["elapsed_seconds"] = time.monotonic()-started
    metadata["peak_allocated_bytes"] = int(torch.cuda.max_memory_allocated()) if device != "cpu" else None
    return write_outputs(output, task, native, image, metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--task", type=Path)
    parser.add_argument("--models", type=Path)
    args = parser.parse_args()
    try:
        if args.probe:
            print(json.dumps(probe(args.device)))
        else:
            if args.task is None or args.models is None or args.task.stat().st_size > 1024*1024:
                raise ValueError("Supply a bounded managed task and model directory")
            task = json.loads(args.task.read_text(encoding="utf-8"))
            run(task, args.task.parent/"input.png", args.task.parent/"output", args.models, args.device)
    except Exception as exc:
        # Tracebacks stay in an operator-owned log; no inference output is published.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
