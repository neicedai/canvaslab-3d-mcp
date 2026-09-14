# 两块 Tesla P4：GPU 视觉辅助 V1

## 交付范围

这版让已有 MCP 使用两个独立、可取消、有界的 GPU 任务处理器。不是把两张 8GB 卡合成 16GB，也不是只加未实现的 HTTP 接口。

| 处理器 | 实际实现 | 给还原流程的产物 |
| --- | --- | --- |
| P4 #0 | Depth Anything V2 Small，FP32 | 原生裁剪尺寸的 float32 相对逆深度、16 位预览、分区域深度摘要、边缘图 |
| P4 #1 | SlimSAM uniform-77，FP32 | 根据原图区域框及可选正/负点提示推理的原生尺寸二值蒙版，保留预测孔洞 |

两个模型都有真实 Transformers 调用；源文件、模型版本、权重摘要、实际设备、预处理尺寸和输出文件摘要均记录。边缘图由 CPU 在原生裁剪上计算，不冒充 GPU 语义识别。两个任务可并发，同一物理卡在同一数据目录中最多一个 worker。

**不会把预测自动当成实测标注，也不会自动改变模型。** 深度是相对逆深度，不是米；分割蒙版是预测，置信分数不是经过标定的准确率。原图不缩小、不重新保存替换；模型内部输入会显式缩放，输出插值回原生裁剪尺寸，这并不代表恢复了原图每个像素的真实深度。水面、反射、玻璃、细树枝和遮挡仍需单独审查。

SF3D / TripoSR / TRELLIS、GPU 法线、自动网格生成、UV 烘焙本轮**未启用**。仅凭显存数字不足以保证 P4 算子兼容，不能先把生成接口标为可用。现有 Blender、Three.js、原图表面投影与候选比较路径保持不变。

## 部署要求

首版是同一 Linux 主机/虚拟机内的本地磁盘方案。MCP 和 worker 共用 `CANVASLAB3D_DATA`；SQLite 不要放在 NFS/SMB，也不建议两个宿主机共用此目录。PVE 部署时显卡必须已经直通到运行 worker 的 Linux 虚拟机，宿主机看到显卡不代表容器可用。

Windows 可运行 MCP 及通用队列测试；worker 本身仅支持 Linux，Windows 启动会明确提示。设备锁测试只在 Linux 执行，Windows 回归将其标为 skipped，不代表设备锁或 P4 硬件验证通过。使用 WSL2 时也需要在 Linux 环境内运行 MCP 和 worker，并共用 Linux 本地数据目录。

建议 Python 3.11 独立环境，锁定 PyTorch 2.6.0 + CUDA 11.8 wheels，FP32/eager attention；禁用 Flash/高效 SDPA，不要求 xformers、不调用 BF16，不自动量化。启动先检查实际 CUDA 内核和设备 UUID；P4 需要 Pascal 编译目标。失败立即报错，不回退 CPU 冒充 GPU 成功。CUDA 驱动与 NVIDIA Container Toolkit 由服务器管理员配置，本补丁不修改驱动。

P4 可使用的 PyTorch wheel 与实际驱动组合仍必须在实机跑 `--check`。80% PyTorch 内存配额不是整卡硬隔离：CUDA 上下文和其他应用仍会占显存；OOM 记录失败、不偷偷缩图或无限重试。一次任务最长 600 秒，等待队列最长 900 秒，最多 64 个待处理/运行任务，累计 256 个任务；历史失败工作目录保留供诊断。

## 推荐：两个独立终端（不更改原 MCP 环境）

在仓库根目录，使用与现有 MCP 相同的系统账户：

```sh
python3.11 -m venv .venv-p4
.venv-p4/bin/python -m pip install -r gpu3d/requirements-p4.txt
.venv-p4/bin/python -m gpu3d.models download --models "$PWD/.canvaslab3d-gpu-models"
export CANVASLAB3D_GPU_ENABLED=1
# 用实际的已有数据目录替换此值，MCP 也必须使用该目录并开启 GPU_ENABLED。
export CANVASLAB3D_DATA="$PWD/.canvaslab3d"
.venv-p4/bin/python -m gpu3d.worker --data "$CANVASLAB3D_DATA" --models "$PWD/.canvaslab3d-gpu-models" --device cuda:0 --operations depth --check
.venv-p4/bin/python -m gpu3d.worker --data "$CANVASLAB3D_DATA" --models "$PWD/.canvaslab3d-gpu-models" --device cuda:1 --operations segment --check
```

