"""把 PNG 图片转换成 RAW8 二进制（1 字节/像素，最简单）。

RAW8 就是不带文件头的 8 bit 灰度数据——调试链路时最省事的目标格式。
``--source-mode gray`` 表示直接取亮度，不做 Bayer 马赛克。

输入：自动生成 photo.png（640x480）
输出：output/photo_raw8.raw（640x480，每像素 1 字节）"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_png_to_raw8.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：把 INPUT_FILE 指向你的图片即可。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/photo.png"
OUTPUT_FILE = "output/photo_raw8.raw"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW8"
SOURCE_MODE = "gray"   # gray = 纯灰度；bayer = 生成马赛克（RAW8 也可）

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

    common.banner("步骤 2/2：调用 CLI 转换成 RAW8")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--source-mode", SOURCE_MODE,
    )
    common.check_ok(result, "PNG -> RAW8")
    common.report_output(output_path)
    print(f"\n期望大小 {WIDTH * HEIGHT} 字节（1 字节/像素）。")
    print("想验证？运行 sample/view/view_raw8_to_png.py 看回读效果（同一种渐变图，肉眼可判断）。")


if __name__ == "__main__":
    main()
