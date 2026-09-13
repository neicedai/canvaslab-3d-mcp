# CanvasLab 3D — 0.3.0-dev

独立的 Blender 组件＋程序化 3D MCP 开发版。模型分析原图后，可调用受控 Blender 配方生成可复用 GLB 组件，再通过严格场景配置与程序化组件共同组装 Three.js 工程；使用真实 Chrome 捕获多视角并执行基础交互测试。不是自动图生模型服务，也不是已通过还原验收的完整产品。

二维 `server/`、现有 `/mcp` 和水乡原页面不受此服务影响。开发服务默认仅绑定本机；尚未部署到 LXC、推送 GitHub 或自动注册到 Codex。

## 已实现与未实现

已实现：

- 原图字节原样保存，文件哈希／尺寸检查、认证上传和下载；不会静默缩图。
- 独立 SQLite 状态、提交／构建幂等、分析与场景版本锁、关键区域覆盖和父子结构校验。
- 9 类程序化组件：box、cylinder、sphere、building、roof、boat、tree、dock、water；另有引用已登记 GLB 的 `asset` 类型。禁止任意 JS、shader、URL 和 billboard。
- 4 类固定 Blender 配方：开放门楼、曲面瓦顶、栏杆、货箱；保存静态 GLB、可编辑 `.blend` 和版本／几何元数据。后台生成不要求 AI 推理 GPU。
- SHA256 标识的不可变组件库；同配方／生产者版本复用，验证实际 GLB 几何、所有文件摘要和场景实例预算。运行时不执行调用方模型文件或代码。
- 本地锁版本 Three.js 打包；内容寻址构建、原子发布目录、ZIP 校验、防止引用别的 job。
- 真实 HTML 控件：旋转缩放、暮色、动画暂停／继续、物件指针与键盘移动、全状态复位。
- 受控本地捕获进程：只加载验证过的构建字节、固定相机／DPR／时间、正面与侧面、ID／深度图、桌面／窄屏／横屏证据。
- 原图标注对应的投影包围框诊断、逐对象模型发现、证据摘要和本地签名、过期／重放拦截。
- 17 个真实 MCP 工具，所有返回为结构化内容；失败给出可操作错误。
- 独立无抗锯齿的物件 ID 渲染通道，避免边缘混色误认物件；可见轮廓按区域所含物件的像素并集计算。
- 原图 `visible_polygons` 标注支持多轮廓并集；缺失或置信度不足明确转人工复核，不冒充通过。
- 正交相机的有界参数拟合建议，只改相机、不改物件；输出总体与逐物件误差、退步物件和需重建的完整配置。
- `get_scene_build_handoff` 返回原图、当前分析／计划和最近审计，标记历史审计是否仍匹配当前构建。
- 状态区别“进程实际加载的源码”和“磁盘源码”；不一致返回 `restart_required=true`。

未实现（明确阻断完成认证）：

- 透视相机／可见轮廓驱动的优化、遮挡质量门、校准后的视觉评分与自动修正调度。现有相机建议仅拟合投影包围框，不等于提高造型还原度。
- 注册远程渲染 worker、正式低权限隔离部署、可发布的证书。
- 完整碰撞／支撑面验证、完整触屏／多指与所有异常交互测试。
- 真机性能基准、五个独立标注的还原样本、整场景 GLB 导出、自动图生网格、复杂贴图／骨骼／动画资产。单个受控组件的 GLB 导出与下载已实现。

`workflow_complete` 始终为 false，`complete_scene_review` 返回具体缺失项，不签发假证书。`audit_scene_views` 分开输出 world AABB 投影框和 `visible_quality` 的真实可见像素指标。轮廓 IoU 对照的是原图人工／模型标注，不是物件包围框；0.85 仅是未校准的诊断参考值，没有正式验收效力。当前构建同步执行且有数量预算；取消会阻止后续发布，但正在执行的本地截图会排空或超时。远程队列和可恢复长任务留在后续阶段。

## 本机运行

从仓库根目录执行。Python 3.12 与 Node.js 24 已用于本轮开发验证。

```powershell
python -m venv D:\venvs\canvaslab3d
D:\venvs\canvaslab3d\Scripts\python.exe -m pip install -r server3d/requirements.txt
npm --prefix runtime3d ci
npm --prefix runtime3d run build
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.serve --port 8031
```

