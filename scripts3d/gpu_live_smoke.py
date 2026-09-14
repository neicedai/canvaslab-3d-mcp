"""Exercise a running MCP and its real CUDA workers; retain VRAM lifecycle evidence."""
import argparse
import hashlib
import io
import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw


def run(url, token_file, output):
    headers = {"Authorization": "Bearer " + token_file.read_text().strip(),
               "Accept": "application/json, text/event-stream"}
    def rpc(name, arguments):
        response = httpx.post(url + "/mcp-3d", headers=headers, timeout=30,
                             json={"jsonrpc":"2.0", "id":1, "method":"tools/call",
                                   "params":{"name":name,"arguments":arguments}})
        response.raise_for_status()
        payload = next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: "))
        result = payload["result"]
        if result.get("isError"):
            raise RuntimeError(result)
        return result.get("structuredContent") or json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
    def memory():
        result = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
                                check=True, capture_output=True, text=True)
        return [int(line.split(",")[1]) for line in result.stdout.strip().splitlines()]
    before = memory()
    image = Image.new("RGB", (256,192), "#eeeeee")
    draw = ImageDraw.Draw(image)
    draw.rectangle((56,44,188,160), fill="#d86d43")
    draw.polygon([(36,50),(120,8),(216,50)], fill="#314d55")
    stream = io.BytesIO(); image.save(stream, "PNG"); raw = stream.getvalue()
    response = httpx.post(url + "/assets", headers=headers, content=raw, timeout=30)
    response.raise_for_status(); source = response.json()
    job = rpc("submit_scene_reference", {"asset_id":source["asset_id"], "requirements":"synthetic real P4 lifecycle test", "idempotency_key":uuid.uuid4().hex})
    jid = job["job_id"]
    analysis = {"source_sha256":hashlib.sha256(raw).hexdigest(), "scene_box":[0,0,256,192],
                "regions":[{"id":"house","label":"synthetic house","box":[32,4,188,164],"critical":True,
                            "confidence":0.8,"evidence":"synthetic fixture measured box"}],
                "assumptions":["synthetic, not visual reconstruction acceptance"], "change_reason":"initial"}
    rpc("save_scene_analysis", {"job_id":jid,"analysis":analysis,"base_revision":0})
    tasks = []
    for operation in ("depth", "segment"):
        request = {"operation":operation,"execution":"cuda"}
        if operation == "segment": request["region_id"] = "house"
        tasks.append(rpc("submit_scene_gpu_task", {"job_id":jid,"source_sha256":analysis["source_sha256"],
                     "analysis_revision":1,"request":request,"idempotency_key":uuid.uuid4().hex})["task_id"])
    samples = []
    deadline = time.monotonic() + 650
    while time.monotonic() < deadline:
        results = [rpc("get_scene_gpu_task", {"task_id":tid}) for tid in tasks]
        sample = {"time":time.time(), "memory_mib":memory(), "states":[r["state"] for r in results]}
        samples.append(sample); print(json.dumps(sample), flush=True)
        if all(r["state"] in {"succeeded","failed","cancelled"} for r in results): break
        time.sleep(3)
    else: raise RuntimeError("GPU smoke timed out")
    time.sleep(3)
    after = memory()
    report = {"job_id":jid,"before_mib":before,"after_mib":after,"samples":samples,"results":results}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert all(r["state"] == "succeeded" for r in results), results
    assert all(r["result"]["metadata"]["runtime"]["p4_hardware_test"] for r in results)
    assert all(a <= b+32 for a,b in zip(after,before)), (before,after)
    print(json.dumps({"real_p4_passed":True,"before_mib":before,"after_mib":after}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8031")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.url,args.token_file,args.output)
