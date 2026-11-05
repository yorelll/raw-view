"""把 RAW16 二进制解码成 PNG（满量程 16 bit）。

RAW16 每像素占满 16 bit（0..65535），只有字节序可选，没有位对齐概念——
16 bit 正好填满一个 16-bit 字。

输入：自动生成 raw16.bin（640x480 渐变）
输出：output/raw16.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时打印指引）
# 2. 在本目录运行：python view_raw16_to_png.py
# 3. 输出会生成在 output/ 目录
#
# 如果你要改用自己的文件，修改下面的「配置区」即可。

# ============================================================
# 配置区（用户通常只需要改这里）
# ============================================================
CLI = None
INPUT_FILE = "input/raw16.bin"
OUTPUT_FILE = "output/raw16.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW16"
ENDIANNESS = "little"

# ============================================================
# 以下为实现（用户通常不需要修改）
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

    common.banner("步骤 1/2：生成测试输入（RAW16）")
    _generate.write_raw16(input_path, WIDTH, HEIGHT, endianness=ENDIANNESS)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--endianness", ENDIANNESS,
        "--preview-mode", "Grayscale",
    )
    common.check_ok(result, "RAW16 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("raw_msb"))


if __name__ == "__main__":
    main()
