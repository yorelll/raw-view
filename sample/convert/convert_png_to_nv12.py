"""把 PNG 图片转换成 NV12（YUV 4:2:0 半平面）。

这是"我要给视频编码器 / 硬件 ISP 喂一帧 YUV"最常见的需求。
NV12 = 1.5 字节/像素：Y 全分辨率 + 色度每 2x2 像素共用一组。

``--width/--height`` 是**输出尺寸**（会缩放）。宽高必须都是偶数。

输入：自动生成 photo.png（640x480 彩条）
输出：output/photo_nv12.yuv"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_png_to_nv12.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：把 INPUT_FILE 指向你的图片即可。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/photo.png"
OUTPUT_FILE = "output/photo_nv12.yuv"
WIDTH = 640          # 4:2:0 要求宽高均为偶数
HEIGHT = 480
YUV_TYPE = "NV12"    # 同族可换：NV21 / I420 / YV12

# ============================================================
# 实现
# ============================================================
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common as common
import _generate


def main() -> None:
    common.require_numpy()
    # 先确认 exe 存在：缺 exe 时在生成任何文件之前就失败，
    # 而不是跑完一半才发现。
    cli = common.require_cli(CLI)
    here = Path(__file__).resolve().parent
    input_path = here / INPUT_FILE
    output_path = here / OUTPUT_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    if WIDTH % 2 or HEIGHT % 2:
        print(f"错误：4:2:0 要求宽高均为偶数，当前 {WIDTH}x{HEIGHT}",
              file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/2：生成测试输入图片")
    _generate.write_png(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner("步骤 2/2：调用 CLI 转换成 NV12")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "PNG -> NV12")
    common.report_output(output_path)
    print(f"\n期望大小 {WIDTH * HEIGHT * 3 // 2} 字节（1.5 字节/像素）。")
    print("想看回读效果？运行 sample/convert/convert_yuv_to_png.py")


if __name__ == "__main__":
    main()