开发服务：`http://127.0.0.1:8031/mcp-3d`；健康检查 `/health`。除健康检查外，API 和 MCP 都要求 `Authorization: Bearer <token>`。

`scripts3d.serve` 首次将随机令牌保存在被 Git 忽略的 `.canvaslab3d/access.token`，不会打印令牌。也可通过 `CANVASLAB3D_TOKEN` 提供令牌。此文件和 `.canvaslab3d/capture.key` 都是敏感数据，不能上传 GitHub；生产部署还需要配置实际操作系统访问权限，不能把本机开发设置当作生产隔离。

本轮使用独立环境 `D:\venvs\canvaslab3d`，没有往二维的环境安装依赖。没有修改 Codex 配置；如需连接开发版，使用上述 HTTP 地址和令牌。跨机器部署必须先完成独立服务与来源域名配置，不要直接把本机端口对公网开放。

stdio 入口也可运行：

```powershell
D:\venvs\canvaslab3d\Scripts\python.exe -m server3d.mcp_server
```

stdio 是已获本机权限的进程调用，不走 HTTP 令牌；上传 API 可与它共用相同 `CANVASLAB3D_DATA`，但数据库、资产和密钥目录应只有服务账户可访问。

### Blender 配置

在运行 3D MCP 的机器上安装 Blender 4.2 或更新版本；组件 worker 已用 Blender 4.5.9 LTS 实测。服务在本机查找可执行文件，不会替用户下载程序。可将 `blender` 加入 PATH，或在启动服务前设置可执行文件的绝对路径：

```powershell
$env:CANVASLAB3D_BLENDER = 'C:\实际安装目录\blender.exe'
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.serve --port 8031
```

上面只是路径示例，不要求相同安装目录。环境变量必须传给运行 MCP 的进程；修改配置后重启 **3D 开发服务**。用 `scene_runtime_status` 或 `list_scene_components` 确认 `blender.available=true`，同时检查 `restart_required=false`、Blender 版本与可执行文件／worker 摘要。没有 Blender 时，已有程序化场景能力仍可使用，但生成新 Blender 组件会明确报配置错误。

组件后台通过固定脚本和 `--background --factory-startup --disable-autoexec` 启动，调用方只能提交受限 JSON 配方。它不是通用 Blender 远程控制接口：不接收 Python、shell、任意路径、外部 URL、上传的 `.blend` 或任意 GLB。固定模板减少输入面，但 **不等于操作系统沙箱**；当前仍是有本机权限的开发进程，正式部署前需要低权限账户、隔离 worker 和完整资源限制。

## 正常调用顺序

1. `scene_runtime_status`：确认 `runtime_ready=true`、`restart_required=false`；记录指纹。
2. 用 `scripts3d/upload.mjs` 将本地原图字节上传到 `/assets`，取得 `asset_id`。
3. `submit_scene_reference` 新建任务；`get_scene_contract` 读取本版实际支持的 schema。
4. 模型查看原图，`save_scene_analysis` 保存原生坐标 `scene_box` 和物件框；手机外框等不应当作三维场景。
5. 如需精细组件，先调用 `list_scene_components`／`generate_scene_component`／`get_scene_component`，再把返回的组件 `asset_id` 写入场景。`validate_scene_plan` 保存有效计划，取得 `plan_id`；`build_threejs_scene` 从该 ID 生成新构建。
6. `capture_scene_views` 在本机 Chrome 取证；返回 `capture_id` 与检查结果。截图通过认证的 `/captures/<id>/<filename>` 下载。
7. 模型查看原图和这些截图，给每个物件及 `overall` 写 `object_id/difference/severity/planned_fix`，调用 `audit_scene_views`。
8. 修正配置后重新校验和构建，不能修改已签名构建的某个 JS 后复用旧证据。
9. `export_scene_project` 返回 ZIP 下载路径。可本地打开预览；它仍然是开发原型。

服务器不接收任意网页 URL 进行截图，也没有导入调用方自写 observation 的接口。本地 worker 是受信任开发进程，不是对恶意本机用户的远程证明。性能输出只是短时 headless 采样，不能声称真实手机性能通过。

