# raw-view

Python RAW/YUV 图像查看与格式转换工具。

## 功能

- RAW 查看：RAW8/10/12/16/32、RAW10/12/14 Packed，支持 LSB/MSB 对齐、大小端与 Bayer(RGGB/GRBG/GBRG/BGGR)彩色预览
- YUV 查看：I420/YV12/NV12/NV21/YUYV/UYVY/NV16
- 文件大小校验、偏移解析、缩放查看、导出 PNG/JPEG（支持设置 DPI）
- 图片转换：PNG/JPEG/BMP -> RAW（支持 Bayer Pattern 选择，可选灰度）或 YUV
- **批量转换**：支持多文件批量转换，进度条显示，统一参数设置，转换报告
- **转换预览**：Convert 对话框中显示原图缩略图及目标格式帧大小信息
- **输出模板命名**：支持 `{date}_{time}_{input_stem}_{width}x{height}{ext}` 模板，可在 Settings 中自定义
- **CLI 模式**：支持命令行解码 RAW/YUV→PNG/JPEG（`python -m raw_view view`）、编码 image→RAW/YUV（`convert`）、批量模式（`batch`）、启动 GUI 并打开文件
- 支持主界面拖拽打开文件，支持转换输入拖拽
- 支持多标签页 item：可同时打开多文件、独立参数、关闭单个 item
- 支持 Recent Files 最近文件列表
- Convert 输出支持默认 `out` 目录（可在 Settings 调整）与手动更改
- 内置 Help：格式排列、Packed bit 规则与示例
- 默认显示为 Fit to Window，可自行缩放
- **帧导航**：支持 RAW/YUV 多帧切换（图像下方 Prev/Next 按钮、上/下方向键快捷键），自动检测总帧数，显示帧号从 1 开始
- **缩放控件**：缩放滑块（10%-1000%）、双击图像切换 Fit/1:1
- **全屏模式**：F11 进入/退出全屏，Escape 退出
- **图像旋转/翻转**：顺时针/逆时针旋转 90°（Ctrl+R / Ctrl+Shift+R）、水平/垂直翻转
- **标签页切换**：Ctrl+Tab / Ctrl+Shift+Tab 或右键菜单切换多文件标签页

## 安装

```bash
pip install -r requirements.txt
```

## 运行

### GUI 模式

```bash
python -m raw_view
python -m raw_view view    # same as above
```

### CLI View 模式（RAW/YUV → PNG/JPEG）

```bash
# RAW → PNG，指定所有参数
python -m raw_view view \
    -i input.raw -o output.png \
    --target RAW \
    --raw-type RAW12 \
    --width 1920 --height 1080 \
    --alignment msb \
    --endianness little \
    --preview-mode "Bayer Color" \
    --bayer-pattern RGGB

# YUV → JPEG
python -m raw_view view \
    -i input.yuv -o output.jpg \
    --target YUV \
    --yuv-type NV12 \
    --width 1280 --height 720

# 仅指定输入，自动生成输出到 view_out/ 目录
python -m raw_view view -i input.raw --target RAW --width 1920 --height 1080

# 灰度预览输出
python -m raw_view view -i input.raw -o gray.png --target RAW --preview-mode Grayscale

# 无 -i 时启动交互式 GUI（可同时打开文件）
python -m raw_view view
python -m raw_view view file1.raw file2.png
python -m raw_view file1.raw file2.png
```

### CLI 转换模式（Image → RAW/YUV）

```bash
# 单文件转换（所有参数）
python -m raw_view convert \
    -i image.png \
    -o output.raw \
    --target RAW \
    --raw-type RAW12 \
    --width 1920 --height 1080 \
    --alignment msb \
    --endianness little \
    --source-mode bayer \
    --bayer-pattern RGGB

# 转换为 YUV
python -m raw_view convert \
    -i image.png \
    --target YUV \
    --yuv-type NV12 \
    --width 640 --height 480

# 仅指定输入，自动生成输出到 convert_out/ 目录
python -m raw_view convert -i image.jpg --target RAW --width 1920 --height 1080

# 灰度模式 + 大端
python -m raw_view convert -i image.png --target RAW --source-mode gray --endianness big
```

### CLI 批量模式（支持 view + convert 混合，每文件独立参数）

```bash
python -m raw_view batch --batch-file batch.json
python -m raw_view --batch-help   # 查看 JSON 格式说明
```

`batch.json` 支持**全局默认 + 每文件覆盖**，且每个文件可独立指定 `mode`：

```json
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "yuv_type": "YUYV",
  "width": 640,
  "height": 480,
  "alignment": "lsb",
  "endianness": "little",
  "source_mode": "bayer",
  "bayer_pattern": "RGGB",
  "preview_mode": "Bayer Color",
  "offset": 0,
  "files": [
    {"input": "img1.png"},
    {
      "input": "img2.jpg",
      "output": "custom_out.raw",
      "width": 1920,
      "height": 1080,
      "alignment": "msb"
    },
    {
      "input": "img3.png",
      "target": "YUV",
      "yuv_type": "NV12",
      "width": 1280,
      "height": 720
    },
    {
      "input": "image.raw",
      "mode": "view",
      "width": 1920,
      "height": 1080,
      "bayer_pattern": "BGGR",
      "preview_mode": "Grayscale"
    },
    {
      "input": "video.yuv",
      "mode": "view",
      "target": "YUV",
      "yuv_type": "NV12",
      "width": 1920,
      "height": 1080
    }
  ]
}
```

- `files[].output` 可选，省略时自动生成
- 每文件可通过 `"mode": "view"` 或 `"mode": "convert"` 指定操作
- 每文件可覆盖任意字段（`target`, `width`, `height`, `raw_type`, `yuv_type`, `alignment`, `endianness`, `source_mode`, `bayer_pattern`, `preview_mode`, `offset`）

## 设置（Settings）

- `Default convert output folder`：转换默认输出子目录名（默认 `out`）
- `Output filename template`：输出文件命名模板（默认 `{date}_{time}_{input_stem}_{width}x{height}{ext}`），支持占位符：`{date}` `{time}` `{input_stem}` `{width}` `{height}` `{ext}`
- `Saved image DPI`：导出 PNG/JPEG 的目标 DPI（默认 300）
- `UI font size`：主界面字体大小（默认 13）
- `UI theme`：界面主题（`Light` / `Dark`，基于 QDarkStyle + 自定义样式）
- 工具栏图标：基于 QtAwesome Font Awesome 图标集（PyQt5 兼容）

## 默认参数

- RAW 格式默认：RAW12、MSB 对齐、2560×1440
- YUV 格式默认：YUYV
- 帧号显示与状态栏均从 1 开始计数

## 打包为 EXE

详见：`docs/release_exe.md`

## 后续功能扩展建议

详见：`docs/future_extensions.md`

## 测试

```bash
python -m unittest discover -s tests -q
```
