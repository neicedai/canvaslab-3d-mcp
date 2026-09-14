# 212 上的按需 GPU 部署

此目录记录当前站点：PVE 192.168.101.212 的 LXC 102，容器地址 192.168.101.213。
二维服务仍使用 `/srv/canvaslab`。三维代码、依赖、权重、数据在 `/srv/canvaslab-3d`。

先将当前代码部署到 `/srv/canvaslab-3d/app`，以 root 执行 `install-local-p4.sh`。
脚本安装独立 Python 环境、固定 CUDA 11.8 依赖和校验过的模型，并安装 Node、Chrome、Blender。
首次安装需要下载依赖。完成两卡预检后，执行 `activate-local-p4.sh` 配置独立用户和 systemd。
已有连接应保留 `data/access.token`，不要用新令牌覆盖它。

三个服务为 `canvaslab3d`、`canvaslab3d-gpu@0`、`canvaslab3d-gpu@1`。
MCP 仅监听容器回环地址 8031。Windows 的 `connect-212.ps1` 建立同端口 SSH 转发；
继续使用 Codex 现有 `canvaslab_3d` 连接和令牌。脚本不关闭占用该端口的进程。
Windows 重启后如转发未运行，可再次执行此脚本；服务器服务由 systemd 自动启动。

消费者可常驻 CPU，每单推理使用独立子进程。模型和 CUDA 上下文随子进程结束释放。
显卡忙碌时暂缓领取任务；这不是与二维服务之间的严格共享锁。
可用以下命令验证实际 GPU 生命周期，报告包含两类真实推理结果及显存采样：

```sh
cd /srv/canvaslab-3d/app
/srv/canvaslab-3d/venv/bin/python -m scripts3d.gpu_live_smoke \
  --token-file /srv/canvaslab-3d/data/access.token \
  --output /srv/canvaslab-3d/data/p4-lifecycle-report.json
```

推理是否成功、任务完成后显存是否回落，需要以此实测为准。
部署成功不代表示例图片重建 CI 或原图还原质量全部通过。

## 2026-09-14 实机记录

- Linux 后端 255 项测试通过；Windows 跳过其中 Linux 专用设备锁测试。
- Tesla P4 两卡通过 PyTorch 2.6.0+cu118 实际 CUDA 内核检查。
- 真实分割与深度通过 MCP 排队完成，分别在两张卡运行。任务前后均为 3MiB/3MiB；完成后无 3D CUDA 推理进程。
- 推理器报告分割峰值分配 2,520,597,504 字节，深度峰值分配 193,976,832 字节；这是此合成样例的测量值。
- 服务器 Blender 4.2.0（仓库 CI 固定版本）、Chrome 153.0.8010.36；纹理组件 20/20、原图投影 23/23 浏览器检查通过。
- GPU 报告：`/srv/canvaslab-3d/data/p4-lifecycle-report.json`。
- 浏览器报告：`/srv/canvaslab-3d/data/deployment-projection-check/integration-report.json`。
- 从 Windows 的 8031 转发入口实测 `gpu.enabled=true`、两卡在线、`restart_required=false`。