## Blender 组件调用与复用

`get_scene_contract` 返回 `component_schema` 和 `canvaslab-component-v1` 合同。新增的三个 MCP 工具是：

- `list_scene_components()`：列出受控模板、Blender 状态和已登记组件。
- `generate_scene_component(recipe, idempotency_key)`：后台生成并校验，返回不可变 `asset_id`、配方／生产者版本、实测几何和下载路径。相同配方与生产者版本可复用；重复相同幂等键不会重复生成。
- `get_scene_component(asset_id)`：重新检查登记文件摘要，取得准确来源、自然尺寸与下载路径。文件被改动时拒绝使用，而不是静默更新旧资产。

| `template` | 当前真实几何 |
|---|---|
| `open_gate` | 四根开放式立柱、石础、横梁、斜撑、透空窗棂、曲面瓦顶 |
| `tiled_roof` | 闭合曲面屋顶、瓦垄、瓦缝、檐口和屋脊 |
| `railing` | 分节栏杆、柱帽、交叉斜撑、底座 |
| `cargo_crate` | 六面木板、加固条、斜撑与钉帽 |

配方只允许 `template`、整数 `detail`（1、2 或 3，默认 1）、`primary_color`、`secondary_color`（均为 `#RRGGBB`）。例如：

```json
{
  "recipe": {
    "template": "open_gate",
    "detail": 2,
    "primary_color": "#89623e",
    "secondary_color": "#475543"
  },
  "idempotency_key": "water-town-open-gate-v1"
}
```

把工具返回的完整 SHA256 `asset_id` 用于新场景对象：`kind: "asset"`，保留 `id`、`region_ids`、`position`、`dimensions`、`material_id` 和 `inferred_surfaces` 等必需字段。不要把图片上传得到的 `asset_id` 当成组件 ID；只有通过组件库登记和验证的 ID 可用。

GLB 顶点统一为底部中心原点、Y 向上、正面 +Z，边界为 X/Z `[-0.5, 0.5]`、Y `[0, 1]`。`dimensions` 按 `[宽, 高, 深]` 放大组件；返回的 `natural_dimensions` 是归一化前的比例，可用于保留原始长宽高关系，不代表从图片恢复了真实米制尺寸。材质采用组件内已创作的、不透明 PBR 颜色与粗糙度，不由场景的通用材质偷偷替换。

同一组件可以在多个任务或同一场景中多次引用；每个实例拥有独立物件 ID、变换和交互状态，构建只打包一份相同 GLB。目前只允许 `cargo_crate` 资产启用移动；这仍是中心点运动边界，不是完整碰撞检测。给程序化 `building` 添加瓦顶资产子节点时可设 `roof_enabled: false`，避免双层屋顶；子节点的 `parent_id`、局部坐标和原图 `region_ids` 必须匹配所属建筑。

`downloads.glb` 与 `downloads.blend` 分别指向认证下载地址 `/components/<asset_id>/glb`、`/components/<asset_id>/blend`。GLB 供网页加载，`.blend` 保留 Blender 原生可编辑网格和材质；二者不是同一种坐标文件格式。下载后可以在 Blender 编辑，但本版不支持将手动修改的 `.blend` 回传自动登记。修改受控配方、重新生成新资产，再版本化构建，不能就地覆盖已登记文件。

轻量／精细档组件的 GLB 验证限制为 100,000 三角面、16 MiB，`.blend` 为 64 MiB；模板还执行各自更低的生成预算。`detail: 3` 展示档使用明确的独立上限：GLB 300,000 三角面、1,000,000 顶点、64 MiB，`.blend` 128 MiB；当前 worker 再限制客栈 250,000、其他模板 180,000 三角面。库仍最多 128 个登记组件。场景 `render_quality: "standard"`（默认）仍按实例累计 250,000 三角面／去重下载 64 MiB；显式 `"showcase"` 为 1,500,000／128 MiB。不能用重复实例绕开面数计费。所有档位都保留静态 GLB、正常法线、材质与资源引用验证。输出先进入暂存目录，超时／失败不发布；这些预算不是生产级磁盘配额或进程隔离的替代品。

