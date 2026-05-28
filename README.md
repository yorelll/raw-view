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
- 支持主界面拖拽打开文件、拖拽文件夹自动扫描 RAW/YUV 文件、拖入时高亮窗口边框视觉反馈
- 支持转换输入拖拽
- 支持多标签页 item：可同时打开多文件、独立参数、关闭单个 item
- 支持 Recent Files 最近文件列表
- Convert 输出支持默认 `convert_out` 目录（可在 Settings 调整）与手动更改
- 内置 Help：格式排列、Packed bit 规则与示例
- 默认显示为 Fit to Window，可自行缩放
- **帧导航**：支持 RAW/YUV 多帧切换（图像下方 Prev/Next 按钮、上/下方向键快捷键），自动检测总帧数，显示帧号从 1 开始
- **缩放控件**：缩放滑块（10%-1000%）、双击图像切换 Fit/1:1
- **全屏模式**：F11 进入/退出全屏，Escape 退出
- **图像旋转/翻转**：顺时针/逆时针旋转 90°（Ctrl+R / Ctrl+Shift+R）、水平/垂直翻转
- **标签页切换**：Ctrl+Tab / Ctrl+Shift+Tab 或右键菜单切换多文件标签页
- **现代主题**：卡片式设计（圆角 12px、阴影）、Material Design 色系、Light/Dark 双主题、选项卡与菜单圆角风格
- **日志系统**：文件日志（RotatingFileHandler，最大 5MB，保留 3 份）+ 控制台日志，记录解码错误、转换异常、崩溃信息
- **Sensor 预设（一键 apply）**：可把任意一组解码参数（type / format / alignment / endianness / preview / Bayer / width / height / offset）保存为命名预设；下次打开 RAW 时只需在面板顶部下拉框中选中，即可自动填充所有字段并立刻渲染。支持在 Settings → *Manage sensor presets* 或主面板的 *Manage* 按钮中新增 / 重命名 / 编辑 / 删除
- **RAW Packed 标准布局**：RAW10P / RAW12P / RAW14P 的解码与编码均遵循 MIPI CSI-2 标准布局（B0..Bn-1 = 高 8bit，末字节 = LSBs，MSB-first），与真实 sensor 数据互通

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
- `Manage sensor presets`：打开 Sensor 预设管理对话框（详见下文 *Sensor 预设*）
- 工具栏图标：基于 QtAwesome Font Awesome 图标集（PyQt5 兼容）

## Sensor 预设（一键 apply）

为了避免每次打开新 sensor 的 raw 文件都要重复填写一整套参数，主面板顶部提供了 **Preset** 行：

- **下拉框**：列出所有已保存的预设；选中后立即把所有字段写入面板并自动 Apply（如果当前已打开文件）。
- **Save 按钮**：把面板当前的所有字段（type、format、alignment、endianness、RAW preview、Bayer pattern、width、height、offset）保存为命名预设；同名时会询问是否覆盖。
- **Manage 按钮**：打开预设管理对话框，可新增 / 重命名 / 编辑 / 删除任意预设。

未打开文件时也可以通过 **Settings → Manage sensor presets** 进入相同的管理对话框。

### 存储位置

预设以 JSON 形式存储在 **QSettings**（不在仓库 / 不在 exe 内部）：

| 平台 | 位置 |
|---|---|
| Windows | 注册表 `HKEY_CURRENT_USER\Software\yorelll\raw-view`，键 `presets\sensors` |
| macOS | `~/Library/Preferences/com.yorelll.raw-view.plist` |
| Linux | `~/.config/yorelll/raw-view.conf` |

跨会话自动保留，重启 / 升级 exe 后仍然存在；卸载 / 删除注册表键后会丢失。

### Import / Export（团队共享）

Preset 管理对话框底部提供 **Import** 与 **Export** 两个按钮：

- **Export**：把当前所有预设序列化为 JSON 文件（默认文件名 `raw-view-presets.json`），可发给同事或随 exe 一起分发；导出包含尚未保存的对话框内编辑。
- **Import**：选择 JSON 文件后，逐条合并到本机预设。若发现重名，弹窗询问 **Overwrite** / **Skip duplicates** / **Cancel**。导入完成后还需要点 **Save** 才会真正写入注册表/配置文件。

**只需要导入一次**：写入注册表后跟用户自己手动 Save 出来的预设没有区别，下次打开 exe 仍在；用户也可以继续新增、修改自己的预设。
重新导入只在换电脑、重装系统、或想恢复"团队标准版本"时才需要。

> 注意：导出的 `*.json` 文件已经加入 `.gitignore`（`raw-view-presets*.json` / `*sensor-presets*.json` 等），默认不会被 commit。打包发布的 exe 也**不会**带上这个 JSON——团队预设需要单独分发，详见 `docs/release_exe.md`。

> 示例：创建 `401ai`：Type=RAW、Format=RAW10 Packed、Alignment=msb、Endianness=little、RAW preview=Bayer Color、Bayer pattern=BGGR、Width=2560、Height=1440、Offset=0。

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
