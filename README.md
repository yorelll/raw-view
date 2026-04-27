# raw-view

Python RAW/YUV 图像查看与格式转换工具。

## 功能

- RAW 查看：RAW8/10/12/16/32、RAW10/12/14 Packed，支持 LSB/MSB 对齐、大小端与 Bayer(RGGB/GRBG/GBRG/BGGR)彩色预览
- YUV 查看：I420/YV12/NV12/NV21/YUYV/UYVY/NV16
- 文件大小校验、偏移解析、缩放查看、导出 PNG/JPEG（支持设置 DPI）
- 图片转换：PNG/JPEG/BMP -> RAW（支持 Bayer Pattern 选择，可选灰度）或 YUV
- 支持主界面拖拽打开文件，支持转换输入拖拽
- 支持多标签页 item：可同时打开多文件、独立参数、关闭单个 item
- 支持 Recent Files 最近文件列表
- Convert 输出支持默认 `out` 目录（可在 Settings 调整）与手动更改
- 内置 Help：格式排列、Packed bit 规则与示例
- 默认显示为 Fit to Window，可自行缩放

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python -m raw_view
```

## 设置（Settings）

- `Default convert output folder`：转换默认输出子目录名（默认 `out`）
- `Saved image DPI`：导出 PNG/JPEG 的目标 DPI（默认 300）
- `UI font size`：主界面字体大小（默认 13）
- `UI theme`：界面主题（`Light` / `Dark`，基于 QDarkStyle + 自定义样式）
- 工具栏图标：基于 QtAwesome Font Awesome 图标集（PyQt5 兼容）

## 打包为 EXE

详见：`docs/release_exe.md`

## 后续功能扩展建议

详见：`docs/future_extensions.md`

## 测试

```bash
python -m unittest discover -s tests -q
```
