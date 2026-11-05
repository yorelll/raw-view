"""把 RAW12（LSB 对齐、大端）二进制解码成 PNG。

LSB 对齐表示 12 bit 有效位放在 16-bit 字的**低** 12 位；大端表示高字节在前。
这两项最容易搞反，单列一个脚本便于对照。

输入：自动生成 raw12_lsb_be.bin（640x480 渐变）
输出：output/raw12_lsb_be.png

提示：把 ALIGNMENT 改成 "msb" 再运行，画面会明显偏亮——这就是对齐写错的表现。"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时打印指引）
# 2. 在本目录运行：python view_raw12_lsb_bigendian_to_png.py
# 3. 输出会生成在 output/ 目录
#
# 如果你要改用自己的文件，修改下面的「配置区」即可。

# ============================================================
# 配置区（用户通常只需要改这里）
# ============================================================
CLI = None
INPUT_FILE = "input/raw12_lsb_be.bin"
OUTPUT_FILE = "output/raw12_lsb_be.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW12"
ALIGNMENT = "lsb"      # ← 本例关键：有效位在低 12 位
ENDIANNESS = "big"     # ← 本例关键：大端

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

    common.banner("步骤 1/2：生成测试输入（RAW12, LSB 对齐, 大端）")
    _generate.write_raw12(
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
    common.check_ok(result, "RAW12 LSB 大端解码")
    common.report_output(output_path)
    print("\n提示：把 ALIGNMENT 改成 msb 再运行，画面会明显偏亮。")

    print("\n" + common.expected_view_hint("raw_lsb"))


if __name__ == "__main__":
    main()
