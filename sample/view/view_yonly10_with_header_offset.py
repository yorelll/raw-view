"""把「带文件头 + 多帧」的 YOnly10 二进制解码成 PNG（演示 --offset 与多帧）。

真实设备常在帧数据前放一段头（时间戳、曝光参数、行号……）。若直接按 0 偏移
解码，整幅图会错位成"斜纹垃圾"。用 ``--offset`` 跳过头部即可。

本脚本还顺带演示多帧文件：
* ``view`` 只解**第一帧**（CLI 会打印 Frames found / decoding frame 1 only）
* 要把所有帧都拿出来，请用 batch 或先转码

输入：自动生成 yonly10_header.bin（128 字节头 + 3 帧 640x480，每帧亮度不同）
输出：output/yonly10_header.png（第一帧）"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_yonly10_with_header_offset.py
# 3. 输出在 output/ 目录；对比"加 offset"与"不加 offset"两张图，差异一目了然
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yonly10_header.bin"
OUTPUT_FILE = "output/yonly10_header.png"
OUTPUT_FILE_NO_OFFSET = "output/yonly10_header_no_offset.png"  # 对照组
WIDTH = 640
HEIGHT = 480
OFFSET = 128           # 头部字节数；不对就调这里
FRAMES = 3             # 只影响生成多少个测试帧
YUV_TYPE = "YOnly10"

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
    control_path = here / OUTPUT_FILE_NO_OFFSET
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    common.banner(f"步骤 1/3：生成 {OFFSET} 字节头 + {FRAMES} 帧的测试文件")
    size = _generate.write_multiframe_with_header(
        input_path, WIDTH, HEIGHT, frames=FRAMES, header_bytes=OFFSET
    )
    common.report_output(input_path)
    print(f"     共 {size} 字节 = 头 {OFFSET} + {FRAMES} 帧 x "
          f"{WIDTH * HEIGHT * 2} 字节")


    common.banner("步骤 2/3：先做「错误示范」——不加 --offset")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", control_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "不加 offset 解码（对照组）")
    common.report_output(control_path)
    print("     这张图是错位的——头部被当成了像素数据。")

    common.banner(f"步骤 3/3：加上 --offset {OFFSET} 正确解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--offset", OFFSET,
    )
    common.check_ok(result, "带 offset 解码")
    common.report_output(output_path)
    print(f"\n对比两张图：{OUTPUT_FILE} 应为平滑渐变（第 1 帧），")
    print(f"{OUTPUT_FILE_NO_OFFSET} 应为错位的花屏。")


if __name__ == "__main__":
    main()
