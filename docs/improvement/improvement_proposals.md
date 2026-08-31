# raw-view 改进建议

本文面向**功能、逻辑正确性、UI 布局、用户体验**四个维度，按优先级分组。每一条都标注了涉及的模块/位置，以及预期的实现方式。

优先级：🔴 P0（强烈建议 0.1.x 内） > 🟠 P1（很好，下个迭代） > 🟢 P2（锦上添花）。

---

## 一、逻辑正确性（P0 / P1）

### P0-1 修复异步解码竞态（杜绝“翻帧/切页”错图）

- 现象：快速按 → / 切换标签页时，慢解码的旧线程完成后把旧帧画到当前标签。
- 方案：解码代数（generation counter）+ 结果校验。
  ```python
  # app.py
  self._decode_generation = 0

  def _start_async_decode(...):
      self._decode_generation += 1
      gen = self._decode_generation
      self._worker.finished.connect(
          lambda result, g=gen: self._on_decode_finished(result, g)
      )
      # worker 记录它属于哪个 item（id(item)），完成时带回

  def _on_decode_finished(self, result, gen):
      if gen != self._decode_generation:      # 过期结果
          return
      if result.item_id is not id(self._current_item()):
          return
      ...
  ```
- 同时清理悬空线程：`worker.finished` → `self._thread.finished` → `thread.deleteLater()` + `worker.deleteLater()`。

### P0-2 按需读取帧，避免整文件 OOM

- 现象：几 GB 的 RAW/YUV，每次翻帧都 `f.read()` 整文件。
- 方案：`decode_current` 改为：
  ```python
  remaining = os.path.getsize(path) - effective_offset
  if remaining < expected and warn_mismatch: ...   # 仍先弹窗
  with open(path, 'rb') as f:
      f.seek(effective_offset)
      data = f.read(expected)                       # 只读一帧
  ```
- 建议把 `formats.decode_raw/decode_yuv` 增加一个接受“bytes-like + offset/size”或直接 `(fileobj, ...)` 的入口，让 CLI 与 GUI 都能复用。

### P0-3 批量转换输出目录与 Settings 保持一致

- 现象：Batch Convert 未勾 “Same directory as input” 时落到 `输入目录/out/`，忽略用户在 Settings 配置的 `convert_out` / 自定义目录；与单文件 Convert 不一致。
- 方案：`batch_convert.py::_run_batch` 传 `output_dir`：
  ```python
  out_dir = str(Path(input_path).parent) if self._same_dir_cb.isChecked() \
            else self._settings.default_output_dirname
  output_path = format_output_template(..., output_dir=out_dir, ...)
  ```
  并根据是否已经放输入同目录，去掉/保留后面的“同目录覆盖”逻辑。

---

## 二、功能增强（P1 / P2）

### P1-1 大文件真实“多帧”体验：解码缓存 + 后台线程 + 可取消

- 现状：帧导航每次全量重解码（配合 P0-2 后即使只读一帧，解一帧大图也较慢）。
- 建议：
  1. LRU 帧缓存（解码结果按 `(path, offset, format, w, h)` 缓存，上限若干帧）。
  2. 帧导航改为真正的 worker 线程执行（含 Bayer demosaic），UI 不冻结。
  3. 增加 Cancel 能力（`cancel_flag`，循环内检查）。
  4. 增加“上一帧/下一帧”期间的键盘预载。

### P1-2 增加“间距/尺寸启发式预填”

- 现象：拖入一个未知传感器 raw，默认 2560×1440 RAW12 一定不对，要么手动填要么 Apply 时报错。
- 建议：打开文件后，若 `文件大小` 能被若干常见分辨率整除（2560×1440、1920×1080、1280×720、640×480…），在状态栏提示“按 XX×YY/RAW12 可整除，已预填，请按 Apply 校验”。避免“必然报错”的首次体验。

### P1-3 可变位深/端序/对齐的**解码预设**一键试用

- 现状：presets 只能整套保存/套用。
- 建议：在控制面板增加“**快速遍历**”按钮：对同一文件，按（alignment×endianness×preview）组合依次渲染，左右箭头在组合间切换，方便 sensor 调试时快速确认正确的参数组合。

### P1-4 直方图 / 像素探针

- 建议：图像区域显示十字光标 + 像素坐标/R/G/B 值；工具栏增加直方图弹窗（每通道 256 bin）。对确认曝光/白平衡极有价值。

### P1-5 帧差异 / 双图对比

- 建议：多帧文件增加“帧间差分（abs diff）叠加”视图；两标签页间“并排/滑动对比”。便于发现坏点/闪烁。

### P1-6 转换加“覆盖确认”

- 现象：`convert_out` 里同名模板可能覆盖已生成文件（尤其多变体）。
- 建议：生成前检查目标是否存在，弹“跳过/覆盖/全部覆盖”；多变体批量同理，避免静默覆盖。

### P1-7 转换/多变体支持**后台执行**（避免 UI 卡死）

- 现状：`_do_convert` / `_generate_variants` / `_run_batch` 都在 GUI 线程同步执行，靠 `processEvents()` 维持假响应。
- 建议：改用 `QThreadPool`/`concurrent.futures` + 信号回传进度，Convert/Batch 均可取消。

### P2-1 支持更多输入格式

- 加入 `GRAY`(8bit 灰度 raw)、10/12/14 bit 的 24/48 pixel-packed（少见于工具）保持简洁可选；至少在 FourCC 表里已覆盖 Monochrome（Y8/Y10/Y12），解码侧可按需补齐。

### P2-2 CLI 支持“输出到 stdout / 管道”

- `view` 模式 `-o -` 输出 PNG 字节到 stdout，便于脚本管道接入。

### P2-3 批量 JSON 增加相对路径基准与 glob

