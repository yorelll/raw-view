"""把 NV12（YUV 4:2:0 半平面）二进制解码成 PNG。

4:2:0 表示色度在**水平和垂直方向都减半**：每 2x2 个像素共用一组 U/V。
因此宽度和高度**都必须是偶数**。NV12 是一整个 Y 平面 + 交错 UV 平面，
1.5 字节/像素——这是相机、视频编码里最常见的 YUV 格式之一。

输入：自动生成 nv12.yuv（640x480）
输出：output/nv12.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_nv12_420_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/nv12.yuv"
OUTPUT_FILE = "output/nv12.png"
WIDTH = 640        # 4:2:0 要求宽高均为偶数
HEIGHT = 480
YUV_TYPE = "NV12"  # 同族可换：NV21 / I420 / YV12

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

    common.banner("步骤 1/2：生成测试输入（NV12，1.5 字节/像素）")
    size = _generate.write_nv12(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 3 // 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "NV12 解码")
    common.report_output(output_path)
    print("\nNV12 是 1.5 字节/像素：Y 全分辨率 + 色度 1/4 分辨率。")
    print("同族还有 NV21 / I420 / YV12，只差平面顺序，改 YUV_TYPE 即可。")

    print("\n" + common.expected_view_hint("yuv_420"))


if __name__ == "__main__":
    main()
