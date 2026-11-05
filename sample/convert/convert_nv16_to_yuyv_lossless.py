"""YUV 家族内部无损互转：NV16 → YUYV（4:2:2，不缩放）。

**这不是重新采样，也不是缩放**：两种格式描述完全相同的图像数据，只是字节
排列不同。因此转换是逐字节可逆的（把输出再转回去，能与原文件逐字节相同）。

关键区别（最容易踩的坑）：
* ``--width/--height`` 是**源帧尺寸**，必须填对，且**不会**缩放图片
* ``--input-yuv-type`` 说明**输入**布局，``--yuv-type`` 说明**输出**布局
* 两者不能相同（否则是 no-op，CLI 会拒绝）

输入：自动生成 nv16.yuv（640x480）
输出：output/nv16_to_yuyv.yuyv"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_nv16_to_yuyv_lossless.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」的 INPUT_YUV_TYPE / YUV_TYPE。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/nv16.yuv"
OUTPUT_FILE = "output/nv16_to_yuyv.yuyv"
WIDTH = 640            # 源帧尺寸（不缩放！）
HEIGHT = 480
INPUT_YUV_TYPE = "NV16"   # 输入布局
YUV_TYPE = "YUYV"         # 输出布局（不能与输入相同）
ROUNDTRIP_FILE = "output/nv16_to_yuyv_roundtrip.nv16"  # 回转验证输出

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

    if INPUT_YUV_TYPE.upper() == YUV_TYPE.upper():
        print(f"错误：输入与输出布局相同（{YUV_TYPE}），这是 no-op。请改一个。",
              file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/4：生成测试输入（NV16）")
    _generate.write_nv16(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    original_size = input_path.stat().st_size

    common.banner(f"步骤 2/4：NV16 -> {YUV_TYPE}（无损重排）")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "--input-type", "yuv",
        "--input-yuv-type", INPUT_YUV_TYPE,
        "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"NV16 -> {YUV_TYPE}")
    common.report_output(output_path)
    print("上面的逐字节比对就是验证：回转文件与原文件完全一致即正确。")
    converted_size = output_path.stat().st_size
    print(f"     源 {original_size} 字节 / 目标 {converted_size} 字节"
          f"（{'相同，符合无损重排' if original_size == converted_size else '不同！'}）")

    common.banner(f"步骤 3/4：把 {YUV_TYPE} 转回 NV16，验证可逆")
    result = common.run_cli(
        cli, "convert",
        "-i", output_path, "-o", roundtrip_path,
        "--input-type", "yuv",
        "--input-yuv-type", YUV_TYPE,
        "--yuv-type", INPUT_YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} -> NV16")
    common.report_output(roundtrip_path)

    common.banner("步骤 4/4：逐字节比对原文件与回转文件")
    if input_path.read_bytes() == roundtrip_path.read_bytes():
        print(f"     完全一致 —— 证明 {INPUT_YUV_TYPE} <-> {YUV_TYPE} 是逐字节可逆的。")
    else:  # pragma: no cover - 出现即说明实现有问题
        print("     警告：回转结果与原文件不一致！", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