`export_scene_project(format="zip")` 打包网页运行时、组件 GLB 和来源清单。**尚不支持把整个场景导出为一个 GLB 或 `.blend`**；单组件 `.blend` 使用独立下载，不混入公开网页运行目录。

## 工程样例

### 相机建议与轮廓审计

`get_scene_build_handoff(job_id)` 取得准确的当前计划、分析和版本号。
`suggest_scene_camera(job_id, capture_id)` 仅返回建议，不修改任务。要求同任务当前构建的未过期可信捕获，至少三个分布充分、置信度 ≥ 0.6 的独立对象；Python 投影先与真实 Three.js 捕获核对，防止坐标或相机模型不一致。建议会列出 `regressed_object_ids`，不能只看总误差下降就盲目采用。

```powershell
# 默认只看建议；--apply 才创建新配置、新构建和新截图，绝不改旧工程文件。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.fit_camera <job_id> <capture_id>
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.fit_camera <job_id> <capture_id> --apply
```

原图分析的 region 可增加 `visible_polygons: [[[x,y], ...], ...]`，坐标为原图原生像素，且在该 region.box 内。不重复闭合端点；不接受自交、退化多边形；本版支持多个简单多边形的并集，暂不支持孔洞。必须先查看原图进行标注，不能拿生成图的轮廓充当原图真值。置信度 < 0.8 不输出 IoU；未标注明确返回 `missing_original_visible_silhouette`。

更新分析会使旧计划／构建失效。重新校验、构建、捕获后再审计。旧版带抗锯齿的 ID 图仅可保留作历史证据，不用于新轮廓指标。审计检查构建文件和捕获签名／哈希／时效，记录前一次审计 ID 与标注版本；最终完成状态仍为 false。

```powershell
# 原始水乡图：含手机界面的原始图片，字节不变，仅测量场景裁剪框。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.demo <原始水乡图片路径> --capture

# 第二套布局用于验证组件复用；并非该输入图的高保真还原。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.demo <另一张参考图路径> --variant pavilion --source-layout unmeasured --capture
```

水乡样例包含 12 个语义对象；庭院样例包含 5 个对象。两个样例共用同一个运行时，不复制或修改每个网页的 JS。`benchmarks3d/scenes.py` 的分析框是粗标注／测试占位，不能作为正式验收真值。

`benchmarks3d/blender_scene.py` 提供第三个工程组合：同一水乡原图、原来的 12 个参考区域，构成 20 个场景对象。它把门楼、货箱替换成 Blender 资产，增加客栈／商铺瓦顶、分段栏杆及少量码头货物，其余建筑主体、树和船继续程序化生成。新增细节只借用原有宽泛参考区域，没有声称获得了独立精确的物件标注；船帆、台阶、招牌、相机和轮廓仍需返修验证。

生成目录在 `.canvaslab3d/builds/<job_id>/<build_id>/`。预览时只服务该目录，不把整个项目、状态数据库或密钥目录作为网站根目录：

```powershell
D:\venvs\canvaslab3d\Scripts\python.exe -m http.server 5186 --bind 127.0.0.1 --directory <具体build目录>
```

## 验证命令

0.3 的 Blender worker 已在真实 Blender 4.5.9 LTS 上生成全部四种模板及 detail 2 门楼：三角面分别为开放门楼 detail 1／2 的 6,384／9,528、瓦顶 4,228、栏杆 660、货箱 1,984。全部实际 GLB 通过严格二进制／归一化几何检查；相同 detail 1 门楼在两次独立进程生成的 GLB 摘要完全相同。这些是组件工程证据，不是本轮浏览器或原图还原验收结果。

```powershell
D:\venvs\canvaslab3d\Scripts\python.exe -m unittest discover -s server3d/tests -v
npm --prefix runtime3d test
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.verify_http <原始水乡图片路径> --capture

# 先配置 Blender 并启动当前 3D MCP。实际通过 HTTP MCP 生成／复用四类组件，
# 校验 GLB 和 .blend 下载，按原图配置组装、检查 ZIP；--capture 才执行浏览器取证。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.verify_blender <原始水乡图片路径> --capture

# 非默认端口／数据目录时明确指定；不要把令牌直接写进命令行。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.verify_blender <原始水乡图片路径> --url http://127.0.0.1:8031 --token-file <access.token路径> --capture
```

