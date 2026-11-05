"""把 YUYV（YUV 4:2:2 打包）二进制解码成 PNG。

4:2:2 表示色度在**水平方向**减半采样：每 2 个像素共用一组 U/V。
YUYV 是其中最常见的打包排列：每 4 个字节 = Y0 U Y1 V。

要换其它 4:2:2 布局（YVYU / UYVY / VYUY / NV16 / NV61），只需改 YUV_TYPE。

输入：自动生成 yuyv.yuv（640x480，Y 渐变 + 左右不同色度）
输出：output/yuyv.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python view_yuyv_422_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yuyv.yuv"
OUTPUT_FILE = "output/yuyv.png"
WIDTH = 640        # 4:2:2 要求宽度为偶数
HEIGHT = 480
YUV_TYPE = "YUYV"  # 可换：YVYU / UYVY / VYUY / NV16 / NV61

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

    common.banner("步骤 1/2：生成测试输入（YUYV，2 字节/像素）")
    size = _generate.write_yuyv(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, "YUYV 解码")
    common.report_output(output_path)
    print("\n打开 PNG 应看到亮度渐变，左右两半色度不同。")
    print("把 YUV_TYPE 改成 NV16 再运行：NV16 是半平面布局（Y 平面 + 交错 UV），")
    print("字节总数相同但排列不同——这是 4:2:2 家族互换的典型例子。")

    print("\n" + common.expected_view_hint("yuv_422"))


if __name__ == "__main__":
    main()
