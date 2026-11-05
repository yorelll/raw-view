"""YUV 家族内部无损互转：I420 → NV21（4:2:0，不缩放）。

I420 三平面（Y + U + V），NV21 半平面（Y + 交错 VU）。色度采样相同，
只是 U/V 顺序与组织不同 → 逐字节可逆。

注意 NV21 是 **VU** 交错（NV12 是 UV），这是两者唯一的区别——也最容易搞错。

输入：自动生成 i420.yuv（640x480）
输出：output/i420_to_nv21.nv21"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_i420_to_nv21_lossless.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/i420.yuv"
OUTPUT_FILE = "output/i420_to_nv21.nv21"
WIDTH = 640            # 源帧尺寸（不缩放）；宽高均须为偶数
HEIGHT = 480
INPUT_YUV_TYPE = "I420"
YUV_TYPE = "NV21"
ROUNDTRIP_FILE = "output/i420_to_nv21_roundtrip.i420"

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

    common.banner("步骤 1/4：生成测试输入（I420 三平面）")
    _generate.write_i420(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)

    common.banner(f"步骤 2/4：I420 -> {YUV_TYPE}（NV21 是 VU 交错）")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "--input-type", "yuv",
        "--input-yuv-type", INPUT_YUV_TYPE,
        "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"I420 -> {YUV_TYPE}")
    common.report_output(output_path)
    print("上面的逐字节比对就是验证：回转文件与原文件完全一致即正确。")

    common.banner(f"步骤 3/4：把 {YUV_TYPE} 转回 I420")
    result = common.run_cli(
        cli, "convert",
        "-i", output_path, "-o", roundtrip_path,
        "--input-type", "yuv",
        "--input-yuv-type", YUV_TYPE,
        "--yuv-type", INPUT_YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} -> I420")

    common.banner("步骤 4/4：逐字节比对")
    if input_path.read_bytes() == roundtrip_path.read_bytes():
        print("     完全一致 —— I420 <-> NV21 逐字节可逆。")
    else:  # pragma: no cover
        print("     警告：回转结果不一致！", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
