"""把 I420（YUV 4:2:0 三平面）二进制解码成 PNG。

I420 与 NV12 同属 4:2:0，但色度是**两个独立平面**而不是交错：
文件顺序 = 完整 Y 平面 → 完整 U 平面 → 完整 V 平面，每个色度平面 h/2 x w/2。

这也是 V4L2 / FFmpeg 里最常用的格式之一。宽高都必须为偶数。

输入：自动生成 i420.yuv（640x480）
输出：output/i420.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_i420_420_planar_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/i420.yuv"
OUTPUT_FILE = "output/i420.png"
WIDTH = 640        # 4:2:0 要求宽高均为偶数
HEIGHT = 480
YUV_TYPE = "I420"  # 同族可换：YV12 / NV12 / NV21

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

    if WIDTH % 2 or HEIGHT % 2:
        print(f"错误：4:2:0 要求宽高均为偶数，当前 {WIDTH}x{HEIGHT}", file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/2：生成测试输入（I420，Y/U/V 三平面）")
    size = _generate.write_i420(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 3 // 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "I420 解码")
    common.report_output(output_path)
    print("\nI420 与 NV12 演示了同一个 4:2:0 家族的两种排列：")
    print("  三平面 (I420/YV12)  vs  半平面 (NV12/NV21)")

    print("\n" + common.expected_view_hint("yuv_420"))


if __name__ == "__main__":
    main()
