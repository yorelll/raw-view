# raw-view 代码 Review 报告

* **Review 对象**：`D:\\work\\jira\\generate\_raw\\raw-view`（commit `0938d3f` / main）
* **Review 日期**：2026-08-31
* **代码规模**：核心源码约 5,900 行 Python（`raw\_view/`），测试约 1,100 行，总计约 8,500 行
* **技术栈**：Python 3.12 + PyQt5 5.15 + numpy + opencv-python + qt-material/qtawesome + PyInstaller
* **测试基线**：`pytest tests/` → **87 passed, 3 failed, 19 subtests passed**（3 个失败均为测试自身对 POSIX 路径的假设，产品代码不受影响，详见 §7）
* **总体结论**：架构清晰、模块划分合理、功能完整度高，达到可发布程度。主要风险集中在**并发/线程清理**、**大文件整读内存**、**批量转换输出目录不一致**三处，另有多处小型 UX/一致性问题。

\---

## 1\. 严重程度分级

|级别|说明|数量|
|-|-|-|
|🔴 High|会导致错误结果、崩溃或明显行为偏差，建议修复后再放量使用|4|
|🟠 Medium|明显缺陷/不一致，影响部分场景正确性或体验|14|
|🟡 Low|细节、健壮性、风格问题|12|

\---

## 2\. High 级问题

### H-1 过期的异步解码结果可能覆盖新解码结果（跨帧/跨标签页错图）

* **位置**：`raw\_view/gui/app.py:1025-1100`（`\_start\_async\_decode` / `\_cancel\_async\_decode` / `\_cleanup\_thread` / `\_on\_decode\_finished`）
* **问题**：

  1. `\_cancel\_async\_decode` 对正在运行的线程调用 `thread.quit()` 后再 `wait(500)`。但 `DecodeWorker.run()` 是纯 CPU 计算，**没有事件循环**，`quit()` 无法终止它；`wait(500)` 只等 500ms。若某帧解码耗时超过 500ms（大图/慢机器），旧线程仍然存活，而新线程已启动，**两个工作线程并发解码**。
  2. 旧线程完成后发出的 `finished` 信号依然会被 `\_on\_decode\_finished` 处理。而 `\_on\_decode\_finished` 使用的是 `self.\_current\_item()`（当前标签页），并不是“发起该次解码的那个 item/frame”。结果是：**快速切换帧或标签页时，旧帧旧数据可能被画到“当前”标签页上，覆盖新结果**（错图）。
  3. 线程和 worker 创建后从不 `deleteLater`，仅靠丢弃引用；`finished`/`error` 都接到 `\_cleanup\_thread`，存在重复清理路径。
* **失败场景**：用户按住 →（下一帧）或连续切标签页，慢速解码的旧线程先完成并 `finished`，把 A 帧像素画到 B 标签；用户看到一张“不是自己选的”图像，且状态栏却显示 “Decoded”。
* **建议**：

  * 引入**代数（generation/sequence counter）**：每次 `\_start\_async\_decode` 自增，worker 完成后携带该代数返回；UI 侧仅在“代数 == 当前代数 \&\& item 仍为当前 item”时才应用结果，否则丢弃。
  * 将 `QThread` 交给 Qt 管理（`finished` → `deleteLater`），避免悬挂对象。
  * 若想真正取消，可在 worker 中设置 `cancel\_flag`（由 `configure` 注入，每个像素循环里检查）或在轮询循环中退出。

### H-2 每次解码/帧导航都整读整个文件到内存，可能 OOM

* **位置**：`raw\_view/gui/app.py:972-974`（`decode\_current` 中的 `data = f.read()`）；`raw\_view/converter.py:307-309 / 346-347`（`raw\_file\_to\_image` / `yuv\_file\_to\_image` 同样整读）
* **问题**：

  1. 对多帧大文件（数 GB 的 RAW/YUV），`decode\_current` 每次（含每帧导航）都 `open(...).read()`，把整个文件读进内存，再仅截取 1 帧用于解码。一次帧导航 = 一次全文件 IO + 一次整文件内存占用。
  2. `formats.decode\_raw/decode\_yuv` 基于 `bytes` 输入而不是文件句柄，因此必须整读。
