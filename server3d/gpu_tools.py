"""MCP bindings for optional GPU vision. No torch/model import at server startup."""
from typing import Any
from .gpu_jobs import GPUQueue


def install_gpu_tools(mcp, store, expected):
    queue = GPUQueue(store)

    @mcp.tool(structured_output=True)
    def scene_gpu_status() -> dict[str, Any]:
        """Report enabled state, online device workers and queue; VRAM is never pooled.

        CUDA availability is reported from an actual worker probe, not inferred
        from installed packages. No workers is not successful GPU processing.
        """
        return expected(queue.status)

    @mcp.tool(structured_output=True)
    def submit_scene_gpu_task(job_id: str, source_sha256: str, analysis_revision: int,
                              request: dict, idempotency_key: str) -> dict[str, Any]:
        """Queue depth or prompted segmentation of THIS original and source-analysis revision.

        request={operation:'depth'|'segment', execution:'cuda'|'cpu',
        region_id?:source_region, points?:[{x,y,label:0|1}], depth_input_side?:518|784}.
        CUDA is default; CPU must be explicitly requested. Segment requires a
        region and uses its original-coordinate box; points are ORIGINAL native
        coordinates. Depth is relative inverse depth, NOT meters. Returns a task
        ID immediately, not finished evidence. Poll get_scene_gpu_task, inspect
        prediction artifacts, then revise the scene through normal plan/build/
        capture tools. Never replace measured annotations with predictions.
        """
        return expected(queue.submit, job_id, request, analysis_revision, source_sha256, idempotency_key)

    @mcp.tool(structured_output=True)
    def get_scene_gpu_task(task_id: str) -> dict[str, Any]:
        """Read GPU state, verify successful artifacts, expose source-bound advisory summaries.

        Download paths require the normal API bearer token. A stale prediction
        may be inspected historically but cannot certify current scene fidelity.
        """
        return expected(queue.get, task_id)

    @mcp.tool(structured_output=True)
    def cancel_scene_gpu_task(task_id: str) -> dict[str, Any]:
        """Cancel queued/running inference; worker terminates its child on next heartbeat.

        Old completion tokens cannot publish after cancellation. Existing source,
        scene plans, components, builds and previously completed tasks remain.
        """
        return expected(queue.cancel, task_id)
