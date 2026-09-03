# raw-view UI 评审处置回复 — 0.3.1

> 本文件是对 [`ui-review-report-0-3-1.md`](ui-review-report-0-3-1.md) 的逐条处置回复；报告本身只记录发现，不记录修复状态。
> 本轮以有效 EXE 证据为准：`screenshot-index.md` 认定有效的 `L-*` / `D-*` 与历史 `S01–S10`、`S13`、`S14–S28`。报告引用但当前工作树未保留的 `L/D-convert-filled`、`L/D-convert-yonly`、`L/D-settings-default`、`L/D-format-help` PNG/CSV 不作为独立截图证据；相应结论由源码及仍存在的同类证据交叉核验。

- **对应审查**：`docs/ui-review/ui-review-report-0-3-1.md`
- **评审基线**：`dist/raw-view/raw-view.exe`；代码版本 `0.3.1`
- **处置日期**：2026-09-03
- **统计**：报告共 13 项 → 已修复 10 / 无需修改 3；另有自审 3 项 → 已修复 3 / 无需修改 0

> 统计将 UI-14～UI-16 单独列在“新增自审问题”，不混入报告原有 13 项。

> 统计将 UI-14～UI-16 单独列在“新增自审问题”，不混入报告原有 13 项。

报告中“已确认优点”整体接受；本回复不重复列举优点，只记录问题的判定、证据和处置。

---

## 0.3.1

### P1

#### UI-01 — Preset 图标按钮和 Zoom slider 缺少 UIA 可访问名称

- **判定**：`CONFIRMED`
- **证据与核验**：`docs/ui-review/evidence/S13-raw12-accessibility-controls.csv` 中两个 `QPushButton` 和 `QSlider` 的 `name` 均为空；源码原先只有 `setToolTip()`（`raw_view/gui/panels.py` 的 preset/zoom 初始化）。这不是截图视觉问题，而是 UIA 语义缺失。
- [x] **已修复**
- `ControlPanel` 为保存/管理 preset 按钮增加 `Save sensor preset`、`Manage sensor presets` accessible name/description；为滑块增加 `Zoom level` accessible name/description。原有 tooltip 保留，Tab/鼠标操作不变。

#### UI-02 — 帧导航仅用符号，缺少可读的程序化名称

- **判定**：`CONFIRMED`
- **证据与核验**：`S13-raw12-accessibility-controls.csv` 与 `L/D-raw-last-frame-controls.csv` 显示按钮名为 `│◀`、`◀`、`▶`、`▶│`；`raw_view/gui/framenav.py` 原先只设置符号文本和 tooltip。Home/Left/Right/End 快捷键逻辑位于 `app.py:1703-1739`。
- [x] **已修复**
- 四个按钮增加 `First frame`、`Previous frame`、`Next frame`、`Last frame` accessible name/description，并为当前帧和总帧数控件补充语义。符号和快捷键 tooltip 保持不变。

### P2

#### UI-03 — RAW 参数面板混合解析参数和视图参数，主任务层级偏弱

- **判定**：`PARTIAL`
- **证据与核验**：`L/D-raw-loaded`、`S09-compact-controls.csv`、`L/D-tab-dirty` 能证明参数在滚动表单中顺序连续，Zoom 与 Width/Height/Offset 同处主表单；源码 `raw_view/gui/panels.py:227-273` 仅将 Preset 与其余内容用分隔线分开，尚未形成 Source/Geometry/Interpretation/View 四组。
- [ ] **无需修改**
- 本轮不重排主表单。当前实现已把 Apply 固定在滚动区外（`panels.py:266-273`），条件参数会同步隐藏并从 Tab 链移除，且滚动区允许紧凑窗口继续访问 Width/Height/Offset。完整分组/150% DPI 验收仍属于待补的人工环境项；在没有新的 DPI/键盘焦点证据前，大幅重排会增加面板状态同步和既有布局回归风险，收益不足以纳入本次小范围处置。

#### UI-04 — 紧凑窗口的状态栏会挤压 Frame/Zoom/Decode 状态

- **判定**：`CONFIRMED`
- **证据与核验**：`docs/ui-review/evidence/S09-compact-controls.csv` 显示 File/Image/Frame/Zoom/Decoded 的宽度约为 470/371/85/92/88；`app.py:294-307` 以永久控件固定比例加入状态栏。源码未给长文件名或高 DPI 下的优先级折叠。
- [x] **已修复**
- `app.py` 为文件状态增加 basename + 完整路径 tooltip；帧大小改为人类可读单位（例如 `7.0 MB/frame`），精确字节数保留在 image 状态 tooltip，并对文件字节数使用千位分隔。这样窄窗口更容易保留关键信息，详细值仍可获取。

#### UI-05 — Convert 多变体选项网格会压低单图转换主流程