* **失败场景**：4GB 的 RAW 文件，`f.read()` 产生 4GB bytes，加上 numpy 解码数组，32 位 Python / 内存不足的机器直接 OOM 崩溃；即使不崩溃，每翻一帧都产生 4GB IO，极慢。
* **建议**：按需读取帧区间：`f.seek(effective\_offset); data = f.read(frame\_size)`。可将 `decode\_raw/decode\_yuv` 增加一个 `memoryview`/`seek` 接口，或新增 `decode\_raw\_fd(f, ...)`；`ImageSpec` 已有 `offset` 字段，读取端对齐即可。对 `raw\_file\_to\_image/yuv\_file\_to\_image` 同样处理。

### H-3 中间低效：整读后尺寸不匹配判定浪费 IO / 大文件帧数计算

* **位置**：`raw\_view/gui/app.py:936-997`
* **问题**：`decode\_current` 先 `f.read()` 整个文件，再判断“剩余数据 < 预期帧大小 → 弹窗”。当用户点了“不解析”退出时，**整文件已经读入**；当用户只是浏览（未 Apply）默认参数必不匹配时，每次拖入大文件都会发生一次整读。且 `\_compute\_frame\_info` 用 `os.path.getsize`（不开文件）计算帧数是 OK 的，但随后仍整读。
* **建议**：先 `os.path.getsize` 判断（已用 get size 校验过吗？当前 `\_compute\_frame\_info` 只用于帧数，size 校验靠整读后的 len）。改为：先取文件大小 → 计算 expected → 不充分时先弹窗 → 确认后才 seek+read 需要的区间。

### H-4 GUI 批量转换忽略 Settings 里的默认输出目录

* **位置**：`raw\_view/gui/dialogs/batch\_convert.py:335-347`
* **问题**：单文件 Convert 对话框用 `self.\_settings.default\_output\_dirname`（默认 `convert\_out`）作为输出目录；而 Batch Convert 对话框调用 `format\_output\_template(...)` 时**没有传 `output\_dir`**，会落入 `models.format\_output\_template` 的默认值 `src.parent / "out"`；仅当勾选 “Same directory as input” 时又改为输入文件同目录。结果：

  * 不勾选 "Same directory as input" → 输出到 `输入目录/out/`（而不是用户在 Settings 配置的 `convert\_out` 或自定义目录）；
  * 勾选时 → 输入同目录。
  * 与 Convert 对话框、CLI `convert\_out` 行为不一致，用户会困惑。
* **建议**：在 `\_run\_batch` 中给 `format\_output\_template` 传入 `output\_dir=str(Path(input\_path).parent) if same\_dir else self.\_settings.default\_output\_dirname`（与生成单文件路径一致），且“Same directory as input”默认勾选的状态保持。

\---

## 3\. Medium 级问题

### M-1 多标签页“独立参数”不完整：Preview / Bayer 未按 item 保存恢复

* **位置**：`raw\_view/gui/app.py:823-855`（`\_save\_panel\_to\_item` / `\_load\_item\_to\_panel`）；`raw\_view/models.py:93-105`（`DecodeOptions` 无 preview/bayer 字段）
* **问题**：README 声称“多标签页 item：独立参数”。但 `\_save\_panel\_to\_item` 只保存 type/formats/width/height/alignment/endianness/offset，**不保存 `preview\_mode` 和 `bayer\_pattern`**；`\_load\_item\_to\_panel` 也不恢复这两项。解码时（`app.py:1029-1030`）直接读 `self.panel.raw\_preview\_combo.currentText()` 等共享面板值。于是：

  * 标签 A 设 Bayer=BGGR，切到标签 B（不同 RAW）再切回 A，Bayer 仍是面板当前值（可能被 B 改过），A 的原始 Bayer 丢失；
  * 灰度预览/彩色预览看起来是“全局”的，与文档不符。
* **建议**：把 `preview\_mode`、`bayer\_pattern` 纳入 `DecodeOptions` 与 `ViewerItem`，随 item 一起保存/恢复；解码用 `item.options` 而非面板实时值。

### M-2 异步错误定位不准确

* **位置**：`raw\_view/gui/app.py:1090-1092`
* **问题**：`\_on\_decode\_error` 只弹 `QMessageBox`，没有记录是哪个文件/哪一帧失败；`DecodeWorker` 里 `logger.exception` 有记录，但用户看到的报错信息 = `str(exc)`（如 “data too short...”），不含文件路径/帧号，排查不便。
* **建议**：`error` 信号携带 `(item\_key, frame, message)`，或在主窗口按当前 item 拼接“文件名 + 帧号 + offset”到错误文本与日志。