HTTP smoke 脚本读取本地令牌，不回显，实际调用上传、MCP 分析／构建／截图／下载／完成阻断。测试中的合成图片仅用于契约与安全回归，不计算还原度。

### 0.3 本轮实测（功能通过，还原验收未通过）

2026-09-13：92 项 Python 测试、17 项 Node 几何／GLB 测试、107 项旧二维回归通过。使用真实 HTTP MCP 首次生成四类 Blender 组件，再创建第二个任务复用全部四个资产；重复键与跨请求复用、注册查询、GLB／`.blend` 下载摘要、构建重试与 ZIP 内容均已验证。

最终任务 `cd2468ba6aec430ba7b8e48502a1ffa3` 的构建 `b23d78bb021c91b52eef564e8eea04294b044fc5d5c4daa6c9a3e2e99672c7d3` 包含 20 个语义对象，其中 10 个是 Blender 资产实例。可信本地捕获 `35c17b6be2d3483ca64e0151605e19ed` 的 18 项 Chrome 检查全部通过：桌面 1440×900、窄屏 390×844、横屏 844×390；模型身份／边界／网络字节、实例材质隔离、诊断后材质恢复、货箱移动复位以及原有交互均检查通过，控制台无错误或警告。实际显示约 6.15 万三角形、392 次 draw call；不代表真实手机性能合格。

模型已对原图及该构建的正面／侧面／桌面／窄屏画面复核，向 MCP 提交 21 条逐物件及整体发现，审计 ID `e92eabfba3cf475bb77b09d0d05c428c`。船帆、招牌、台阶、树木、建筑立面、瓦片比例和相机仍有差距，12 个原图区域尚缺独立可见轮廓；`requires_revision=true`、`workflow_complete=false`，未签发完成证书。不能把本轮功能验证说成完整高保真还原。

### 历史验证（不作为本轮验收证据）

0.2 开发轮：47 项 Python 测试、10 项几何测试和 107 项二维回归测试通过。通过真实 HTTP MCP 创建任务、给出相机建议、版本化重建并重新捕获；前后各 13 项 Chrome 检查通过，包括新增“旋转复位后无惯性漂移”。原图样例的框拟合损失从 0.00489 降至 0.00334，但 8 个对象至少一项框指标退步；这是优化器诊断，不是还原率提升。12 个原图区域尚无独立轮廓标注，IoU 明确为 null，审计结果仍需要修改。

0.1 开发轮：26 项 Python 契约／安全测试、10 项 Node 几何测试通过；水乡与庭院两个工程样例各通过 12 项真实 Chrome 检查（桌面 1440×900、窄屏 390×844、横屏 844×390），控制台无错误或警告。旋转、指针拖动物件、键盘移动、暮色、动画暂停与复位均检查实际状态变化。它们是功能测试，不是高保真验收或真机性能结论。

水乡开发构建 `1c781525c7356b70a1963033dc54269d4058da9e37a88277b3e65bcac79eef8f` 的原图和多视角截图已进行模型复核；门楼结构、栏杆、船帆、材质和相机等仍有明显差异。`benchmarks3d/water-town-v0.1-findings.json` 记录本次发现，不可直接用于审核别的构建。经 `scripts3d.review` 实际提交审计，结果为 `requires_revision=true`、`workflow_complete=false`。

## 下一阶段

### 新原图青崖渡效果预览

#### 建模精细档

`generate_scene_component` 的 `recipe.detail` 为 `1`（默认轻量档）、`2`（精细档）或 `3`（近景展示档）。江南系列的精细档实际影响几何：曲面平滑法线、瓦片弧面加密、船篷弧线从 12 段到 24 段、船身插值增加截面、实体木石边缘小倒角，树木和盆栽改用独立闭合薄叶片／花瓣簇。并非把建筑全部磨圆，细窗框仍保留硬边。

新的组件先经实际 GLB 法线／几何／文件验证，才以新资产 ID 进入场景。`detail: 2` worker 上限仍为客栈 90,000、其他组件 45,000 三角形；默认场景预算不变。展示档的更高预算必须由 `render_quality: "showcase"` 显式声明并进入构建摘要。重复树木／盆栽仍重复计费。招牌实际为盒体 12 + 文字平面 2 个三角形，预算由两处共用计算并有实际运行时几何回归。

