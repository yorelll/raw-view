"""把 YOnly10（YUV 4:0:0，10 bit）二进制解码成 PNG。

YOnly 是"只有亮度、没有色度"的 YUV：等价于全分辨率灰度，所以文件里只有
一个 Y 平面。10/12/14/16 bit 版本存在 16-bit 字里，受对齐与字节序影响；
8 bit 版本（YOnly8）是 1 字节/像素，无对齐概念。

这是"相机输出的 .bin 灰度图直接看图"最常见的场景。

输入：自动生成 yonly10.bin（1296x736，双向渐变）
输出：output/yonly10.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_yonly10_yuv_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yonly10.bin"
OUTPUT_FILE = "output/yonly10.png"
WIDTH = 1296       # 你原图的实际宽度
HEIGHT = 736       # 你原图的实际高度
YUV_TYPE = "YOnly10"   # YOnly8 / YOnly10 / YOnly12 / YOnly14 / YOnly16
ALIGNMENT = "msb"      # 只对 10/12/14/16 有效
ENDIANNESS = "little"  # 只对 10/12/14/16 有效

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

    common.banner("步骤 1/2：生成测试输入（YOnly10，2 字节/像素）")
    size = _generate.write_yonly_multi_bit(
        input_path, WIDTH, HEIGHT, 10,
        alignment=ALIGNMENT, endianness=ENDIANNESS,
    )
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
    )
    common.check_ok(result, "YOnly10 解码")
    common.report_output(output_path)
    print("\n注意：这里用的是 `view -t YUV`。若你之前用 `convert` 遇到")
    print("      \"failed to read image\"，就是因为 convert 默认按 image 解码。")

    print("\n" + common.expected_view_hint("yonly"))


if __name__ == "__main__":
    main()