### M-3 `save\_display` 静默失败仍显示成功

* **位置**：`raw\_view/gui/app.py:1104-1123`
* **问题**：`qimg.save(path)` 返回值被忽略；若用户输入无扩展名路径、目录不可写、或格式不支持，QImage 可能保存失败，但状态栏仍提示 “Saved: ... @ 300 DPI”。
* **建议**：`if not qimg.save(path):` 弹错误并置状态 `error`。还可在保存后 `QFileDialog` 无扩展名时自动补 `.png`。

### M-4 帧导航条按钮深色硬编码，Light 主题下视觉错乱

* **位置**：`raw\_view/gui/framenav.py:73-93`
* **问题**：`#frameNavBar QPushButton` 的背景/边框硬编码为深色（`#2A2D4A` 等），在 Light 主题下按钮仍是深底，与浅色面板反差极大（也违反 README “Light/Dark 双主题” 的承诺）。
* **建议**：跟随主题——把按钮样式改由 `models.build\_ui\_stylesheet` 注入，或在 `MainWindow.\_apply\_theme` 时按 `settings.ui\_theme` 生成对应配色，而不是 `setStyleSheet` 硬编码。

### M-5 批量 CLI 模式读取 JSON 无显式 encoding；CLI 输出编码不跨平台

* **位置**：`raw\_view/\_\_main\_\_.py:372`（`open(args.batch\_file)`）；CLI 各 `print`（`--batch-help` 的箭头/CJK 文本）
* **问题**：
  1. `open(...)` 默认编码依赖 locale；含中文路径/注释的 batch JSON 在非 UTF-8 系统（如中文 Windows GBK locale）下可能 `UnicodeDecodeError`。
  2. **CLI 输出编码**：`--batch-help` 等打印 `─ ◀ →` 箭头与 CJK 文本；当 stdout 绑定到窄单字节编码（GitHub Actions Windows runner 默认 `cp1252`、中文系统 `cp936/GBK`）时抛 `UnicodeEncodeError` 崩溃——**0.1.0 首次 CI 发布即因此失败**（`'charmap' codec can't encode characters ... position 28-29`）。
* **已修复（0.1.0）**：`__main__.py` 新增 `_make_utf8_stdio()`，在 `main()` 入口把 stdin/stdout/stderr 重配置为 UTF-8（尊重 `PYTHONIOENCODING`/`PYTHONUTF8`，不覆盖用户显式选择），附回归测试。
* **仍建议**：`open(args.batch\_file, encoding="utf-8")`；`--batch-help` 注明 batch JSON 需为 UTF-8。

### M-6 大规模解码无上限保护，可能崩溃

* **位置**：`raw\_view/gui/app.py`（width/height spin 上限 65535）；CLI `--width/--height`、batch JSON 无上限
* **问题**：GUI 允许 65535×65535，decode 会分配 `width\*height` 的 uint16/uint8 数组 → 65535²×2B ≈ 8.6GB，直接 OOM；CLI/batch 无任何上限更危险。
* **建议**：在 `\_run\_view\_decode` / `\_run\_batch` / `decode\_current` 前加一个“像素数 × 字节数 ≤ 阈值（如 512MB）”校验，超限报错；GUI 面板 width/height 上限可保留但解码前拦截。

### M-7 `ImageView.fit\_image` 的 zoom% 依赖 transform m11，旋转后失真

* **位置**：`raw\_view/gui/imageview.py:58-63, 94-110`
* **问题**：`fit\_image` 用 `transform().m11()\*100` 估 zoom%；旋转/翻转后 m11 不再是纯比例，会得到错误百分比显示。`rotate\_cw/ccw` 维护的 `\_rotation` 字段从未被 `fit\_image`/`reset\_zoom` 使用，旋转+适应窗口后旋转会丢失。
* **建议**：显式记录缩放系数（自身维护 `self.\_zoom\_percent`），`fitInView` 后利用视图/场景尺寸数学重新计算比例，而不是读 m11；将旋转纳入统一变换管理（旋转后 reset 变换时保留旋转）。

### M-8 `RAW32` 解码忽略 alignment，且显示用全局 min/max 拉伸

