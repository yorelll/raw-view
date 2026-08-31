# raw-view 代码架构与技术总结

- 版本：0.1.0（本次发布基线，commit `0938d3f`）
- 语言/框架：Python 3.12 + PyQt5 5.15 + numpy + opencv-python + qt-material/qtawesome（打包用 PyInstaller）
- 行数：核心源码约 5,900 行，测试约 1,100 行

---

## 1. 项目定位

**raw-view** 是一个面向摄像头 / ISP / 传感器调试工程师的 **RAW / YUV 图像查看与格式转换工具**，提供：

- **查看**：RAW8/10/12/16/32、RAW10/12/14 Packed（MIPI CSI-2）、10 种 YUV 格式；LSB/MSB 对齐、大小端、Bayer 彩色预览/灰度预览。
- **转换**：PNG/JPG/BMP → RAW 或 YUV（支持 Bayer/灰度源、缩放）。
- **批量/多变体**：多文件批量转换；一张图一次生成 格式×Bayer×分辨率 组合。
- **辅助**：传感器预设、输出文件名模板、FourCC 查找、帧导航、缩放/旋转/翻转、明暗主题、日志、CLI、批量 JSON。

核心目标：**用最少的参数配置，快速看 RAW/YUV 图像是否正确，并把标准图转成目标格式给下游（sensor/ISP）用。**

---

## 2. 代码框架总览

```
raw-view/
├── raw_view/
│   ├── __init__.py          # 包入口，导出核心 API，初始化日志
│   ├── __main__.py          # CLI 入口：view / convert / batch 三模式 + GUI
│   ├── formats.py           # 纯编解码核心（不依赖 Qt/文件系统）
│   ├── converter.py         # 文件级转换（依赖 cv2/numpy）
│   ├── models.py            # 数据模型、QSettings 持久化、主题/模板工具
│   ├── fourcc_data.py       # FourCC 内置表 + 检索逻辑
│   ├── logger.py            # 文件(旋转)+控制台双通道日志
│   ├── help_content.py      # 内嵌格式讲解 HTML
│   └── gui/
│       ├── app.py           # MainWindow：装配、菜单/工具栏、解码调度、拖拽、快捷键
│       ├── panels.py        # 左侧 ControlPanel（参数表单 + 预设 + 缩放）
│       ├── imageview.py     # 基于 QGraphicsView 的缩放/平移/旋转/翻转视图
│       ├── framenav.py      # 帧导航条（首/上/下/末 + 帧号输入）
│       ├── worker.py        # DecodeWorker（QThread 后台解码）
│       ├── widgets/
│       │   ├── filedrop.py  # 可拖入文件的 QLineEdit
│       │   └── variant_selector.py  # 多变体勾选网格
│       └── dialogs/
│           ├── convert.py      # 单图转换（含预览、多变体）
│           ├── batch_convert.py# 批量转换（表格 + 进度）
│           ├── fourcc.py       # FourCC 查找/管理
│           ├── preset.py       # 传感器预设管理（增删改查/导入导出）
│           ├── settings.py     # 偏好设置
│           └── help.py         # 帮助
├── assets/                    # logo、图标、暗/亮主题 XML、勾选/箭头/关闭 PNG
├── scripts/make_icon.py       # 由 logo.svg 生成 logo.png + raw-view.ico
├── tests/                     # unittest 测试（converter/formats/gui_helpers）
├── raw-view.spec              # PyInstaller spec（含本机路径，CI 未直接复用）
├── requirements.txt
└── docs/                      # release_exe.md / future_extensions.md / 本次新增的 review·summary·improvement
```

**依赖方向（分层，单向）**：

```
formats.py  ←  converter.py  ←  gui/worker.py          (纯计算层，不 import Qt GUI 之外的 UI)
                        ↖         models.py   ←  gui/*（对话框、面板）
                        ↖         fourcc_data.py ← gui/dialogs/fourcc.py
   CLI: __main__.py 调用 converter/models
   GUI: app.py 装配 panels/imageview/framenav/dialogs/worker
```

没有循环依赖（`format_output_template` 内延迟 `import RAW_BITS` 避开 models↔formats 环）。

---

## 3. 核心实现逻辑

### 3.1 RAW 解码（`formats.decode_raw`）

1. `expected_frame_size_raw(type, w, h)` 计算一帧字节数：
   - `RAW8` = W×H；`RAW10/12/16` = W×H×2；`RAW32` = W×H×4；
   - Packed：`RAW10P` = W×H×5/4（宽需 %4==0）、`RAW12P` = W×H×3/2（宽需 %2==0）、`RAW14P` = W×H×7/4（宽需 %4==0）。
2. `ImageSpec(offset)` 切片出一帧。
3. 16bit 存放格式按 `alignment` 归一到原生位深：
   - `lsb`：`val & ((1<<bits)-1)`；`msb`：`val >> (16-bits)`。
4. **Packed 走 MIPI CSI-2 位拆包**（MSB-first）：
   - RAW10P：4 像素 → 5 字节，B0..B3=高 8 位，末字节 4 个 2-bit LSB；
   - RAW12P：2 像素 → 3 字节，B0/B1=高 8 位，B2=两个 4-bit LSB；
   - RAW14P：4 像素 → 7 字节，按 6/4/2/6 切位。
5. `raw_to_display_gray`：按位深做 `round(v / (2^bits-1) * 255)` 归一化到 8bit；`RAW32` 走全局 min/max（见 review M-8）。

### 3.2 YUV 解码（`formats.decode_yuv`）

