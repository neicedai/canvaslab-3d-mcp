# CanvasLab MCP 图生网页链路问题报告

**日期**：2026-09-10  
**状态**：开放，拆图质量仍未达到网页还原要求  
**影响范围**：`submit_ui_split` → `get_ui_build_handoff` → reference web 还原链路

## 版本核对

- 本地 `main`：`211a060ab9aa4db1925b1a0c0b93b2d217d004b4`
- GitHub `origin/main`：同一提交（已核对）
- 线上 LXC 102 的 `canvaslab-mcp.service`：已部署并重启
- 在线 MCP 已返回新版字段：`graphicLayers`、`automaticGraphicLayers`、`semanticLayers`、`selectionStats`、`discardedGraphicFragments`
- 发布前后服务端测试：91 个全部通过

结论：本报告中的失败结果来自最新 MCP，不是旧版本缓存造成的。

## 复现方法

1. 使用 imagegen 生成原生 1536×1024 的 Web UI 设计稿。
2. 使用原图调用 `node scripts/split-image.mjs <image-path>`，不使用缩略图。
3. 等待 `get_split_job` 完成后调用 `get_ui_build_handoff`。
4. 检查 `split_quality.requiresResplit`、背景补绘比例、文字保留率和图形层统计。

## 实测结果

验证线程连续生成了多张 UI 设计稿，全部被新版质量门拦截：

| job_id | 背景补绘面积 | OCR 检出 | 图层总数 | 自动图形层 | 语义层 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| `12f0fcb12dba462d99be7ca8372a520c` | 50.00% | 0 | 60 | 54 | 6 | `requires_resplit=true` |
| `004c176f63b142a6a7efd54ebcd2d0f0` | 63.20% | 18 | 71 | 46 | 7 | `requires_resplit=true` |
| `01c7e99daa8a4a4180bfbecd72ad4886` | 80.00% | 16 | 29 | 8 | 5 | `requires_resplit=true` |
| `4ce872069ae24d9ba100a4ecc352a1c8` | 59.00% | 17 | 76 | 50 | 9 | `requires_resplit=true` |
| `9c60a59a5db4483a88cf5128314caae0` | **66.63%** | 24 | 82 | 48 | 10 | `requires_resplit=true` |

最新设计稿：`design/9c60a59a5db4483a88cf5128314caae0/source.png`。该图本身是完整的森林故事会页面，但拆图后的 `background.png` 已有大面积被抹除和 Telea 补绘的区域，因此没有继续生成网页，也没有进行视觉审计。

最新任务的详细统计：

- `textRetentionRatio = 1.0`（24/24）
- `graphicLayers = 58`
- `automaticGraphicLayers = 48`
- `semanticLayers = 10`
- `discardedGraphicFragments = 97`
- `discardedGraphicBudget = 233`
- `automatic_min_area = 472 px²`
- 自动候选 322 个，最终自动预算 64 个

## 问题判断

### 1. 复杂插画被拆成大量大范围 mask

新版已经解决“小碎片按面积升序吃掉预算”的问题，但 SAM 自动 mask 仍会把树屋、人物、草地、书本和纹理拆成许多相互覆盖的图形区域。每个 mask 单独未必超过 50% 画布，因此不会触发单 mask oversized 过滤；多个 mask 合计后却占用了约 50%～80% 画布，背景重建因此大面积补绘。

### 2. 质量门正确拦截，但当前没有可用的修正路径

`requires_resplit=true` 的判定是正确的：拿这种背景进入 reference web 会把插画细节变成模糊补绘，网页截图无法代表原设计。但当前 handoff 只返回失败结果，没有自动把这些区域收敛为可用的 atomic illustration/crop region，也没有给出可直接修正的分割策略。

### 3. 图形预算统计已经暴露问题

最新任务有 322 个自动候选，其中 233 个因预算丢弃、97 个因碎片阈值丢弃。说明过滤和统计已经生效；剩下的核心问题不是“预算没有应用”，而是进入预算前的候选仍然过多且包含大范围纹理 mask。

### 4. OCR 不是当前瓶颈