* **位置**：`raw\_view/formats.py:137-139`（RAW32 不做 LSB/MSB 处理），`formats.py:185-198`（`\_to\_8bit` bits=None → min/max）
* **问题**：RAW10/12/16 都按 alignment 处理，RAW32 完全忽略 alignment（不一致）；`\_to\_8bit` 对 RAW32 走 min-max 全局拉伸，均匀灰度帧会因 `vmax<=vmin` 得全黑。
* **失败场景**：RAW32 全 0 帧 → `raw\_to\_display\_gray` 返回全 0 → 图像全黑，无提示。
* **建议**：RAW32 也按 endianness/alignment 统一（若合理）；`\_to\_8bit` 在 min==max 时返回中性灰（128）而非全黑，或显示“数据平坦”提示。

### M-9 Preset 面板与 item 双向同步缺 preview/bayer（与 M-1 同源）

* **位置**：`raw\_view/gui/app.py:1347-1375`（`\_on\_preset\_selected` 已 set preview/bayer，但后续 `\_save\_panel\_to\_item` 不存）；`models.py:823-833`
* **影响**：预设包含 preview/bayer，选中后已填到面板；但只要切一次标签页，面板上的 preview/bayer 就会被其它 item 覆盖，再切回时不还原。
* **建议**：同 M-1，一并把 preview/bayer 纳入 item 状态。

### M-10 拖拽整个目录会递归扫描并全部建标签页

* **位置**：`raw\_view/gui/app.py:190-200, 202-221`
* **问题**：拖入一个含上千文件的目录会把所有文件都加入 tabs（每个默认 2560×1440 RAW12，实际上多数不是 RAW12），且全部在 GUI 线程递归扫描，大目录卡顿。
* **建议**：限制目录内文件扩展名到支持集合；弹出“扫描到 N 个文件，是否全部打开”的确认；扫描放线程。

### M-11 拖文件默认参数必然不匹配，首次拖入即整读+可能弹错（结合 H-3）

* **位置**：`raw\_view/gui/app.py:694-700, 936-997`
* **问题**：用户拖入一个实际上不同尺寸的 raw，`decode\_current` 用默认 2560×1440 RAW12 解码；由于 `warn\_mismatch=False` 只在 Apply 时弹校验，正常拖入不弹；但小文件（如内存不够的前置）仍会整读并失败弹 “Decode Failed”——对“还没配置参数”的用户不友好。README 已说明只有 Apply 才校验，属已知设计；此处列为优化项：首次拖入可先按文件大小+常见分辨率做启发式预填。
* **建议**：Low 优先级的体验优化；顶多提示“文件大小=…，当前参数帧大小=…，请设置参数后点 Apply”。

### M-12 `--batch-help` 里 `output\_dir` 说明与实现不完全一致

* **位置**：`raw\_view/\_\_main\_\_.py:94-135`
* **问题**：帮助文档说默认输出到 `convert\_out/` 或 `view\_out/`；实现 `\_default\_out\_dir(mode)` 确实如此（convert→convert\_out，view→view\_out），但 `\_run\_batch` 在输出模板后又 `Path(input\_path).parent / Path(output\_path).name` 强制放到输入同目录（`\_\_main\_\_.py:444-446`），与实际行为矛盾（实际是“输入同目录”，不是 convert\_out）。
* **建议**：统一语义：batch 里既不显式给 output\_dir 就放输入同目录（与 GUI 勾选 “Same directory” 默认一致），并同步更新 `--batch-help` 与 README。

### M-13 依赖未锁定版本

* **位置**：`requirements.txt`
* **问题**：全部 `>=` 无上限；跨机器安装可能拿到不兼容的新版 qt-material/opencv，导致 CI 与本地构建产物行为不一致。
* **建议**：提供 `requirements.txt`（宽松）+ `constraints.txt`（锁定已测版本），或直接固化当前已验证版本（numpy 2.4.4 / opencv 4.13 / PyQt5 5.15.11 / qt-material 2.17 / QtAwesome 1.4.2 / Pillow 12.3 / pyinstaller 6.20）。

### M-14 测试对 Windows 不友好（3 个失败）

