"""Durable queue tests with synthetic predictions; NOT Tesla P4 hardware validation."""
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image

from server3d.jobs import Store, Conflict
from server3d.gpu_jobs import GPUQueue, VisionRequest, source_image, HEARTBEAT_TTL
from gpu3d.models import verify, MODELS
from gpu3d.outputs import write_outputs
from gpu3d.worker import device_lock, offline_env


class GPUQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict("os.environ", {"CANVASLAB3D_GPU_ENABLED":"1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.store = Store(Path(self.temp.name))
        self.q = GPUQueue(self.store)
        stream = io.BytesIO(); Image.new("RGB", (96,96), "white").save(stream,"PNG")
        self.raw = stream.getvalue(); self.source = self.store.upload(self.raw)
        self.jid = self.store.submit(self.source["asset_id"],"synthetic GPU test","source")["job_id"]
        self.analysis = {"source_sha256":self.source["sha256"],"scene_box":[8,8,64,64],
            "regions":[{"id":"house","label":"house","box":[16,16,40,40],"critical":True,
                        "confidence":.8,"evidence":"synthetic box, not a P4 result"}],
            "assumptions":["test"],"change_reason":"initial"}
        self.store.save_analysis(self.jid,self.analysis,0)
        self.identity = {"execution":"cuda", "device_key":"synthetic-device-0", "name":"test double"}
        self.q.register("worker0",self.identity,["depth","segment"])

    def submit(self, operation="depth", key="task", **extra):
        req = {"operation":operation,**extra}
        if operation == "segment": req.setdefault("region_id","house")
        return self.q.submit(self.jid,req,1,self.source["sha256"],key)

    def write(self, task):
        work = self.q.root/task["task_id"]; work.mkdir(parents=True,exist_ok=True)
        image = source_image(self.store,task["snapshot"])
        data = np.arange(4096,dtype=np.float32).reshape(64,64) if task["request"]["operation"] == "depth" else np.ones((64,64),dtype=bool)
        return write_outputs(work/"output",task,data,image,{"runtime":self.identity,"synthetic_test":True})

    def test_no_torch_needed_for_server_status(self):
        state = self.q.status()
        self.assertFalse(state["combined_vram"])
        self.assertFalse(state["image_to_mesh_enabled"])
        self.assertEqual(state["max_inflight_per_device"],1)

    def test_explicit_enable_required(self):
        with patch.dict("os.environ",{"CANVASLAB3D_GPU_ENABLED":"0"}):
            with self.assertRaisesRegex(ValueError,"disabled"): self.submit()

    def test_no_implicit_cpu_fallback(self):
        with self.assertRaisesRegex(ValueError,"No online"): self.submit(execution="cpu")

    def test_offline_workers_not_admitted(self):
        with self.store.transaction() as db:
            w=self.store.get(db,"gpu_worker","worker0");w["last_seen"]=0;self.store.put(db,"gpu_worker","worker0",w)
        with self.assertRaisesRegex(ValueError,"No online"): self.submit()

    def test_unknown_operation_script_and_bad_resolution_rejected(self):
        for req in ({"operation":"image_to_mesh"},{"operation":"depth","url":"https://example.com"},
                    {"operation":"depth","depth_input_side":4096},{"operation":"segment"},
                    {"operation":"depth","points":[{"x":20,"y":20,"label":1}]}):
            with self.subTest(req=req),self.assertRaises(ValueError): VisionRequest.model_validate(req)

    def test_source_revision_and_identity_are_bound(self):
        for revision,source in [(0,self.source["sha256"]),(1,"a"*64),(True,self.source["sha256"])]:
            with self.assertRaises(ValueError): self.q.submit(self.jid,{"operation":"depth"},revision,source,"bad")

    def test_source_tampering_rejected_before_enqueue(self):
        (self.store.root/"assets"/self.source["asset_id"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"changed"): self.submit()

    def test_points_native_bounds_and_region_ids(self):
        for extra in ({"region_id":"absent"},{"points":[{"x":0.,"y":0.,"label":1}]}):
            with self.assertRaises(ValueError): self.submit("segment",**extra)
        task=self.submit("segment",points=[{"x":30.,"y":32.,"label":1}])
        self.assertEqual(task["request"]["points"][0]["x"],30.)

    def test_repeat_key_is_idempotent_across_store_restart(self):
        task=self.submit()
        other=GPUQueue(Store(self.store.root))
        same=other.submit(self.jid,{"operation":"depth"},1,self.source["sha256"],"task")
        self.assertEqual(task["task_id"],same["task_id"])
        with self.assertRaises(Conflict): self.submit("segment")

    def test_worker_cannot_claim_two_and_two_workers_do_not_duplicate(self):
        self.submit(key="one");self.submit(key="two")
        first=self.q.claim("worker0")
        with self.assertRaises(ValueError): self.q.claim("worker0")
        self.q.register("worker1",{**self.identity,"device_key":"synthetic-device-1"},["depth"])
        second=self.q.claim("worker1")
        self.assertNotEqual(first["task_id"],second["task_id"])

    def test_capability_routing(self):
        self.submit("segment")
        self.q.register("depth-only",self.identity,["depth"])
        self.assertIsNone(self.q.claim("depth-only"))
        self.assertIsNotNone(self.q.claim("worker0"))

    def test_success_keeps_original_plan_source_and_analysis_unchanged(self):
        task=self.submit(); before=self.store.handoff(self.jid)
        claimed=self.q.claim("worker0");self.write(claimed)
        self.assertTrue(self.q.finish(task["task_id"],claimed["claim_token"]))
        result=self.q.get(task["task_id"])
        self.assertEqual(result["state"],"succeeded")
        self.assertFalse(result["result"]["metric_depth"])
        self.assertTrue(result["advisory_only"])
        self.assertEqual(before,self.store.handoff(self.jid))
        self.assertEqual((self.store.root/"assets"/self.source["asset_id"]).read_bytes(),self.raw)

    def test_mask_holes_are_not_auto_adopted_into_annotations(self):
        self.submit("segment");task=self.q.claim("worker0");self.write(task)
        self.q.finish(task["task_id"],task["claim_token"])
        self.assertEqual(self.q.get(task["task_id"])["result"]["foreground_pixels"],4096)
        self.assertEqual(self.store.handoff(self.jid)["analysis"]["regions"][0]["visible_polygons"],[])

    def test_cancel_running_fences_late_completion(self):
        task=self.submit();claimed=self.q.claim("worker0");self.write(claimed)
        self.q.cancel(task["task_id"])
        self.assertFalse(self.q.heartbeat("worker0",task["task_id"],claimed["claim_token"]))
        self.assertFalse(self.q.finish(task["task_id"],claimed["claim_token"]))
        self.assertEqual(self.q.get(task["task_id"])["files"],{})

    def test_wrong_token_cannot_publish(self):
        task=self.submit();claimed=self.q.claim("worker0");self.write(claimed)
        self.assertFalse(self.q.finish(task["task_id"],"wrong"))
        self.assertEqual(self.q.get(task["task_id"])["state"],"running")

    def test_revision_change_discards_running_results(self):
        self.submit();task=self.q.claim("worker0");self.write(task)
        self.store.save_analysis(self.jid,self.analysis,1)
        self.assertFalse(self.q.finish(task["task_id"],task["claim_token"]))
        self.assertFalse(self.q.get(task["task_id"])["matches_current_analysis"])

    def test_worker_death_fails_without_blind_requeue(self):
        self.submit();task=self.q.claim("worker0")
        with self.store.transaction() as db:
            task["heartbeat"]=time.time()-HEARTBEAT_TTL-1;self.store.put(db,"gpu_task",task["task_id"],task)
        self.assertEqual(self.q.get(task["task_id"])["state"],"failed")
        self.assertIsNone(self.q.claim("worker0"))

    def test_bad_result_identity_and_bad_device_rejected(self):
        self.submit();task=self.q.claim("worker0");self.write(task)
        path=self.q.root/task["task_id"]/"output"/"result.json"
        result=json.loads(path.read_text());result["source_sha256"]="a"*64;path.write_text(json.dumps(result))
        with self.assertRaises(ValueError): self.q.finish(task["task_id"],task["claim_token"])
        result["source_sha256"]=task["source_sha256"];result["metadata"]["runtime"]["device_key"]="different";path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError,"physical device"): self.q.finish(task["task_id"],task["claim_token"])

    def test_changed_output_cannot_be_downloaded(self):
        self.submit();task=self.q.claim("worker0");self.write(task);self.q.finish(task["task_id"],task["claim_token"])
        (self.q.root/task["task_id"]/"output"/"depth.npy").write_bytes(b"tamper")
        with self.assertRaisesRegex(ValueError,"changed"): self.q.artifact(task["task_id"],"depth.npy")

    def test_paths_not_exposed(self):
        task=self.submit()
        self.assertNotIn("snapshot",task);self.assertNotIn("claim_token",task)
        for name in ["../../capture.key","worker.log"]:
            with self.assertRaises(ValueError): self.q.artifact(task["task_id"],name)
        with self.assertRaises(ValueError): self.q.get("../../")

    def test_queue_full_is_bounded(self):
        with patch("server3d.gpu_jobs.MAX_TASKS",0):
            with self.assertRaisesRegex(ValueError,"quota"): self.submit()

    def test_nan_depth_rejected(self):
        self.submit();task=self.q.claim("worker0")
        with self.assertRaises(ValueError):
            write_outputs(self.q.root/task["task_id"]/"output",task,np.full((64,64),np.nan),
                          Image.new("RGB",(64,64)),{"runtime":self.identity})

    def test_single_physical_device_lock(self):
        with device_lock(self.store.root/"locks","uuid"):
            with self.assertRaises(ValueError):
                with device_lock(self.store.root/"locks","uuid"): pass
        with device_lock(self.store.root/"locks","uuid"): pass

    def test_offline_child_removes_secrets(self):
        with patch.dict("os.environ",{"CANVASLAB3D_TOKEN":"secret","HF_TOKEN":"secret"}):
            env=offline_env();self.assertNotIn("CANVASLAB3D_TOKEN",env);self.assertNotIn("HF_TOKEN",env)
            self.assertEqual(env["HF_HUB_OFFLINE"],"1")

    def test_missing_models_not_marked_ready(self):
        with self.assertRaisesRegex(ValueError,"Missing"): verify(Path(self.temp.name),"depth")
        self.assertEqual(len(MODELS["segment"]["revision"]),40)


if __name__ == "__main__": unittest.main()