- **判定**：`CONFIRMED`
- **证据与核验**：报告所引 `L/D-convert-filled`、`L/D-convert-yonly` 当前工作树缺失，故不将其作为独立截图引用；源码 `raw_view/gui/dialogs/convert.py:225-257` 和 `raw_view/gui/widgets/variant_selector.py:70-157` 明确显示多变体区会常驻于可滚动内容，格式、YOnly bit depth、Bayer、Sizes 形成多组网格。
- [ ] **无需修改**
- 多变体本身已由 Settings 的 `multi_variant_enabled` opt-in 控制（`convert.py:225-240`），未启用时不会占用单图流程；启用后滚动区和底部固定 Convert 按钮保证主操作可达。报告建议的“折叠 + 输出数量摘要”属于增强设计，当前证据没有证明常规单图用户会看到该区，重做布局的风险高于本轮收益。

#### UI-06 — Batch 空表缺少“先添加文件”的强空状态，Start 视觉上过早出现

- **判定**：`CONFIRMED`
- **证据与核验**：`L/D-batch-empty` 与 `S06-batch-empty-controls.csv` 显示表格没有表内空状态文字，而 `Start Batch Convert` 原先启用；`batch_convert.py:320-324` 仅在点击后弹出 `No files to convert.`。
- [x] **已修复**
- Batch 初始时禁用 Start，并设置 tooltip 说明“Add at least one image...”；`_add_files()` 添加首个文件后启用并更新 tooltip，`_clear_files()` 清空后重新禁用。现有 No files 保护保留，拖放/Browse 入口增加 accessible name/description。

#### UI-07 — 对话框的 context-help / Close 模式不一致

- **判定**：`CONFIRMED`
- **证据与核验**：报告引用的 `L/D-settings-default`、`L/D-format-help` 当前 PNG/CSV 未保留；仍有效的 `L/D-about-controls.csv`、`L/D-settings-dirty-warning-controls.csv` 与历史 `S04-settings-default-controls.csv`、`S07-format-help-controls.csv` 显示只读 Help/About 主要依赖标题栏 X，而 FourCC 具有系统 context-help chrome；`help.py` 原先 Help/About 都没有内部 Close，Settings 则主动移除无绑定 `?`。
- [x] **已修复**
- Help 和 About 增加显式、可聚焦、可读屏的 `Close` 按钮；Help 内容区增加 accessible name/description。Settings 已保留移除无动作 `?` 的设计，Help/About 的 X/Esc 语义仍由 Qt 保持。

#### UI-08 — Convert 与 Batch 输出冲突的默认策略仍需完成 Batch hardened 证据

- **判定**：`PARTIAL`
- **证据与核验**：`L/D-convert-collision-controls.csv` 确认单图冲突按钮为 `Skip / Rename (_1) / Overwrite / Overwrite All`；Batch hardened 冲突证据在 `screenshot-index.md` 中明确标为待补，源码 `batch_convert.py:502-532` 显示无冲突默认 `rename`、有冲突默认按钮为 Rename。
- [ ] **无需修改**
- 这是“待补 Batch hardened 证据”而不是已确认的跨流程实现缺陷；源码已经明确 Batch 的安全默认值和 Enter 默认按钮。没有真实 Batch 冲突窗口、焦点、Enter 和文件结果证据，不擅自改变策略；按报告后续验收要求补采后再决定。

### P3

#### UI-09 — 状态栏帧大小使用裸字节数，与其他界面人类可读单位不一致

- **判定**：`CONFIRMED`
- **证据与核验**：`L/D-raw-loaded-controls.csv`、`L/D-nv12-loaded-controls.csv` 显示原文为 `Image: ... (7372800)` / `(...5529600)`；`app.py:1168-1177` 和 `_on_decode_success()` 均直接插入整数。
- [x] **已修复**
- 主窗口状态改为 `... (7.0 MB/frame)` 这类可读值，完整精确数值以 tooltip 提供；单位明确标注按帧，避免把整个文件大小误读为 frame size。

#### UI-10 — Settings 信息图标是 hover-only，键盘/读屏不可等价访问

- **判定**：`CONFIRMED`
- **证据与核验**：历史有效 `S04-settings-default-controls.csv` 的 template/variant 右侧为无名称 `ImageControl`；源码 `settings.py:236-249` 原先返回仅有 tooltip 的 `QLabel`，鼠标 hover 之外没有焦点或激活路径。
- [x] **已修复**
- `_info_icon()` 改为可聚焦 `QPushButton`，设置 `More information` accessible name/description，保留 tooltip，并以 Enter/Space 弹出同一说明。Template/variant 两个帮助入口均复用该实现。

#### UI-11 — FourCC 搜索缺少程序化标签，空结果上下文较弱

