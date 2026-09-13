# 原图保真：局部细节诊断与受限候选试验

本模块的目标是更接近输入原图，而不是增加与原图无关的装饰。当前提供可执行的诊断和候选试验流程；不承诺单图恢复全部真实几何，也不提供“还原度百分比”。

## 本轮新增能力

### 1. `measure_scene_fidelity`

输入当前 `job_id` 和 `capture_id`，读取经过校验的原始图像、原图视角截图与 ID 图，返回：

- 原图外轮廓减去孔洞后的可见轮廓 IoU。
- 分区域颜色误差、原生像素误差、边缘误差，以及 1/2/4 像素尺度的高频细节误差。
- 差异较大的局部区域 `worst_patches`；其中 `source_box` 是原图坐标，不是缩放后的截图坐标。

对比不自动平移、配准、改曝光或重着色来降低误差。统计蒙版来自原图标注，不能按生成结果的可见区域删掉困难像素。原图字节保持不变；整数裁剪保持原始像素，明确的非整数裁剪会记录重采样。

缺少原图轮廓、标注置信度低于 0.8、透明图像未明确合成、尺寸不符、检查失败或证据被改动，都不能被当成“通过”。颜色与细节指标也受光照影响，是诊断工具，不是经过校准的感知相似度标准。

### 2. `suggest_component_deformation`

仍由调用方提供“当前组件上的规范化锚点”和“原图里该特征应到达的像素”。新求解器同时计算相互重叠的局部形变，不再把相邻控制点当成互不影响。

估计点的修正幅度只衰减一次；原先偏移与执行强度重复相乘的问题已修正。原图位置已经一致时返回 `no_op=true`，不生成非法的零偏移控制点。固定点可以与待移动点共同参与拟合。

结果中的 `linearized_after_rms_px` 是整个局部形变场执行后、完整网格重新归一化前的预测。最终归一化可能移动其他区域，必须重新生成和捕获，不能把预测当成最终效果。

首版仍限定：正交相机、没有父对象的注册组件、未携带已有形变控制点的基础配方。最多 16 个锚点；不自动识别图像特征，不接受任意脚本或模型导入。

### 3. `compare_scene_candidate`

允许候选与基准属于不同任务，但必须来自同一原图 SHA256，且原图尺寸、裁剪、区域 ID、区域框、关键性、置信度、外轮廓与孔洞一致。说明文字、显示名称和任务版本号不是像素测量，可以不同。

旧基准的签名、构建文件、截图及原图字节重新校验，指标从原始证据重新计算，不直接相信旧审查里缓存的分数。过期截图只允许作为历史基准；当前候选仍必须满足当前截图校验。

关键区域的轮廓、颜色、边缘或细节明显退步时，即使全图平均误差变小，也返回 `rollback_recommended`。只有证据充分且没有超过诊断容差的退步，才可能返回 `prefer_candidate`。这两个值都是供审查使用的建议，不是自动验收或发布命令。

### 4. `refine_scene_component`

该工具将下列步骤串联执行：

原任务当前审查 → 联合形变建议 → 有界强度候选 → 受控 Blender 生成 → 独立子任务 → 场景验证与构建 → 浏览器捕获 → 轮廓和原图细节比较。

输入字段：

| 字段 | 含义 |
| --- | --- |
| `job_id` | 原任务 ID |
| `object_id` | 当前场景的根级注册组件 ID |
| `source_sha256` | 原图 SHA256，不能填重新保存图片的摘要 |
| `landmarks` | 当前组件规范化锚点与原图目标像素的对应关系 |
| `base_revision` | 原任务当前场景计划版本 |
| `baseline_audit_id` | 原任务当前构建的审查 ID |
| `max_candidates` | 整数 1–3，默认 1 |

每个 landmark 使用 `id`、`anchor`、`pixel`、`measurement`、`radius` 和 `evidence`。`anchor` 的 x/z 范围为 [-0.5, 0.5]，y 为 [0, 1]；`pixel` 始终是原始整图坐标，包含裁剪偏移。`measurement` 为 `measured` 或 `estimated`，不能把估计伪装成实测。

候选强度：1 个候选为 0.5；2 个为 0.5、1.0；3 个为 0.35、0.65、1.0。候选只改指定对象的组件资产，保持原图测量、相机和场景其余对象不变。模板的基础几何根本不对时，局部形变不能替代重建。

**原任务永不被此工具覆盖。** 每次候选独立建任务，保留配方、构建、截图和比较结果。失败会记录原因；没有足够改善时返回原版。出现可审查候选时返回 `candidate_available_for_review` 及对应任务/构建 ID，仍需检查原视角、近景和其他角度，然后明确决定采用哪一版。

前提是当前原任务已被审查，截图仍有效，所有关键区域有可靠的原图轮廓，运行时已构建并重启，Blender 和 Chrome 可用。单次最多三个候选，属于昂贵操作；不会在工具返回后隐含继续生成。相同任务同时只能有一个候选试验持有租约。

## 推荐执行顺序

先校准相机与构图，再确认遮挡关系与主要轮廓，之后依据局部误差修正几何、材质和灯光。新计划显式设置 `appearance_mode="reference"`；`render_quality="showcase"` 只指定已有高质量预算，不代表获得原图保真认证。

先调用 `measure_scene_fidelity` 查找需要修正的原图区域；审查对应特征并建立模型锚点与原图像素对应关系，再用 `refine_scene_component` 试验。不要通过更改标注、增加无关细节或反复提高模板 detail 代替修复真实差异。

## 验证范围

本轮选择性验证结果：新增 Python 测试 41 项、既有/更新 Python 回归测试 28 项，共 69 项通过；先前纹理与外观相关 Node 测试 17 项通过；Python 编译检查通过。不是全仓库测试通过声明。

```sh
python -m unittest \
  server3d.tests.test_native_fidelity \
  server3d.tests.test_joint_deformation \
  server3d.tests.test_fidelity_evidence \
  server3d.tests.test_refinement_trials \
  server3d.tests.test_deformation_fit \
  server3d.tests.test_reference_appearance \
  server3d.tests.test_reference_framing \
  server3d.tests.test_embedded_textures -v
node --test runtime3d/tests/embedded-textures.test.mjs runtime3d/tests/reference-appearance.test.mjs
python -m compileall -q server3d
```

测试使用真实 Store/SQLite、不可变构建清单、哈希、签名和合成 PNG，验证跨任务证据、颜色/细节退步、联合形变及保留原版逻辑。生成器和浏览器捕获在候选流程测试里使用替身，因此这些测试不能证明实际 Blender 输出、真实 Three.js 渲染或 MCP SDK 传输已通过。

本轮尚未运行完整 npm 构建、实际 Blender 后处理及真实原图的端到端视觉对照。仍需补齐自动特征对应、原图纹理提取/投影/烘焙，以及真实场景与不同视角验收。已有内嵌纹理通路不等于已经完成原图纹理自动恢复。
