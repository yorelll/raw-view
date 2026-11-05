"""把 Bayer 排列的 RAW12 解码成**彩色** PNG（RGGB）。

前面的例子都是灰度预览。真实相机传感器输出的是"每个像素只有一种颜色"的
Bayer 马赛克（RGGB = 左上红、右上绿、左下绿、右下蓝），要按正确排列做去马赛克
才能还原彩色。

输入：自动生成 raw12_bayer_rggb.bin（640x480，红/绿/蓝/白竖条马赛克）
输出：output/raw12_bayer_rggb.png

提示：把 BAYER_PATTERN 改成 "BGGR" 再运行，红蓝会互换——这正是排列写错的表现。"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时打印指引）
# 2. 在本目录运行：python view_raw12_bayer_color_rggb.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/raw12_bayer_rggb.bin"
OUTPUT_FILE = "output/raw12_bayer_rggb.png"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW12"
ALIGNMENT = "msb"
ENDIANNESS = "little"
BAYER_PATTERN = "RGGB"        # RGGB / GRBG / GBRG / BGGR
PREVIEW_MODE = "Bayer Color"  # 彩色去马赛克预览

# ============================================================
# 实现
# ============================================================
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common as common
import _generate


def main() -> None:
    np = common.require_numpy()
    # 先确认 exe 存在：缺 exe 时在生成任何文件之前就失败，
    # 而不是跑完一半才发现。
    cli = common.require_cli(CLI)

    here = Path(__file__).resolve().parent
    input_path = here / INPUT_FILE
    output_path = here / OUTPUT_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    common.banner(f"步骤 1/2：生成 Bayer 马赛克测试输入（{BAYER_PATTERN}）")
    mosaic = _generate.bayer_mosaic_from_color(WIDTH, HEIGHT, BAYER_PATTERN)
    # 把 8-bit 马赛克提升到 12 bit 并按 MSB 对齐存进 16-bit 字
    values = (mosaic.astype(np.uint32) * 4095 // 255).astype(np.uint16)
    values = (values << 4).astype(np.uint16)
    dtype = "<u2" if ENDIANNESS == "little" else ">u2"
    input_path.write_bytes(values.astype(dtype).tobytes())
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节（RAW12 每像素 2 字节）")

    common.banner("步骤 2/2：调用 CLI 做彩色去马赛克")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--alignment", ALIGNMENT, "--endianness", ENDIANNESS,
        "--bayer-pattern", BAYER_PATTERN,
        "--preview-mode", PREVIEW_MODE,
    )
    common.check_ok(result, "Bayer 彩色解码")
    common.report_output(output_path)
    print("\n打开 PNG 应看到红/绿/蓝/白竖条。")
    print(f"把 BAYER_PATTERN 改成 BGGR 再运行：红蓝会对调。")


if __name__ == "__main__":
    main()
