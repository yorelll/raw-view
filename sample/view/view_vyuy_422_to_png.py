"""把 VYUY（YUV 4:2:2 打包）二进制解码成 PNG。

四种 4:2:2 打包排列里最"反直觉"的一种：每 4 字节是 V Y0 U Y1。

| 格式 | 每 4 字节的顺序 |
|---|---|
| YUYV | Y0 U Y1 V |
| UYVY | U Y0 V Y1 |
| YVYU | Y0 V Y1 U |
| VYUY | V Y0 U Y1   ← 本例 |

四个格式字节总数相同、可直接互转，但用错只会颜色错乱而不报错。

输入：自动生成 vyuy.yuv（640x480）
输出：output/vyuy.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时也可源码运行）
# 2. 在本目录运行：python view_vyuy_422_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」的 YUV_TYPE。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/vyuy.yuv"
OUTPUT_FILE = "output/vyuy.png"
WIDTH = 640        # 4:2:2 要求宽度为偶数
HEIGHT = 480
YUV_TYPE = "VYUY"  # 同族可换：YUYV / UYVY / YVYU / NV16 / NV61

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
    cli = common.require_cli(CLI)
    here = Path(__file__).resolve().parent
    input_path = here / INPUT_FILE
    output_path = here / OUTPUT_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    if WIDTH % 2:
        print(f"错误：4:2:2 要求宽度为偶数，当前 WIDTH={WIDTH}", file=sys.stderr)
        raise SystemExit(1)

    common.banner(f"步骤 1/2：生成测试输入（{YUV_TYPE}：V Y0 U Y1）")
    size = _generate.write_vyuy(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("yuv_422"))


if __name__ == "__main__":
    main()
