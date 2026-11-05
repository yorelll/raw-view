"""把 PNG 图片转换成 RAW10 Packed 二进制（MIPI CSI-2 打包格式）。

Packed 格式节省存储：4 个 10-bit 像素压进 5 个字节（1.25 字节/像素），
而"未打包"的 RAW10 要占 2 字节/像素。

**输出宽度必须是 4 的倍数**，否则 CLI 会拒绝（脚本先检查）。

输入：自动生成 photo.png（640x480）
输出：output/photo_raw10_packed.raw"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_png_to_raw10_packed.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：把 INPUT_FILE 指向你的图片即可。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/photo.png"
OUTPUT_FILE = "output/photo_raw10_packed.raw"
WIDTH = 640          # 必须是 4 的倍数
HEIGHT = 480
RAW_TYPE = "RAW10 Packed"
SOURCE_MODE = "bayer"
BAYER_PATTERN = "RGGB"

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

    if WIDTH % 4:
        print(f"错误：RAW10 Packed 要求宽度是 4 的倍数，当前 WIDTH={WIDTH}",
              file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/2：生成测试输入图片")
    _generate.write_png(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner("步骤 2/2：调用 CLI 转换成 RAW10 Packed")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--source-mode", SOURCE_MODE,
        "--bayer-pattern", BAYER_PATTERN,
    )
    common.check_ok(result, "PNG -> RAW10 Packed")
    common.report_output(output_path)
    print("想验证？运行 sample/view/view_raw10_packed_to_png.py 回读这个 .raw。")
    print(f"\n期望大小 {WIDTH * HEIGHT * 5 // 4} 字节"
          f"（1.25 字节/像素，比未打包的 RAW10 省 37.5%）。")


if __name__ == "__main__":
    main()