* **位置**：`tests/test\_gui\_helpers.py:22-41`
* **问题**：`test\_build\_default\_output\_path\_raw/yuv/edge\_cases` 断言 POSIX 路径 `/tmp/input/out/sample.raw`，在 Windows 上 `Path` 返回 `\\tmp\\input\\...`，导致 3 个用例失败。测试是产品代码的“可移植性告警”，修复测试用 `os.path.join`/`PurePosixPath` 或按平台分支。
* **建议**：用 `os.path.join("/tmp", "input", "out", "sample.raw")` 或 `assertEqual(PurePosixPath(p), PurePosixPath("/tmp/input/out/sample.raw"))`，并在 CI 里同时跑 Windows/Linux。

\---

## 4\. Low 级问题

|#|位置|问题|
|-|-|-|
|L-1|`raw\_view/\_\_main\_\_.py:196-200`|`\_resolve\_ext` 定义后从未被调用（死代码），建议删除或使用。|
|L-2|`raw\_view/models.py:169`|`ViewerItem.rotation\_angle` 字段从未被使用。|
|L-3|`raw\_view/gui/app.py:1007`|`\_decode\_standard\_image(data, ...)` 的 `data` 参数未被使用（已重新 `load\_bgr\_image`），可去掉避免误导。|
|L-4|`raw\_view/gui/app.py:890-894`|`\_on\_panel\_type\_changed` / `\_on\_panel\_raw\_preview\_changed` 空实现，占位即可或删除连接。|
|L-5|`raw\_view/formats.py:185-198`|`\_to\_8bit` 的 `v.astype(np.float32)` 对大图（几千×几千）会多一次 4x 内存拷贝；可逐通道或 in-place。|
|L-6|`raw\_view/gui/dialogs/convert.py:72-74` vs `panels.py:62-71`|Convert/Batch 对话框的 RAW 格式列表缺少 `RAW32`，且顺序与面板不一致（面板有 RAW16/RAW32 而对话框到 RAW16 为止）。|
|L-7|`raw\_view/gui/dialogs/convert.py:90-96`|Convert 对话框默认宽高 640×480，而主面板默认 2560×1440、YUV 对话框资源默认 640×480，三处默认不一致。|
|L-8|`raw\_view/gui/dialogs/settings.py:251`|Browse 输出目录用相对名称（如 “out”）作为起始路径，在不同 CWD 下弹窗起点不一致。|
|L-9|`raw\_view/gui/imageview.py:129-133`|wheel 缩放仅在 Ctrl 按下时生效；多数图像软件直接滚轮缩放，可加回 plain-wheel 或设置项。|
|L-10|`raw\_view/gui/app.py:127-137`|`DropCentralWidget.paintEvent` 每次 hover 都创建 QPainter，性能无关但可缓存颜色/字体。|
|L-11|`raw\_view/\_\_main\_\_.py`|CLI 各模式 `print`/`sys.exit(1)` 混杂，错误路径不统一；建议统一异常出口与退出码语义。|
|L-12|`raw\_view/logger.py:29`|`setup\_logger` 固定 DEBUG 级别写盘；高频 DEBUG 日志会快速消耗 5MB×3 滚动空间，可提供级别环境变量。|

\---

## 5\. 各模块专项 Review

### 5.1 `formats.py`（核心编解码，409 行）——质量最好

* 抽象清晰：`ImageSpec` / `expected\_frame\_size\_\*` / `decode\_raw` / `decode\_yuv` / `gray8\_to\_raw\_bytes` / `rgb\_to\_yuv\_bytes`。
* MIPI CSI-2 Packed（RAW10/12/14P）布局正确，且编码/解码对称，round-trip 测试通过（±1 LSB）。
* YUV 4:2:0/4:2:2 的 planar/semi-planar/packed 全排列正确，`\_YUV422\_PACKED`/`\_YUV422\_SEMIPLANAR` 常量集避免重复字符串。
* 问题集中在：RAW32 忽略 alignment（M-8）、`\_to\_8bit` 平坦帧全黑（M-8）、整帧解码接口只吃 `bytes`（H-2）。

### 5.2 `converter.py`（文件级转换，354 行）

* `bgr\_to\_bayer8` 的 4 种 pattern 取位置正确（有单测覆盖）。
* `plan\_image\_variants` / `generate\_image\_variants` 的多变体展开逻辑正确，YUV/灰度源正确跳过 bayer 展开。
* `cv2` 延迟 import + `\_require\_cv2` 优雅降级是好的模式。
* 问题：整读文件（H-2）；`generate\_image\_variants` 串行同步执行，UI 用 `processEvents` 维持响应（可接受但可升级为后台任务）。