展示档不是只改细分数字：固定配方增加客栈通透立面／室内结构、屋顶侧坡、阳台支架、细窗格、密编船篷、手工石块和植物叶片／花瓣。`detail: 3` 与 `render_quality: "showcase"` 只是近景制作的起点，不是还原目标或质量上限。形状、朝向或组件类型不匹配时，必须校正构图与配方，不能用更高面数代替原图对比。

可保持原构图，仅替换组件精细度并新建不可变任务：

```powershell
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.refine_build <已验证build目录> <匹配该任务的原图> --detail 2 --capture

# 选用近景建模与展示渲染配置；仍创建新任务，不覆盖旧成果。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.refine_build <已验证build目录> <匹配该任务的原图> --detail 3 --render-quality showcase --capture
```

脚本先验证原构建摘要及原图 SHA，再通过 MCP 生成／复用组件、校验计划、重建与捕获，不覆盖旧构建。`--detail 3` 未指定渲染档时自动选用 `showcase`；低档仍保留来源场景的渲染配置。可用 `--scene-plan <JSON>` 明确提交完整新配置（例如新模型招牌的实测附着点），仍须由 MCP 对同一原图校验，资产 ID 必须来自基础构建。支持 `--detail 1` 生成轻量对照；不会把未支持的任意模型做自动细分。

展示档渲染使用固定受控木纹／石面／陶瓦／布料材质、几何定位的有上限暖光灯、窗格自发光、局部接触遮蔽、抗锯齿和单张有界平面水面倒影。材质纹理跟随组件移动；停止交互后缓存画面和阴影，移动／昼夜／取消拖动会同步更新。它不是光线追踪、流体模拟或任意外部贴图接口。

取证额外保存固定 1.6 倍近景和暮色。复位检查记录 PNG 精确一致性及解码后的差异；仅此回归检查允许不超过 1% 像素的最大 1/255 色阶 GPU 舍入差，不允许几何位移、明显颜色变化或截图身份不同。源图、构建与所有证据 SHA256 绑定仍严格逐字节；这不是放宽原图还原度验收。性能样本在实际播放动画时采集，不以空闲缓存帧率冒充动态性能。精细化不等于准确隐藏面或高保真完成验收。

另增九种受控 Blender 场景组件（客栈、石岸、茶摊、两类树、两类船、木码头、盆栽组），合计十三种模板。新增固定 `jiangnan` 展示模式、声明文字的实体招牌、日景／暮色、等轴／透视与漫游控件。新江南配方当前使用固定艺术调色板；不是任意模型生成器，也不承诺单图准确恢复隐藏面。

2026-09-13 展示档验证：112 项 Python、23 项 Node 回归通过；十三个 detail-3 模板真实 Blender 生成并通过二进制校验，两类树的密冠修订也重新验证。青崖渡新任务 `a7de9828a5594fdd850f58cf56a4333a`、构建 `5e3b4dbbaff43b258e309e528f61d1a88e5b6f12539a8a42b578574774f579f4`，运行时 JS SHA `b50f7c3b5ae3a795f76aa068c307351efe1aafd7a69b47d5c244d84f7d7972ba`。捕获 `806f21240e91483383bdb31c7cd5603b` 的 22 项 Chrome 检查通过，含诊断画面恢复、Escape 拖动取消、独立实例、日夜、投影、移动、复位及手机布局。原生 1536×1024，桌面 1440×900，窄屏 390×844，横屏 844×390；动态漫游短样本中位 31.2ms、P95 33ms，不代表真实手机性能。约 91.8 万可见场景三角面；组件静止时不持续重复渲染。

原生诊断恢复最大差异为 1 个色阶、约 0.1155% 像素；取消拖动为 1 个色阶、约 0.000309% 像素。控制台无运行错误，保留一组驱动 PMREM 浮点精度编译警告。模型逐项复核 23 个物件及整体，并提交审计 `03658e642f58410a89077fd6cff5e6ef`：功能检查无失败，但原画水体、环境气氛、材质老化及细部比例仍有差距；`requires_revision=true`、`workflow_complete=false`，未签发高保真完成证书。

