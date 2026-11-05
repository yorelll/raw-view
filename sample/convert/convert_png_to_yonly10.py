"""把 PNG 图片转换成 YOnly10（YUV 4:0:0，10 bit 灰度）。

YOnly 系列是"只有亮度"的 YUV：等价于全分辨率灰度，常用于红外 / 单色相机。
10/12/14/16 bit 版本存在 16-bit 字里（2 字节/像素），受 ``--alignment`` 与
``--endianness`` 控制。

输入：自动生成 photo.png（640x480）
输出：output/photo_yonly10.yuv"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_png_to_yonly10.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：把 INPUT_FILE 指向你的图片即可。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/photo.png"
OUTPUT_FILE = "output/photo_yonly10.yuv"
WIDTH = 640
HEIGHT = 480
YUV_TYPE = "YOnly10"   # YOnly8 / YOnly10 / YOnly12 / YOnly14 / YOnly16
ALIGNMENT = "msb"      # 只对 10/12/14/16 有效
ENDIANNESS = "little"

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

    common.banner("步骤 1/2：生成测试输入图片")
    _generate.write_png(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner("步骤 2/2：调用 CLI 转换成 YOnly10")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
    )
    common.check_ok(result, "PNG -> YOnly10")
    common.report_output(output_path)
    print("想验证？运行 sample/view/view_yonly10_yuv_to_png.py 回读这个 .yuv。")
    print(f"\n期望大小 {WIDTH * HEIGHT * 2} 字节（2 字节/像素）。")
    print("注意：YOnly8 是 1 字节/像素且忽略 alignment/endianness，")
    print("     把 YUV_TYPE 改成 YOnly8 时这两个参数会被忽略。")


if __name__ == "__main__":
    main()
