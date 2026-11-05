"""把 YOnly12（YUV 4:0:0，12 bit）二进制解码成 PNG。

YOnly10 的"更高位深"版本：同样 2 字节/像素存在 16-bit 字里，只是有效位
变成 12 位。用它演示**位深差异**——把 YUV_TYPE 改成 YOnly8 会得到 1 字节/像素
（文件长度减半），改成 YOnly16 则 16 位全有效。

输入：自动生成 yonly12.bin（640x480 渐变）
输出：output/yonly12.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时也可源码运行）
# 2. 在本目录运行：python view_yonly12_yuv_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None             # None = 自动查找 exe；仓库内找不到则用源码运行
INPUT_FILE = "input/yonly12.bin"
OUTPUT_FILE = "output/yonly12.png"
WIDTH = 640
HEIGHT = 480
YUV_TYPE = "YOnly12"   # YOnly8 / YOnly10 / YOnly12 / YOnly14 / YOnly16
BITS = 12              # 与 YUV_TYPE 的位深一致
ALIGNMENT = "msb"
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
    # 先确认 CLI 可用：不可用时在生成任何文件之前就失败，
    # 而不是跑完一半才发现。
    cli = common.require_cli(CLI)
    here = Path(__file__).resolve().parent
    input_path = here / INPUT_FILE
    output_path = here / OUTPUT_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    common.banner(f"步骤 1/2：生成测试输入（{YUV_TYPE}，{BITS} bit）")
    size = _generate.write_yonly_multi_bit(
        input_path, WIDTH, HEIGHT, BITS,
        alignment=ALIGNMENT, endianness=ENDIANNESS,
    )
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节 / 实际 {size} 字节"
          f"（{BITS} bit 仍存在 16-bit 字里，故 2 字节/像素）")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
    )
    common.check_ok(result, f"{YUV_TYPE} 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("yonly"))
    print("     把 YUV_TYPE/BITS 改成 8：文件变成 1 字节/像素，且 alignment/endianness 不再生效。")


if __name__ == "__main__":
    main()
