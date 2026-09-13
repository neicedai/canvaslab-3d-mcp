# 原图还原基础改造：第一批

本批是独立分支上的基础能力改造，不是“单图精确重建完成版”。目标是先停止未经授权的美化，并使受控组件能够携带真实表面纹理。现有 Blender 配方仍未自动提取、投影或烘焙原图纹理；也尚未接入图生网格模型、自动局部网格修改或渲染损失优化闭环。

## 1. 显式启用原图优先模式

在新的完整场景计划中加入以下字段，再按原有流程验证和构建。这是计划片段，不是可以直接提交的完整计划：

```json
{
  "appearance_mode": "reference",
  "render_quality": "showcase",
  "reference_lighting": {
    "sky_color": "#ffffff",
    "ground_color": "#808080",
    "hemisphere_intensity": 1.0,
    "sun_color": "#ffffff",
    "sun_intensity": 2.0,
    "sun_position": [-8.0, 14.0, 8.0],
    "sun_target": [0.0, 0.0, 0.0],
    "exposure": 1.0,
    "tone_mapping": "aces"
  }
}
```

这些灯光数值只是起点，不代表从原图恢复出的真实光照。应根据原图另行校准。`tone_mapping` 只允许 `aces` 或 `none`；`none` 不代表不再受到材质和光照影响。

`reference` 模式不调用自动材质改色/程序贴图、展示环境、画面合成、额外水面装饰或展示阴影承接平面。`showcase` 仍控制采样和预算，不再以“高画质”为由重写作者材质与灯光。原有物体、几何、交互、移动、ID/depth 诊断及资产身份机制保持不变。

日间恢复作者配置。暮色保留为明确的推断变体，捕获快照通过 `appearance_mode` 和 `lighting_variant` 区分。场景捕获仍需检查，并不能据此直接认证还原度。

## 2. 旧场景兼容与画幅校准

没有 `appearance_mode` 的计划继续使用 `legacy`，不会升级后突然改变旧场景。`reference_lighting` 只能用于 `reference` 模式，避免参数被静默忽略。

新模式的响应式相机以 `analysis.scene_box` 的宽高比为基准，不再将所有原图都当作 1.45:1。原始画幅下不额外放大垂直跨度；窄视口按比例容纳原有画幅。包围盒拟合与关键点拟合采用同一画幅规则。旧模式及独立拟合函数的默认值仍为 1.45。

旧场景迁移到新模式必须重新校准相机、灯光并重新捕获。不要把旧截图与新模式的测量结果混用。透视相机可显示，但两个建议拟合器依然只支持正交相机。

## 3. 带纹理 GLB 的受控子集

服务端验证器默认仍是旧的无纹理协议。受控组件发布路径显式启用：

```python
validate_glb(payload, profile="standard", texture_profile="embedded-png-v1")
```

新增支持 `TEXCOORD_0`（float32 VEC2）和 PBR `baseColorTexture`，只允许纹理坐标集 0。PNG 必须存放在 GLB 自身的独立 `bufferView` 内。禁止任何 URI、扩展、外部图片、动画、蒙皮、任意模型导入和透明材质；原有几何、索引、三维非共面、归一化和实例预算校验全部保留。

图片采用非交错、8 位 RGB/RGBA PNG。只接受 IHDR、可选 sRGB、IDAT、IEND；生产器需要移除其他元数据并转换格式。RGBA 在 OPAQUE 材质下不提供透明效果。校验包括 CRC、实际有界解压、扫描行长度和滤波类型；拒绝截断、额外数据和解压炸弹。

| 限制 | 数值 |
|---|---:|
| 单张 PNG 编码体积 | 8 MiB |
| 单张图片边长 | 最大 4096 |
| 单组件图片/纹理/采样器各自数量 | 最大 8 |
| 单组件纹理像素预算 | 16,777,216 |
| standard 场景纹理像素预算 | 16,777,216 |
| showcase 场景纹理像素预算 | 33,554,432 |

预算按纹理计费，不只是按图片计费；同一张图片用于不同纹理/采样器可能产生多个 GPU 资源。场景预算按不同组件资产累计。它是受控预算，不是对所有显卡实际显存占用的精确测量或性能保证。

网页先验证组件 SHA-256 与嵌入资源，再由受控 GLTFLoader 插件直接解码 BIN 中的 PNG 字节。全 URL 禁止规则保持不变，不通过允许任意 blob/data/HTTP URL 来绕过加载限制。解码失败必须导致加载失败，不能悄悄换成白色材质；失败时释放已创建的纹理和位图。

**本批只打通验证、发布、预算和加载能力。** 没有新增向 MCP 上传任意模型的接口，也没有让现有模板自动获得原图贴图。下一批需要在受控 worker 中实现原图对应、UV/投影烘焙与来源记录，生成实际带纹理组件后再进行原图对照。原图已有的阴影也不能直接当成纯材质色重复打光。

## 4. 验证记录与上线前检查

本批本地执行通过：

```bash
python -m unittest server3d.tests.test_reference_appearance server3d.tests.test_embedded_textures server3d.tests.test_reference_framing -v
# 24 tests passed

cd runtime3d
node --test tests/reference-appearance.test.mjs tests/embedded-textures.test.mjs
# 17 tests passed
```

新增/修改 Python 文件通过编译检查，JavaScript 源文件通过 `node --check`。另使用实际 Chromium 在桌面 1280×800 和移动尺寸 390×844 下执行离线 PNG 解码与点击检查，2×2 测试图片像素为预期 `[190,70,30,255]`，无控制台错误和网络请求。该检查的 Texture 对象是测试替身，不是完整 Three.js/WebGL 验收。

执行环境没有本项目的 Three.js/esbuild/mcp/Blender 依赖，网络也不能拉取这些依赖；本地 HTTP 导航被浏览器管理员策略阻止，因此仅使用内存中的离线测试页面，没有修改策略。**完整项目回归、`npm run build`、真实 GLTFLoader/WebGL 场景捕获、Blender 输出及原图视觉对照尚未执行。** 单元测试通过不能代替这些检查，也不能换算成还原百分比。

合并前应在项目现有开发环境运行完整测试、构建 runtime，加载实际带纹理组件，并比较原图、原始视角、近景与旋转后的灰模。检查 day/dusk/reset、拖动、ID/depth 图、横竖画幅及失败资源加载。确认后再合并；本分支不自动部署，也不改变现有 master。

重建和重启后调用运行状态工具，核对源码摘要、runtime 摘要及 `restart_required`。重新提交/验证计划并生成新构建 ID，不能手动修改旧的内容寻址构建文件。
