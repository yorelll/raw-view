"""把 RAW8 二进制解码成 PNG（最简单的 RAW 格式）。

RAW8 就是 1 字节/像素的原生 8 bit 数据，没有对齐/字节序的概念——可以把它
理解成"没有文件头的灰度 PNG"。适合第一次接触 RAW 的人。

输入：自动生成 raw8.bin（640x480 渐变）
输出：output/raw8.png
"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_raw8_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/raw8.bin"
OUTPUT_FILE = "output/raw8.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW8"

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

    common.banner("步骤 1/2：生成测试输入（RAW8，1 字节/像素）")
    size = _generate.write_raw8(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--preview-mode", "Grayscale",
    )
    common.check_ok(result, "RAW8 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("raw_8bit"))


if __name__ == "__main__":
    main()
