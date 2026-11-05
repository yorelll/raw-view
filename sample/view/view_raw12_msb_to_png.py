"""把 RAW12（MSB 对齐、小端）二进制解码成 PNG。

这是最常见的传感器 RAW 格式：每个像素 12 bit，存在 16-bit 字里，有效位在
**高 12 位**（MSB 对齐），字节序小端。

输入：脚本自动生成的 raw12_msb.bin（640x480，双向渐变图案）
输出：output/raw12_msb.png

运行后打开 PNG，应看到"左暗右亮、上暗下亮"的平滑渐变。若出现斜纹、错行或
全黑，说明宽度或对齐方式与文件不符。
"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时打印指引）
# 2. 在本目录运行：python view_raw12_msb_to_png.py
# 3. 输出会生成在 output/ 目录
#
# 如果你要改用自己的文件，修改下面的「配置区」即可。

# ============================================================
# 配置区（用户通常只需要改这里）
# ============================================================
CLI = None          # None = 自动查找 dist/raw-view/raw-view-cli.exe
INPUT_FILE = "input/raw12_msb.bin"
OUTPUT_FILE = "output/raw12_msb.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW12"  # RAW8 / RAW10 / RAW12 / RAW16 / RAW32 / "RAW10 Packed" ...
ALIGNMENT = "msb"   # msb = 有效位在高位；lsb = 有效位在低位
ENDIANNESS = "little"  # little / big

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

    common.banner("步骤 1/2：生成测试输入（RAW12, MSB 对齐, 小端）")
    size = _generate.write_raw12(
        input_path, WIDTH, HEIGHT, alignment=ALIGNMENT, endianness=ENDIANNESS
    )
    common.report_output(input_path)
    expected = WIDTH * HEIGHT * 2
    print(f"     期望大小 {expected} 字节 / 实际 {size} 字节"
          f"（RAW12 每像素 2 字节）")

    common.banner("步骤 2/2：调用 CLI 解码成 PNG")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
        "--preview-mode", "Grayscale",   # 本例输入是渐变，灰度预览最直观
    )
    common.check_ok(result, "RAW12 解码")
    common.report_output(output_path)
    print("\n完成。打开该 PNG 应看到平滑的双向渐变。")

    print("\n" + common.expected_view_hint("raw_msb"))


if __name__ == "__main__":
    main()
