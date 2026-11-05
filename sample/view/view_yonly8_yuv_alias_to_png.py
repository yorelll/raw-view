"""把 YOnly8（YUV 4:0:0，8 bit）二进制解码成 PNG，并演示 `YOnly` 别名。

``YOnly`` 就是 ``YOnly8`` 的别名（8 bit 是默认位深），两种写法等价。
8 bit 版本是 **1 字节/像素**，没有位对齐/字节序概念——这是 YOnly 家族里
最简单的成员，也是理解"位深只影响存储方式、不影响像素数量"的入口。

输入：自动生成 yonly8.bin（640x480 渐变）
输出：output/yonly8.png + output/yonly_alias.png（与前者应完全相同）"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时也可源码运行）
# 2. 在本目录运行：python view_yonly8_yuv_alias_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yonly8.bin"
OUTPUT_FILE = "output/yonly8.png"
ALIAS_OUTPUT_FILE = "output/yonly_alias.png"   # 用 YOnly 别名解出的同一帧
WIDTH = 640
HEIGHT = 480
YUV_TYPE = "YOnly8"
ALIAS_TYPE = "YOnly"     # 与 YOnly8 等价的别名

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
    alias_path = here / ALIAS_OUTPUT_FILE
    common.ensure_dir(input_path.parent)
    common.ensure_dir(output_path.parent)

    common.banner("步骤 1/3：生成测试输入（YOnly8，1 字节/像素）")
    size = _generate.write_yonly8(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT} 字节 / 实际 {size} 字节"
          f"（8 bit 是 1 字节/像素，没有对齐/字节序参数）")

    common.banner(f"步骤 2/3：用 {YUV_TYPE} 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} 解码")
    common.report_output(output_path)

    common.banner(f"步骤 3/3：用别名 {ALIAS_TYPE} 解码同一个文件（结果应相同）")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", alias_path,
        "-t", "YUV", "--yuv-type", ALIAS_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{ALIAS_TYPE} 别名解码")
    common.report_output(alias_path)

    if output_path.read_bytes() == alias_path.read_bytes():
        print(f"     两张图逐字节相同 —— 证实 {ALIAS_TYPE} 就是 {YUV_TYPE} 的别名。")
    else:  # pragma: no cover - 出现即说明别名语义变了
        print("     警告：两张图不同！别名语义可能已改变。", file=sys.stderr)
        raise SystemExit(1)

    print("\n" + common.expected_view_hint("yonly"))


if __name__ == "__main__":
    main()