五次测试的文字保留率均为 1.0 或接近完整。文字可以继续交给 HTML 实现；当前主要失败来自插画/背景分割。

## 用户影响

- 不能把 `get_ui_build_handoff` 返回当成可实现素材。
- 如果强行跳过质量门，生成的网页会出现大块模糊补绘、插画缺口和错误透明区域。
- 目前还没有可信的 reference web 页面，因此不能进行有意义的像素审计或完成认证。

## 建议修复顺序

### P0：控制插画区域的总占用

- 在 `_select_masks` 后增加 aggregate occupancy 检查，而不是只按单个 bbox 面积过滤。
- 对连续的大型插画区域优先保留一个 atomic semantic mask 或 `crop_region`，禁止其内部自动 mask 继续打洞。
- 背景重建遇到大型插画区域时，优先保留原始 artwork composite，不能对 50% 以上区域直接 Telea。

### P1：增加稳定的容器/区域检测

- 用 OpenCV 矩形、圆角矩形和边界连通性补充 DINO，识别卡片、书封、侧栏和 banner。
- 将“容器”和“插画内容”分开评分；容器内部的纹理 mask 不应自动升级为独立页面层。
- 在 handoff 中返回每个 region 的占用率、最大 mask 面积和建议 asset strategy。

### P1：让失败结果可操作

- `requires_resplit=true` 时返回明确的失败原因和建议配置，而不是让 agent 自由猜测。
- 增加按 region 的重拆工具或 crop 工具，让 agent 可以只重拆 hero、卡片、侧栏等局部区域。

### P2：建立图生网页回归基准

- 至少保留 5 张生成设计稿作为固定回归样本。
- 验收同时要求：背景补绘比例、图形层占用率、文字保留率、`requires_resplit` 和实际截图差异。
- 禁止仅凭全局 pixel similarity 或流程状态签发完成证书。

## 建议验收标准

对于新的 1536×1024 UI 设计稿，至少满足：

- `requires_resplit=false`
- `automaticHoleRatio ≤ 0.20`（质量门；只统计自动 SAM mask 造成的空洞）
- `backgroundHoleRatio` 继续作为总占用诊断值，不作为单独的失败条件；它包含有意提取的 OCR/语义组件
- OCR 文字保留率 `≥ 0.90`
- 不存在单个页面级 mask，且大型插画以 atomic/crop region 表示
- 能完成 `reconstruction-plan.json`、`generate_reference_web`、桌面截图审计和移动端/控件验证

## 2026-09-10 后续修复与线上回放

针对上述 P0，提交 `183d339` 增加了自动 mask 的 solidity/rectangularity/几何基元门控、自动图层累计占用上限（画布 20%）、按来源计算的 `automaticHoleRatio` 质量门，以及 `resplitReasons`。语义图层和 OCR 图层不占自动图形预算；被安全丢弃的 oversized/scenery 候选只计入诊断，不会把流程永久卡在重拆状态。

代码已推送到 GitHub `main`，并通过事务发布到 LXC 102。线上 5 张固定原图真实上传回放如下（不是仅用最终图层做的下界估计）：

| 线上回放 job | 总背景占用 | 自动空洞率 | OCR 保留率 | 语义层 | 重拆 |
|---|---:|---:|---:|---:|---|
| `ac027f836da44158b33115f27e160e28` | 25.81% | 19.74% | 无 OCR | 6 | 否 |
| `1d32a15e1913447da68e333469dbbd14` | 31.93% | 15.50% | 100% | 7 | 否 |
| `f3a69251b89c4c63a7489341d8455d80` | 63.74% | 14.25% | 100% | 5 | 否 |
| `9367c2f0a219459ca52cb72c8d16e0b3` | 31.96% | 12.83% | 100% | 9 | 否 |
| `98c74edefe68408e81f2196de9f630b4` | 39.37% | 15.20% | 100% | 10 | 否 |

线上 MCP 返回 18 个工具、两张 Tesla P4，API/MCP/nginx 均为 active。后续仍需对这些拆图产物继续做 reference web 的局部视觉审计；“拆图质量门通过”不等于网页已经像素级还原。
