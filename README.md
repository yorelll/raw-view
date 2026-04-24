# raw-view

Python RAW/YUV 图像查看与格式转换工具。

## 功能

- RAW 查看：RAW8/10/12/16/32、RAW10/12/14 Packed，支持 LSB/MSB 对齐、大小端与 Bayer(RGGB)彩色预览
- YUV 查看：I420/YV12/NV12/NV21/YUYV/UYVY/NV16
- 文件大小校验、偏移解析、缩放查看、导出 PNG/JPEG
- 图片转换：PNG/JPEG/BMP -> RAW（默认 Bayer RGGB，可选灰度）或 YUV
- 内置 Help：格式排列、Packed bit 规则与示例

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python -m raw_view
```

## 测试

```bash
python -m unittest discover -s tests -q
```