- **判定**：`CONFIRMED`
- **证据与核验**：`L/D-fourcc-default-controls.csv`、`L/D-fourcc-no-result-controls.csv` 显示搜索 `EditControl` name 为空；空态 status 仅为 `0 format(s) shown (0 custom)`。源码 `fourcc.py:147-155`、`234-248` 与 `_on_search()` 对应此行为。
- [x] **已修复**
- 搜索框增加 `Search formats` accessible name/description，Clear 增加语义和 tooltip；搜索后状态改为 `Showing N result(s) for "query" (...)`，无搜索时保留原总数文案。

#### UI-12 — Convert 预览容易被理解为目标编码结果，而实际是源图缩略图

- **判定**：`CONFIRMED`
- **证据与核验**：报告引用的 `L/D-convert-filled`、`L/D-convert-yonly` PNG/CSV 当前缺失；源码 `convert.py:137-158` 创建 `Preview`、`_preview_thumb`，`429-483` 从 `load_bgr_image(input_path)` 生成缩略图并仅估算目标 frame size，未提示缩略图不是编码后 RAW/YUV。
- [x] **已修复**
- 标题改为 `Source preview`；缩略图 accessible description 明确是源图且不代表编码后字节；信息区使用 `Output specification`，Frame size 显示人类可读值并保留精确字节。

#### UI-13 — Light/Dark 可用，但仍需专业对比度和 focus 状态量化测试

- **判定**：`PARTIAL`
- **证据与核验**：`screenshot-index.md` 记录 Light `(230,232,238)`、Dark `(23,26,36)` 背景像素，`L/D-*` manifest 证明主题真实生效；但报告也明确 WCAG、hover/focus/disabled 全状态尚未量化。
- [ ] **无需修改**
- 这是测试覆盖/验收项，不是当前已证实的代码缺陷。没有 WCAG 测量和完整 focus/disabled 采集前不凭截图臆判对比度；本轮不改变主题色，保留后续人工显示环境与量化测试。

## 新增自审问题

自审仅使用索引允许的有效证据和源码；报告已列问题不重复。

#### UI-14 — Convert/Batch 输入字段的 UIA 名称为空

- **维度**：12 焦点与无障碍；5 文案。
- **位置**：Convert 输入/输出字段、Batch 文件列表入口。
- **证据**：历史有效 `S05-convert-empty-controls.csv` 中两个 `EditControl` 的 name 为空；`S06-batch-empty-controls.csv` 中 `FileDropLineEdit` 的 name 为空。对应源码原先只设置 placeholder（`convert.py:72-75`、`batch_convert.py:80-83`）。
- **影响**：读屏/语音用户只能得到无语义编辑控件；placeholder 不应替代永久标签。
- **修复建议**：设置 Input image、Output file、Images to convert 的 accessible name/description。
- **判定**：`CONFIRMED`
- [x] **已修复**
- Convert 输入/输出和 Batch 输入入口已补 accessible name/description；字段可视标签、placeholder 和交互保持不变。

#### UI-15 — Batch 文件表没有可编程的空状态提示

- **维度**：10 状态；11 表格；2 核心效率。
- **位置**：Batch Convert 初始文件表。
- **证据**：有效 `L/D-batch-empty` 与 `S06-batch-empty-controls.csv` 显示 `QTableWidget` 无行、无表内引导文本；源码 `batch_convert.py:100-107` 只创建空表，未设置 empty-state label。
- **影响**：用户打开 Batch 后不知道表格是否支持拖放、第一步应点 Add Files，且 Start 原先可点。
- **修复建议**：在空表状态显示 Add/Drop 引导，并同步禁用 Start。
- **判定**：`CONFIRMED`
- [x] **已修复（部分处置）**
- 本轮落实高影响部分：Start 空表禁用并提供 tooltip，添加文件后同步启用；表内文字引导未另加 overlay，避免改变 QTableWidget 行/列语义和拖放布局。

#### UI-16 — VariantSelector 的说明图标同样为 hover-only

- **维度**：7 图标；8 键鼠；12 焦点与无障碍。
- **位置**：Convert/Batch 多变体区的 YOnly Bit Depth、Bayer Patterns、Sizes 说明入口。
- **证据**：源码 `raw_view/gui/widgets/variant_selector.py:33-44` 的 `_info_icon()` 返回仅带 tooltip 的 `QLabel`；该区由 `convert.py:225-240` / `batch_convert.py:200-206` 条件创建。报告 UI-10 只覆盖 Settings 图标，未覆盖此复用场景。
- **影响**：开启多变体后，键盘和读屏用户仍不能访问关键“适用范围/自定义尺寸”说明。
- **修复建议**：统一为可聚焦 info button，Enter/Space 打开等价说明。
- **判定**：`CONFIRMED`
- [x] **已修复**
- `variant_selector.py` 的 `_info_icon()` 已改为可聚焦 `QPushButton`，补充 `More information` accessible name/description，并支持 Enter/Space 打开同一说明；tooltip 保留。该修复覆盖 YOnly、Bayer Patterns、Sizes 三组说明入口。
