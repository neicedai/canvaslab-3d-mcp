"""Real official-weight inference through the queue on CPU; never a P4/GPU benchmark."""
from __future__ import annotations
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
from PIL import Image, ImageDraw
from server3d.jobs import Store
from server3d.gpu_jobs import GPUQueue
from gpu3d.worker import hardware_probe, run_task


def run(root, models):
    from mcp import Client
    from server3d.mcp_server import create_mcp
    os.environ["CANVASLAB3D_GPU_ENABLED"]="1"
    store=Store(root);queue=GPUQueue(store)
    image=Image.new("RGB",(128,96),"#eeeeee")
    draw=ImageDraw.Draw(image);draw.rectangle((28,22,94,80),fill="#d86d43")
    draw.polygon([(18,25),(60,4),(108,25)],fill="#314d55")
    raw=io.BytesIO();image.save(raw,"PNG")
    source=store.upload(raw.getvalue());jid=store.submit(source["asset_id"],"synthetic CPU inference smoke","source")["job_id"]
    analysis={"source_sha256":source["sha256"],"scene_box":[0,0,128,96],
        "regions":[{"id":"house","label":"synthetic house","box":[16,2,94,82],"critical":True,
                    "confidence":.8,"evidence":"synthetic fixture bounding box"}],
        "assumptions":["synthetic, not reconstruction acceptance"],"change_reason":"initial"}
    store.save_analysis(jid,analysis,0)
    identity=hardware_probe("cpu");queue.register("real-cpu-smoke",identity,["depth","segment"])
    before=store.handoff(jid)
    async def submit_via_mcp():
        async with Client(create_mcp(store)) as client:
            results=[]
            for operation in ("depth","segment"):
                request={"operation":operation,"execution":"cpu"}
                if operation=="segment":request["region_id"]="house"
                reply=await client.call_tool("submit_scene_gpu_task",{"job_id":jid,
                    "source_sha256":source["sha256"],"analysis_revision":1,"request":request,"idempotency_key":operation})
                if reply.is_error:raise AssertionError(reply.content)
                results.append(reply.structured_content["task_id"])
            return results
    task_ids=asyncio.run(submit_via_mcp());results=[]
    for tid in task_ids:
        task=queue.claim("real-cpu-smoke")
        assert task and task["task_id"]==tid
        run_task(queue,"real-cpu-smoke",task,models,"cpu")
        result=queue.get(tid)
        assert result["state"]=="succeeded",result
        assert result["result"]["execution"]=="cpu"
        assert result["result"]["metadata"]["runtime"]["p4_hardware_test"] is False
        results.append(result)
    assert store.handoff(jid)==before
    report={"mode":"real_official_models_cpu_only","p4_hardware_tested":False,
        "cuda_tested":False,"source_job_unchanged":True,"results":results}
    (root/"gpu-cpu-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"cpu_inference_passed":True,"operations":["depth","segment"],"p4_hardware_tested":False}))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);p.add_argument("--models",type=Path,required=True)
    a=p.parse_args();run(a.output.resolve(),a.models.resolve())
