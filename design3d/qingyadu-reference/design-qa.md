# 青崖渡：原图驱动三维还原复验（2026-09-13 当前轮）

## Final result: blocked — high-fidelity acceptance

交互预览可用：[青崖渡](http://127.0.0.1:5190/)。本轮使用按图还原与视觉 QA 流程，修改独立 3D MCP、受控 Blender 配方及共用 Three.js 运行时；未修改二维 MCP，未部署远程服务器，未提交或推送 Git。下方历史章节不是当前验收结果。

原图原生 1536×1024，字节未缩小或替换。SHA256：`bfb9030897d4c18b22aa3fe5fa7eb088fd8766e96e1494487db3eaf74fb406d1`。

- 原图：`C:/Users/neo/AppData/Local/Temp/codex-clipboard-5db5fc6a-4f09-4843-9aa3-e38130e2ca83.png`
- Job：`8593e4aa0c5e4b288dcbd672c396161f`
- Build：`2e95cd4eb620a7269b7929eeeef24c93ad97066818eaf35400cb85f6bf4c265f`
- Capture：`0da6f10515944739a3d5ae00e028248c`
- Audit：`b199578705cc406ba825443aa9da5041`
- Runtime JS：`685543181b4dc2fcc6b400978a33332d7c8c7a593e2917da181426dffc382ecd`
- Blender worker：`7fccc93152d441d9b7ede904e22151408f902c2688b5f471b9d0324990fbb4c7`
- [原生界面](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/reference-ui.png)、[无 UI 诊断参考](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/reference.png)
- [1.6 倍近景](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/closeup.png)、[左侧](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/left.png)、[右侧](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/right.png)
- [暮色](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/dusk.png)、[手机](../../.canvaslab3d/captures/0da6f10515944739a3d5ae00e028248c/mobile.png)

### Source versus implementation / workflow

本轮内部做了三轮完整组合和截图，不以增加细分档位作为完成标准。第一轮相机与地面校正后，发现屋顶占比过大、门楼方向错误、茶棚混入了竹架区域。第二轮改屋顶与上下层结构、单独建竹架和篮筐、改善窗光及水面；截图仍发现顶部留白、入口浅色门楣和茶字问题。第三轮校正主屋／后树位置高度，入口改深木门楣，重算非均匀缩放后的茶字坡面角度，减少前沿盆栽重复体量。

原图与组合图按原生大小查看，另检查近景、两侧和暮色。新增 `reference-ui.png` 在隐藏 UI 前捕获，进入同一摘要和签名；`reference.png` 仍为独立的无 UI 几何审计图，二者不混作同一像素证据。可见浏览器已刷新为当前 build，实际验证暮色、透视及复位回日景／等轴。

已纠正：水面与建筑分别校准；门楼独立方向；主屋上层收窄后退、屋面缩深；入口木构和窗面暖光；茶棚与竹架分离；竹篮替换误认木箱；倒影不再被水体底面遮掉；茶字随缩放后棚面倾斜。实体瓦片、船、码头、窗格及竹编可旋转，不是原图平面。

### Remaining fidelity blockers

| 优先级 | 区域 | 仍存在的可见差异 |
| --- | --- | --- |
| P1 | 建筑、石岸 | 屋顶与窗洞不逐处一致；石块太均匀，后平台暴露，缺少岩石和自然种植岸线。 |
| P1 | 植被 | 大叶／花簇重复感明显；枝冠、芦苇、攀援和右后方种植密度未准确建出。 |
| P1 | 水与氛围 | 水面偏风格化，不是源图折射／焦散和暖光层次；纸纹、水墨远山及远景船缺失。 |
| P2 | 茶、船、文字 | 茶棚深度／布褶较简单，茶字是实体牌面而非布上墨迹；船体及招牌书法近似。 |
| P2 | 侧视与窄屏 | 左侧诊断图顶部树冠裁切；手机保持完整横向场景，上下留白大。 |

25 个物件加整体共 26 条模型发现已实际提交 MCP，文件 `benchmarks3d/qingyadu-fidelity-v3-findings.json`。`requires_revision=true`、`workflow_complete=false`，没有完成证书或虚构还原百分比。原图独立可见轮廓未标注，IoU 为 null。水面相机拟合只有一个直接可见角点，另三点含遮挡估计及相关补点，低 RMS 不是还原率。

### Verification evidence and required fidelity surfaces

- Python **127/127**，Node **24/24**；最终真实 Chrome **22/22**。
- 原生 1536×1024，桌面 1440×900，窄屏 390×844，横屏 844×390；正面、近景、侧视、暮色与交互均检查。
- 身份、下载字节、实例隔离、诊断恢复、移动／取消拖动、旋转、日夜、投影、动画、复位、窄屏无溢出及横屏控件通过。
- 无运行错误，保留一组驱动 PMREM 浮点精度编译警告。
- 参考场景 891,636 三角形、271 draw calls，另有 2 三角形阴影辅助面。动态短样本 60 帧，中位 18.2ms、P95 19ms；不是手机真机或正式性能认证。
- 十五种受控组件模板；新竹架／篮筐 detail 1/2/3 均实际构建并通过 GLB 检查。最终客栈 233,132 三角形，入口小修前后包围盒精确相同。
- 图片与分析一起替换也无法绕过基础 build 原图 SHA；旧 job/build 不覆盖，未开放任意 Python 或远程模型执行。

本机字体、隐藏面、深度及未见结构仍为近似／推断。当前交付是改善后的可交互预览，用户要求的“无限接近原图”仍未达成；剩下的是原图专属形状、材质和氛围制作，不是再把同一模板调高细分。

---

# 历史记录：初版三维效果预览（以下非当前验收）

## Final result: blocked (high-fidelity acceptance), interactive preview available

本轮是独立 3D MCP，不覆盖二维 MCP。网页以 Blender 实体组件 + Three.js 生成，不以整张原图作为场景贴图。隐蔽面和深度是推断，不是真实恢复。

原图：`C:/Users/neo/AppData/Local/Temp/codex-clipboard-5db5fc6a-4f09-4843-9aa3-e38130e2ca83.png`，原生 1536×1024，未缩小上传。

- Job: `4d6c03fe3fc54799a601d7574cd9d382`
- Build: `99aa4e1681311c1ddb5f9ddaa7bba115570ed76916a60cf14ef39d65ad4de973`
- Capture: `ead0a6a63c3746fe94ee8324bda763c4`
- Audit: `a82c0d4a0b774c5ab51401059da6f7d0`
- Preview: http://127.0.0.1:5190/
- Captures: `.canvaslab3d/captures/ead0a6a63c3746fe94ee8324bda763c4/`

## Visual review

在同一轮视觉输入中对照原图与原生尺寸 `reference.png`，并检查 `left.png`、`right.png`、`desktop.png`、`mobile.png`。参考捕获隐藏 HTML 操作栏用于几何审计；界面单独用 desktop 和可见浏览器截图检查，不把两种截图混为像素一致性证明。

三轮迭代：第一轮新增客栈、石岸、茶摊、树木、乌篷船等组件；第二轮增加树冠密度、水面纹理和投影切换；第三轮缩小客栈、调整船与相机、消除树冠裁切和水体侧面条纹，修复暮色文字对比。

仍未通过的差异：

- P1 建筑：缺少原图完整歇山屋顶、各自独立的门廊屋檐、侧墙客栈招牌及丰富室内陈设；目前立面更平直。
- P1 材质与植被：木石缺少老化纹理，树叶仍较粗；背景水墨远山缺失。没有原图的丰富自然光照层次。
- P1 水面：有实体水体、荷叶与程序纹理，但缺少可信折射、倒影和柔和焦散；纹理仍像细密刻线。
- P1 茶摊：棚顶字被遮挡，缺少竹架、挂帘和编织器具。
- P2 比例：石岸过于整齐、临水面积偏浅；船体与花木形状仍为近似，原图手写字体也非完全一致。
- P2 暮色：控件和标题可读，建筑灯笼尚无原图式暖光照明。

没有给出未经测量的“还原百分比”。23 条逐物件与整体发现实际提交 MCP，详见 `benchmarks3d/qingyadu-reference-findings.json`。MCP 返回 `requires_revision=true`、`workflow_complete=false`，没有完成证书。

## Functional evidence

真实 Chrome 20 项检查全部通过，无控制台错误／警告；桌面 1440×900、手机视口 390×844、横屏 844×390、参考 1536×1024。包括模型身份／边界／下载字节、实例材质隔离、诊断材质恢复、船移动与复位、旋转惯性复位、投影切换、日夜切换、动画、溢出及操作栏可见性。这不是手机真机性能测试。

可见浏览器已检查最终版本和暮色文字对比。手机展示完整场景而非裁切，因场景很宽，上下留白较多。运行时观察到 228438 render triangles、491 draw calls，不代表已满足生产性能预算。

回归：原有 92 项 Python、17 项 Node 测试通过；新增招牌文字与固定 presentation 验证后，core 38 项通过。新江南组件当前采用固定艺术调色板与细节，不支持按 recipe 颜色自动改色，此限制应在后续组件契约中明确化。

## Scope and handoff

本轮交付可旋转、缩放、切换日夜／投影及漫游的效果预览，而非高保真完成品。建筑、船、石岸都是实体网格；招牌使用声明文字的 CanvasTexture，不是截取原图伪造几何。新代码未提交／推送，也未替换服务器上的二维服务。5190 只服务本次不可变 build 目录，旧 5186 页面保留。
