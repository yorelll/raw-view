"""YUV 家族内部无损互转：NV12 → I420（4:2:0，不缩放）。

NV12 是半平面（Y 平面 + 交错 UV），I420 是三平面（Y + U + V 分开）。
两者色度采样完全相同，只是平面组织不同 → 逐字节可逆。

宽高都必须为偶数。

输入：自动生成 nv12.yuv（640x480）
输出：output/nv12_to_i420.i420（并回转验证）"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_nv12_to_i420_lossless.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/nv12.yuv"
OUTPUT_FILE = "output/nv12_to_i420.i420"
WIDTH = 640            # 源帧尺寸（不缩放）；4:2:0 要求宽高均为偶数
HEIGHT = 480
INPUT_YUV_TYPE = "NV12"
YUV_TYPE = "I420"
ROUNDTRIP_FILE = "output/nv12_to_i420_roundtrip.nv12"

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
    roundtrip_path = here / ROUNDTRIP_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    if WIDTH % 2 or HEIGHT % 2:
        print(f"错误：4:2:0 要求宽高均为偶数，当前 {WIDTH}x{HEIGHT}",
              file=sys.stderr)
        raise SystemExit(1)
    if INPUT_YUV_TYPE.upper() == YUV_TYPE.upper():
        print(f"错误：输入与输出布局相同（{YUV_TYPE}），这是 no-op。",
              file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/4：生成测试输入（NV12）")
    _generate.write_nv12(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner(f"步骤 2/4：NV12 -> {YUV_TYPE}")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "--input-type", "yuv",
        "--input-yuv-type", INPUT_YUV_TYPE,
        "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"NV12 -> {YUV_TYPE}")
    common.report_output(output_path)
    print("上面的逐字节比对就是验证：回转文件与原文件完全一致即正确。")
    print(f"     期望大小 {WIDTH * HEIGHT * 3 // 2} 字节（1.5 字节/像素）")

    common.banner(f"步骤 3/4：把 {YUV_TYPE} 转回 NV12")
    result = common.run_cli(
        cli, "convert",
        "-i", output_path, "-o", roundtrip_path,
        "--input-type", "yuv",
        "--input-yuv-type", YUV_TYPE,
        "--yuv-type", INPUT_YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} -> NV12")

    common.banner("步骤 4/4：逐字节比对")
    if input_path.read_bytes() == roundtrip_path.read_bytes():
        print("     完全一致 —— NV12 <-> I420 逐字节可逆。")
    else:  # pragma: no cover
        print("     警告：回转结果不一致！", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