### 5.3 `models.py`（数据/设置/stylesheets，939 行）

* `AppSettings` 用 QSettings + JSON 数组存储 presets/fourcc，原子性好、跨平台一致，注释充分。
* 输出模板系统（`format\_output\_template`）占位符完整、大小写敏感、未知占位符原样保留；legacy 模板一键迁移设计到位。
* `build\_ui\_stylesheet` 用 palette 字典生成 QSS，主题扩展方便。
* 问题：`ViewerItem.rotation\_angle` 死字段（L-2）；`DecodeOptions` 缺少 preview/bayer（M-1 同源）。

### 5.4 GUI 层（app/panels/imageview/framenav/worker）

* 结构清晰：MainWindow 装配、ControlPanel 参数、ImageView 显示、FrameNavBar 帧导航、DecodeWorker 后台解码。
* 拖拽 DropCentralWidget 直接绘制高亮边框，替代 overlay，设计优雅。
* 线程模型存在 H-1 竞态；`preview/bayer` 未入 item（M-1/M-9）；framenav 硬编码深色（M-4）。

### 5.5 对话框（convert/batch\_convert/fourcc/preset/settings/help）

* Convert：预览缩略图 + 帧大小信息 + 多变体面板，功能完整。
* Batch：QTableWidget 状态列 + QProgressDialog，可见性好。
* FourCC：内置 40+ 条 + 自定义 CRUD + 搜索，MRU/映射简单可靠。
* Preset：导入/导出/重名处理完善，Save/Cancel 语义正确。
* Settings：未保存修改确认（保存/不保存/取消）做得仔细。
* 主要问题：Batch 输出目录不一致（H-4）。

### 5.6 测试

* 覆盖良好：packed round-trip、YUV round-trip、边界/截断、Bayer pattern、RAW16 端序×对齐矩阵。
* 缺失：**GUI 流程无测试**（面板信号、帧导航、异步 worker 竞态、对话框逻辑）；批量输入输出路径逻辑无测试；`format\_output\_template` 的 {date}/{time} 与目录计算无明确测试；Windows 路径测试需修复（M-14）。
* CI：暂无（本次任务将新增 build-release workflow；建议后续加 `pytest` job 常驻跑测试）。

### 5.7 打包（raw-view.spec / docs/release\_exe.md）

* `raw-view.spec` 的 `pathex` 与 `datas`（`.venv/.../translations`）硬编码了本机路径，换机/CI 无法直接复用；本次 CI 用命令行参数绕过（详见 `docs/summary`）。建议把 spec 改造为相对路径并纳入 CI 复用。

\---

## 6\. 安全与健壮性补充

* 无外部网络交互、无 shell 拼接注入面；文件读写均本地，风险低。
* `open(...)` 均未显式指定 `encoding`（除部分 JSON 导出）：写入统一 UTF-8，读取需注意（M-5）。
* QSettings 读写 JSON 均做类型/结构防御（`\_load\_presets\_raw` 等），异常安全。
* 大数组内存上限缺失（M-6），是当前最主要健壮性风险。

\---

## 7\. 测试基线（执行于 Windows 11 / Python 3.12.3）

```
87 passed, 3 failed, 19 subtests passed
FAILED tests/test\_gui\_helpers.py::GuiHelperTests::test\_build\_default\_output\_path\_edge\_cases
FAILED tests/test\_gui\_helpers.py::GuiHelperTests::test\_build\_default\_output\_path\_raw
FAILED tests/test\_gui\_helpers.py::GuiHelperTests::test\_build\_default\_output\_path\_yuv
```

3 个失败均为测试对 `/tmp/...` POSIX 路径的硬编码断言，在 Windows 下 `Path` 产出 `\\` 导致；**产品函数 `build\_default\_output\_path` 在 Windows 下行为正确**（相对目录拼接），修复测试即可。

\---

## 9\. 结论

* **架构与工程质量**：整体优秀。分层清晰（formats / converter / models / gui 包 / dialogs / worker），关注点分离，注释与文档（README/docs）质量高；编解码核心有测试背书。
* **最大风险**：异步解码竞态与大文件内存，直接影响可靠性；批量输出目录不一致直接影响预期。