- `"base_dir"` 字段 + `files[].input` 支持 `*.png` glob，方便 CI 场景。

### P2-4 版本信息与自动更新提示

- 菜单 Help → About 显示版本号；Release 时提供校验和；可选“检查更新”（GitHub API）。

---

## 三、UI 布局 / 主题（P1 / P2）

### UI-1 帧导航条颜色跟随主题（修复 Light 下硬编码深色）

- `framenav.py:73-93` 的 `#frameNavBar QPushButton` 背景硬编码深色，Light 主题下刺眼。
- 建议：把配色移入 `build_ui_stylesheet`（按 theme 生成），或在 `_apply_theme` 里重新设置。

### UI-2 控制面板分组折叠

- 面板有 Preset / 解码参数 / Zoom，垂直空间紧张（尤其小屏）。
- 建议：把 “Alignment / Endianness / RAW preview / Bayer” 折叠进“RAW 高级”组（Raw 类型时自动展开），并支持 show/hide。

### UI-3 转换对话框的“目标格式一览”提示

- 在 Convert 对话框底部加一行当前将生成的**文件名（模板预览）**（已经实时算 `_auto_output_path`，展示出来即可），用户改完参数立刻能看到文件名，减少“生成了却找不到/被覆盖”。

### UI-4 多标签页的 Banner（路径 + 暗淡/未保存标记）

- 标签标题目前只有 `basename`；建议 tooltip 显示完整路径，未 Apply 的标签加“●”提示。

### UI-5 空状态引导可以更主动

- 空状态已有图标+按钮；建议预览区在真正解码失败时在图片区域显示错误横幅（而非只弹 Messagebox）。

### UI-6 工具栏增加“上一文件/下一文件”

- 打开同目录文件组时，工具栏可直接在文件间切换，比每次 Find 目录方便。

### UI-7 鼠标滚轮默认缩放

- 目前仅 Ctrl+滚轮缩放（`imageview.py:129`）。多数看图工具直接滚轮缩放，建议直接滚轮缩放、Ctrl+滚轮平移，或在 Settings 加开关。

### UI-8 全屏时隐藏面板或最小化

- F11 全屏当前只隐藏菜单栏/工具栏；建议提供“仅图像”视图选项（隐藏左侧面板），并让面板可自动隐藏（QMainWindow 的折叠栏）。

---

## 四、用户体验 / 交互细节（P1 / P2）

### UX-1 状态栏显示“当前解码是哪个文件”

- 多标签时，错误消息带上 `文件名（帧 N/N，offset=…）`，避免“哪个文件出错了”的歧义（对应 review M-2/M-3）。

### UX-2 保存自动补扩展名 + 失败反馈

- `save_display` 检查 `qimg.save()` 返回值；无扩展名自动补 `.png`。

### UX-3 “首次打开”的引导

- 首次启动显示一个欢迎对话框：简单演示“调整宽度/高度 → Apply → 翻帧”。降低上手门槛。

### UX-4 目录拖入确认 + 仅扫支持后缀

- 拖目录时：只收集支持的扩展名（`.raw/.bin/.yuv/*.png/.jpg/.bmp` 等），超过 N 个先确认再打开。

### UX-5 键盘快捷键一致性

- 统一并文档化：`Space` 下一帧 / `Shift+Space` 上一帧、`[` `]` 首末帧；放大镜 `+`/`-`；热键冲突检测。

### UX-6 多文件批量转换的报告沉淀

- Batch 完成后把“每文件的成功/失败/输出路径”存成 CSV/TXT，方便审计大任务。

### UX-7 中文界面语言支持

- 所有界面文案是英文，README 是中文。可考虑 Qt Linguist 的 zh_CN 翻译（或至少提供 Settings 语言切换），目标用户可能更习惯中文。

---

## 五、工程化（P1 / P2）

### ENG-1 依赖版本锁定

- `requirements.txt` 建议补充 `constraints.txt`（锁定已测版本）或直接固化版本，保证 CI/本地一致。

### ENG-2 测试加固与 CI

- 修复 `test_gui_helpers` 的 Windows 路径断言（review M-14）。
- 新增：异步 worker 代数竞态测试、`format_output_template` 全面单测（含 {date}/{time}/目录/未知占位符）、批量输出目录一致性测试、GUI 冒烟（可无头跑 `MainWindow` 构造）。
- 常驻 CI：push/PR 上跑 `pytest`（本次 release workflow 里已含测试步骤，可拆出）。

### ENG-3 spec 相对路径化

- `raw-view.spec` 的 `pathex`/`datas` 硬编码本机路径；建议改为相对项目根并纳入 CI 复用（详见 docs/summary §6）。

### ENG-4 崩溃日志收集

- 已有日志系统；建议 `sys.excepthook` + Qt `globalException` 写入独立 crash log 并可一键复制到剪贴板。

### ENG-5 自动更新

- 通过 GitHub Releases API 提供 “Check for Updates”，下载新 exe 并替换。

---

## 六、建议的 0.2.0 路线图（示例）

| 阶段 | 内容 |
|---|---|
| **0.1.x（修复发布）** | P0-1..3 竞态/内存/目录一致 + M-14 测试 |
| **0.2.0（体验）** | P1-1 帧缓存+后台解码、P1-2 尺寸启发式、UI-1 主题修复、UX-1 状态栏文件名 |
| **0.3.0（分析）** | P1-4 直方图/像素探针、P1-5 帧差异/对比 |
| **0.4.0（转换强化）** | P1-6 覆盖确认、P1-7 后台转换、ENG-5 自动更新 |

> 所有改动建议都保持“默认行为不变”——尤其多变体、默认输出目录、输出模板等，用户已依赖的行为不因增强而改变。