两个 `--check` 都通过，再分别在两个终端执行相同命令但去掉 `--check`。这是实际排队消费者，不是打印配置后退出的占位程序。可以把任意卡的 `--operations` 改为 `depth segment` 进行动态分工；仍然一张卡一次一个任务。

启动/重启现有 MCP 时也设置 `CANVASLAB3D_GPU_ENABLED=1`。不启用时旧功能仍可使用，主 MCP 进程不需要安装 torch。不要覆盖现有 access token，不要删除 `.canvaslab3d`。原有鉴权、模型资产验证、不可变构建和审查要求全部保留。

## 可选 Docker Compose

```sh
docker compose -f compose.p4.yml --profile setup run --build --rm prepare
docker compose -f compose.p4.yml up -d p4-depth p4-segment
docker compose -f compose.p4.yml logs --tail=100 p4-depth p4-segment
```

`compose.p4.yml` 将宿主设备 0、1 分别暴露给两个容器；每个容器内部都是 `cuda:0`，但按真实 UUID 加锁，不把它们误认为同一张卡。已有 MCP 不被此配置重建或启动，仍需用同一个实际数据目录并开启 GPU 开关。默认使用仓库下 `.canvaslab3d`，可以通过变量指向已有目录。运行中容器没有网络、无新增监听端口，模型目录只读。只有显式 prepare 阶段联网下载固定公开模型。Docker 构建、驱动和 P4 运行需在目标服务器验证。

## MCP 使用流程

先调用 `scene_gpu_status` 查看 `enabled` 和 `workers[].online`。状态中的 CPU worker 不是 GPU worker。原图上传、建立任务并保存原图分析后：

```json
{
  "job_id": "当前任务ID",
  "source_sha256": "原始图片真实SHA256",
  "analysis_revision": 1,
  "request": {"operation": "depth"},
  "idempotency_key": "depth-first-pass"
}
```

以上是 `submit_scene_gpu_task` 的示例；不要照抄中文占位值。它返回 `task_id` 与 `queued`，不是已完成。用 `get_scene_gpu_task(task_id)` 查询；完成后包含区域深度摘要及受既有 Bearer 鉴权保护的下载路径。分割请求使用：

```json
{"operation":"segment","region_id":"building","points":[{"x":350.0,"y":250.0,"label":1}]}
```

`region_id` 是当前原图分析中的区域ID，点坐标是原始整图坐标，包含裁剪偏移。负点 `label=0` 可排除误选区域。无需自由文本自动分割，也不接受 URL、文件路径、脚本、任意模型名或任意 checkpoint。支持显式 `execution:"cpu"` 用于测试，默认是 `cuda`，两者分别调度，不互相冒充。

拿到结果后，审查原图/深度/分割：用它们辅助确定前后层次、物体轮廓与局部遮挡，再调用已有 `validate_scene_plan → build_threejs_scene → capture_scene_views → measure_scene_fidelity`。需要保留预测与实测的区别；禁止直接覆写源标注提高验收分数。已经比较过的模型仍通过原有流程迭代。

`cancel_scene_gpu_task` 取消后，worker 下一次心跳会终止推理子进程，旧结果不可发布。worker 丢失或超时会失败，而不是偷偷重复执行同一任务；重试要用新幂等键。更新原图分析版本会使旧预测变为历史参考，不覆盖新计划。MCP 返回不代表后台一定有卡在处理，必须检查 worker 在线和任务终态。

## 验证口径

`server3d/tests/test_gpu_queue.py` 用真实 SQLite 和合成数组验证队列、双 worker 互斥、取消、超时、哈希和版本保护；其设备对象是测试替身，**不是 P4 测试**。

`.github/workflows/validate-p4-workers.yml` 另外安装真实 CPU PyTorch / Transformers，下载并核对两个固定官方权重，通过真实 MCP Client 提交任务、推理子进程处理、读取原生输出，报告明确标为 `cuda_tested:false`、`p4_hardware_tested:false`。这个测试验证模型适配和接线，不证明 CUDA/显存/速度。结果以对应提交 Actions 为准。

## 固定模型与官方参考

- P4 compute capability 6.1：https://developer.nvidia.com/cuda/gpus/legacy
- 官方 cu118 历史安装命令：https://docs.pytorch.org/get-started/previous-versions/
- 深度模型：https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf
- 分割模型：https://huggingface.co/Zigeng/SlimSAM-uniform-77

模型 registry 在 `gpu3d/models.py` 固定完整 commit SHA 和权重 SHA256；仅允许 safetensors，`trust_remote_code=False`，推理时 `local_files_only=True`。prepare 同时保留模型原始 README 供许可证和限制核查。
