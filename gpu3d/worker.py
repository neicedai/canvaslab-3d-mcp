"""One long-lived queue consumer and at most one inference subprocess per device.

Linux deployment; local SQLite/shared disk only, never NFS or an open worker API.
The subprocess boundary makes timeout/cancellation/OOM recovery deterministic.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
import uuid

from server3d.builder import REPO, canonical, sha
from server3d.jobs import Store
from server3d.gpu_jobs import GPUQueue, TASK_TIMEOUT, enabled, source_image
from gpu3d.models import MODELS, verify


def offline_env():
    env = os.environ.copy()
    for key in list(env):
        if key in {"CANVASLAB3D_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}:
            env.pop(key)
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="2")
    return env


@contextmanager
def device_lock(root: Path, device_key: str):
    require_linux_worker()
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha256(device_key.encode()).hexdigest()+".lock")
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another worker already owns this physical GPU in this store") from exc
        try:
            yield lock.fileno()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def hardware_probe(device):
    result = subprocess.run([sys.executable, "-m", "gpu3d.inference", "--probe", "--device", device],
        cwd=REPO, env=offline_env(), capture_output=True, text=True, timeout=60, shell=False)
    if result.returncode:
        raise ValueError("GPU preflight failed: "+result.stderr[-1500:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def run_task(queue, worker_id, task, models, device, lock_fd=None):
    work = queue.root / task["task_id"]
    work.mkdir(parents=True, exist_ok=False)
    child = None
    try:
        image = source_image(queue.store, task["snapshot"])
        image.save(work/"input.png")
        snapshot = {**task, "input_sha256":sha((work/"input.png").read_bytes())}
        (work/"task.json").write_bytes(canonical(snapshot))
        with (work/"worker.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable,"-m","gpu3d.inference","--task",str(work/"task.json"),
                "--models",str(models),"--device",device], cwd=REPO, env=offline_env(),
                stdout=log, stderr=subprocess.STDOUT, shell=False,
                pass_fds=() if lock_fd is None else (lock_fd,))
            until = time.monotonic()+TASK_TIMEOUT
            while child.poll() is None:
                if time.monotonic() > until or not queue.heartbeat(worker_id, task["task_id"], task["claim_token"]):
                    child.kill(); child.wait(timeout=10)
                    queue.finish(task["task_id"], task["claim_token"], reason="cancelled_stale_or_timed_out")
                    return
                time.sleep(1)
            if child.returncode:
                tail = (work/"worker.log").read_text(encoding="utf-8", errors="replace")[-4000:]
                reason = "gpu_out_of_memory" if "OutOfMemoryError" in tail or "CUDA out of memory" in tail else "inference_failed_see_managed_worker_log"
                queue.finish(task["task_id"], task["claim_token"], reason=reason)
            else:
                queue.finish(task["task_id"], task["claim_token"])
    except Exception as exc:
        queue.finish(task["task_id"], task["claim_token"], reason=type(exc).__name__+": "+str(exc))
        raise
    finally:
        if child is not None and child.poll() is None:
            child.kill(); child.wait(timeout=10)


def require_linux_worker():
    if not sys.platform.startswith("linux"):
        raise ValueError("GPU workers require Linux (including WSL2 or a Linux container); the MCP server can still run on Windows")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--operations", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    parser.add_argument("--check", action="store_true", help="Verify models and run real device kernels, then exit")
    parser.add_argument("--once", action="store_true", help="Drain at most one already queued task")
    args = parser.parse_args()
    try:
        require_linux_worker()
    except ValueError as exc:
        parser.error(str(exc))
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    if not enabled():
        raise SystemExit("Set CANVASLAB3D_GPU_ENABLED=1 explicitly")
    models = args.models.resolve()
    for operation in args.operations:
        verify(models, operation)
    identity = hardware_probe(args.device)
    print(json.dumps({"preflight":identity, "models_verified":args.operations}), flush=True)
    if args.check:
        return
    store = Store(args.data.resolve()); queue = GPUQueue(store)
    worker_id = uuid.uuid4().hex
    with device_lock(store.root/"gpu-locks", identity["device_key"]) as lock_fd:
        queue.register(worker_id, identity, args.operations)
        try:
            while True:
                task = queue.claim(worker_id)
                if task:
                    try:
                        run_task(queue, worker_id, task, models, args.device, lock_fd)
                    except (ValueError, OSError, subprocess.SubprocessError):
                        pass  # Failure is persisted; the next subprocess starts with clean VRAM.
                if args.once:
                    break
                time.sleep(1)
        finally:
            with store.transaction() as db:
                record = store.get(db,"gpu_worker",worker_id)
                record.update(last_seen=0,active_task=None)
                store.put(db,"gpu_worker",worker_id,record)


if __name__ == "__main__":
    main()