可用 `python -m scripts3d.river_reference <青崖渡原图路径> --capture` 经独立本地 HTTP MCP 重跑。脚本是该图的测量案例，不代表任意图片都可直接套用相同布局。

本轮实际三轮构建、最终 20 项 Chrome 功能检查通过；视觉审计仍要求修改。原图对比、精确构建／截图 ID 和残留差异见 `design3d/qingyadu-reference/design-qa.md`。不得把可交互预览说成高保真完成。

当前已跑通 M1 的本地开发切片，增加 M2 的相机建议、M3 的可见轮廓诊断，以及可复用 Blender 组件链路，但未满足完整 M1 的远程 worker 登记，也未完成 M2–M4。下一步补独立原图轮廓标注／遮挡关系、船帆／台阶等更多受控组件、可靠碰撞约束，再校准视觉迭代与发布级 worker；不能为了让状态变绿而放宽完成门。

### 0.3.1：以原图为目标的校准与组件纠错

独立三维 MCP 新增 `fit_scene_landmarks(job_id, source_sha256, landmarks, base_revision)`，将原生图像点与显式三维点对应，拟合有边界的正交相机方位、俯角、缩放与平移。每点必须记录 `measurement: measured | estimated` 和 `evidence`。结果只是一份绑定原图及计划版本的建议，包含各点残差，不写入计划、不伪造捕获、不签发完成证书。遮挡角点的估计、共面歧义和高残差会明确告警。拟合小误差不是整张图还原率。

```powershell
# 必须使用该job的原生图像测点；仅创建新建议文件。
D:\venvs\canvaslab3d\Scripts\python.exe -m scripts3d.fit_landmarks <jobId> <measurements.json> --plan-output <new-plan.json>
```

`get_scene_build_handoff` 返回 `source-led-3d-v1` 原图还原要求：依次检查构图与相机、轮廓和遮挡、组件比例、几何细节、材质光照、水体和氛围。档位和面数均不是还原证据。源图中仍有明显的可修正差异时，不得宣称还原完成。

增加独立 `bamboo_rack` 与 `woven_basket` 配方：不是把货箱磨圆，而是为正确源物件建立另一种受控组件。旧 `cargo_crate` 与二维 MCP 不改变。新的 `refine_build --component-recipes <JSON>` 显式指定节点ID对应的受控配方；未知节点、非资产节点和任意脚本字段被拒绝。`--source-analysis <JSON>` 可修正原图区域的组件归属，仍须匹配同一张原图SHA并经服务校验。模板保持固定代码和实测二进制预算，未开放任意Python或模型URL执行。

青崖渡的校准点与示例布局在 `benchmarks3d/qingyadu-landmarks.json`、`qingyadu_fidelity.py`。它们是这张图的人工测量案例，不可套到其他原图。水面矩形与地上建筑的边线方向不同，实际分别校准，门楼另有独立朝向；茶棚与后方竹架拆成两个组件，避免用一个对象硬撑整个区域。

0.3.1 本轮实测：127 项 Python、24 项 Node、22 项真实 Chrome 检查通过。十五种模板保持受控；两种新增模板均验证三个细节等级。最终 job `8593e4aa0c5e4b288dcbd672c396161f`、build `2e95cd4eb620a7269b7929eeeef24c93ad97066818eaf35400cb85f6bf4c265f`、capture `0da6f10515944739a3d5ae00e028248c`、audit `b199578705cc406ba825443aa9da5041`。实际修改屋顶／层高、入口木构、独立门楼方向、树冠、竹架／篮筐、茶字附着、暖窗和水体倒影。原生 `reference-ui.png` 与其它捕获统一哈希签名，原 `reference.png` 的诊断身份链不变。

这些验证不代表高保真通过。26 条模型发现已提交，植被、石岸、材质、水体与水墨氛围仍有差异，`requires_revision=true`、`workflow_complete=false`。最新原图、近景、侧视、手机及限制记录以 `design3d/qingyadu-reference/design-qa.md` 为准。5190 本机预览已经切到上述不可变构建；没有部署远程或推送代码。
