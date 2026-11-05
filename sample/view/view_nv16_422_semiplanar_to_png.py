"""把 NV16（YUV 4:2:2 半平面）二进制解码成 PNG。

与 YUYV 同属 4:2:2（色度水平减半），但**排列不同**：
NV16 = 一整个 Y 平面，后面跟一整个交错 UV 平面；
YUYV = 每 4 字节一个宏像素（Y0 U Y1 V）交织在一起。

两者总字节数相同，可直接互转（见 sample/convert/convert_nv16_to_yuyv_lossless.py）。

输入：自动生成 nv16.yuv（640x480）
输出：output/nv16.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_nv16_422_semiplanar_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/nv16.yuv"
OUTPUT_FILE = "output/nv16.png"
WIDTH = 640        # 4:2:2 要求宽度为偶数
HEIGHT = 480
YUV_TYPE = "NV16"  # 同族可换：NV61 / YUYV / YVYU / UYVY / VYUY

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

    if WIDTH % 2:
        print(f"错误：4:2:2 要求宽度为偶数，当前 WIDTH={WIDTH}", file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/2：生成测试输入（NV16，Y 平面 + 交错 UV 平面）")
    size = _generate.write_nv16(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "NV16 解码")
    common.report_output(output_path)
    print("\nNV16 与 YUYV 的字节数完全相同，只是排列不同——所以能无损互转。")

    print("\n" + common.expected_view_hint("yuv_422"))


if __name__ == "__main__":
    main()
