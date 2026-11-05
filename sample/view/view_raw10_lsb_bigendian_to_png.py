"""把 RAW10（LSB 对齐、**大端**）二进制解码成 PNG。

与 ``view_raw12_msb_to_png.py`` 的区别只在两个参数：
``--alignment lsb``（10 bit 有效位放在 16-bit 字的**低** 10 位）和
``--endianness big``（高字节在前）。

对齐/字节序写错是"画面偏亮、发灰、有台阶"最常见的原因——比较本脚本与
MSB 版本的输出即可看出差别。

输入：自动生成 raw10_lsb_be.bin（640x480 渐变）
输出：output/raw10_lsb_be.png
"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_raw10_lsb_bigendian_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/raw10_lsb_be.bin"
OUTPUT_FILE = "output/raw10_lsb_be.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW10"
ALIGNMENT = "lsb"      # ← 本例的关键：有效位在低 10 位
ENDIANNESS = "big"     # ← 本例的关键：大端

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

    common.banner("步骤 1/2：生成测试输入（RAW10, LSB 对齐, 大端）")
    _generate.write_raw10(
        input_path, WIDTH, HEIGHT, alignment=ALIGNMENT, endianness=ENDIANNESS
    )
    common.report_output(input_path)

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
        "--preview-mode", "Grayscale",
    )
    common.check_ok(result, "RAW10 LSB 大端解码")
    common.report_output(output_path)
    print("\n提示：把 ALIGNMENT 改成 'msb' 再运行一次，输出的图会明显偏亮/发白——")
    print("      这就是对齐方式写错时的典型表现。")

    print("\n" + common.expected_view_hint("raw_lsb"))


if __name__ == "__main__":
    main()