- 4:2:0（I420/YV12/NV12/NV21）：Y 全分辨率 + U/V 各 1/4，双线性上采样到全分辨率 → BT.601 转 RGB。
- 4:2:2（YUYV/UYVY/YVYU/VYUY/NV16/NV61）：2:1 水平采样，packed 逐个宏像素拆出 Y0/Y1/U/V，semi-planar 拆 U/V 交错平面，水平 repeat ×2。
- 转换公式：`R=Y+1.402(V-128)`、`G=Y-0.344(U-128)-0.714(V-128)`、`B=Y+1.772(U-128)`（BT.601）。

### 3.3 编码（image → RAW/YUV）

- 源图 BGR（cv2 读入）→ resize 到目标尺寸 → 灰度（`cvtColor BGR2GRAY`）或 Bayer（按 pattern 从 R/G/B 抽取到 2×2 格）→ `gray8_to_raw_bytes`：
  - 16bit 存放：`round(v/255*(2^bits-1))`；msb 时左移补位；按 endianness 输出。
  - Packed：`_pack_raw10/12/14` 复现 MIPI 布局。
- YUV 编码：RGB→BT.601 Y/U/V，4:2:0 用 2×2 均值降采样、4:2:2 用水平均值，然后按子格式排布平面/交错/宏像素。

### 3.4 多变体生成（`converter.plan_image_variants` / `generate_image_variants`）

- 输入：formats × sizes × bayer_patterns（+source_mode/alignment/template）。
- 展开规则：RAW + bayer 源 → bayer 全展开；YUV 与灰度源 → 仅按 size。
- `plan_*` 只算路径和计划（不写盘），`generate_*` 逐个生成并回调进度 → 供对话框显示 “Generating n/N…”。

### 3.5 GUI 主流程

```
MainWindow
 ├─ ControlPanel(左)  ── Apply ──► decode_current()
 ├─ QSplitter ── QStackedWidget{ emptyState, QTabWidget{ per-file: ImageView+FrameNavBar } }
 ├─ 拖拽 DropCentralWidget ─ filesDropped ─► _open_item ×N
 ├─ 菜单/工具栏：打开、保存(DPI)、转换、批量、设置、帮助、FourCC、全屏
 └─ 状态栏：File / Image / Frame / Zoom / 状态点(配色)
```

- 打开文件按扩展名推断类型（.png/.jpg/.bmp → Standard Image；.yuv/.nv12/.i420… → YUV；其余 → RAW）；
- `decode_current`：整读文件（H-2）→ 计算帧数/帧偏移 → Standard 同步解码、RAW/YUV 异步 `DecodeWorker`（QThread）；
- 帧导航：`effective_offset = base_offset + frame_index * frame_size` 更新解码；
- 面板参数与 `ViewerItem` 双向同步（type/format/width/height/alignment/endianness/offset，preview/bayer 未入 item —— review M-1）；
- 主题：qt-material 基础主题（自定制 Material 蓝 XML） + `build_ui_stylesheet` 覆盖层 + 图片装饰（勾选/箭头）；DWM 标题栏明暗随主题。

### 3.6 持久化（QSettings，组织 `yorelll` / 应用 `raw-view`）

| Key | 内容 |
|---|---|
| `convert/default_output_dirname` | 默认输出目录名（默认 `convert_out`） |
| `convert/output_template` | 输出文件名模板（含 legacy 迁移） |
| `convert/multi_variant` | 多变体开关 |
| `save/dpi` | 导出 DPI |
| `ui/font_size` / `ui/theme` | 字号 / 主题 |
| `recent/files` | 最近文件 |
| `presets/sensors` | 传感器预设（JSON 数组） |
| `fourcc/custom` | 自定义 FourCC（JSON 数组） |

### 3.7 CLI（`python -m raw_view …`）

- `view`：RAW/YUV → PNG/JPEG（`-i` 给定则命令行解码，否则启动 GUI；可带文件参数直接打开）。
- `convert`：单图 → RAW/YUV。
- `batch`：从 JSON 读全局默认 + 每文件覆盖（mode/target/width/…），支持 view+convert 混合。
- `--batch-help`：打印 JSON 格式说明。

---

## 4. 关键设计亮点

1. **纯计算核心与 UI 解耦**：`formats.py` 不依赖 Qt / 文件系统，编解码可单独测试、可被 CLI 复用。
2. **MIPI Packed round-trip**：编解码对称，测试保证 ±1 LSB。
3. **拖拽高亮无 overlay**：`DropCentralWidget` 自绘边框，事件冒泡天然透传。
4. **预设/导入导出**：JSON 原子存储 + 重名处理，团队共享友好。
5. **输出模板系统**：占位符体系 + legacy 迁移，命名可完全自定义。
6. **日志**：旋转文件日志 + 控制台，崩溃可回溯。

---

## 5. 已知短板（详见 docs/review/review_report.md）

- 异步解码竞态（旧结果可能覆盖新结果）— H-1
- 解码整读文件，大文件 OOM — H-2
- 批量转换输出目录与设置不一致 — H-4
- preview/bayer 未随标签页独立保存 — M-1
- 测试的 Windows 路径断言 3 个失败 — M-14

---

## 6. 打包与发布（本次新增）

- 本地打包：`pyinstaller raw-view.spec` 或 `docs/release_exe.md` 中的命令。
- 本次新增 **GitHub Actions**（`.github/workflows/build-release.yml`）：触发后创建 venv → 安装依赖 → 运行 `pytest` → PyInstaller 打包 → 上传 `dist/raw-view.exe` 到 GitHub Release（详见 docs/release_exe.md 新增的“GitHub Release 版本发布”小节）。
- 发布版本：**v0.1.0**，含 exe 资产 + 版本说明 + 校验和。
