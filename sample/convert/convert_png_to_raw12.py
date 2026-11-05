"""把 PNG 图片转换成 RAW12 二进制（Bayer RGGB 马赛克）。

这是"我有一张图，想喂给 ISP / 仿真器当传感器输入"的最常见需求。
``--width/--height`` 在这里是**输出尺寸**：图片会被缩放（与 view 相反）。

输入：脚本自动生成的 photo.png（640x480 彩条 + 渐变）
输出：output/photo_raw12.raw（640x480，每像素 2 字节）

公式：输出大小 = 宽 x 高 x 2 字节。"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_png_to_raw12.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：把 INPUT_FILE 指向你的图片即可。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/photo.png"
OUTPUT_FILE = "output/photo_raw12.raw"
WIDTH = 640          # 输出尺寸（会缩放图片）
HEIGHT = 480
RAW_TYPE = "RAW12"   # RAW8 / RAW10 / RAW12 / RAW16 / RAW32 / "RAW10 Packed" ...
ALIGNMENT = "msb"
ENDIANNESS = "little"
SOURCE_MODE = "bayer"   # bayer = 生成 Bayer 马赛克；gray = 直接转灰度
BAYER_PATTERN = "RGGB"  # 仅 SOURCE_MODE="bayer" 时有效

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

    common.banner("步骤 1/2：生成测试输入图片（PNG 彩条）")
    _generate.write_png(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner("步骤 2/2：调用 CLI 转换成 RAW12")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
        "--source-mode", SOURCE_MODE,
        "--bayer-pattern", BAYER_PATTERN,
    )
    common.check_ok(result, "PNG -> RAW12")
    common.report_output(output_path)
    print(f"\n期望大小 {WIDTH * HEIGHT * 2} 字节（RAW12 每像素 2 字节）。")
    print("想看转换回来的效果？运行 sample/view/view_raw12_bayer_color_rggb.py")


if __name__ == "__main__":
    main()
