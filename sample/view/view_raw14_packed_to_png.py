"""把 RAW14 Packed（MIPI CSI-2）二进制解码成 PNG。

RAW14 Packed 把 **4 个像素压进 7 个字节**，宽度必须是 **4 的倍数**。
14 bit 常见于高动态范围传感器。

输入：自动生成 raw14_packed.bin（640x480 渐变）
输出：output/raw14_packed.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时打印指引）
# 2. 在本目录运行：python view_raw14_packed_to_png.py
# 3. 输出会生成在 output/ 目录
#
# 如果你要改用自己的文件，修改下面的「配置区」即可。

# ============================================================
# 配置区（用户通常只需要改这里）
# ============================================================
CLI = None
INPUT_FILE = "input/raw14_packed.bin"
OUTPUT_FILE = "output/raw14_packed.png"
WIDTH = 640        # 必须是 4 的倍数（RAW14 Packed 的约束）
HEIGHT = 480
RAW_TYPE = "RAW14 Packed"

# ============================================================
# 以下为实现（用户通常不需要修改）
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

    if WIDTH % 4:
        print(f"错误：RAW14 Packed 要求宽度是 4 的倍数，当前 WIDTH={WIDTH}",
              file=sys.stderr)
        raise SystemExit(1)

    common.banner("步骤 1/2：生成测试输入（RAW14 Packed）")
    _generate.write_raw14_packed(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 7 // 4} 字节（4 像素 / 7 字节）")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "RAW", "--raw-type", RAW_TYPE,
        "--width", WIDTH, "--height", HEIGHT,

        "--preview-mode", "Grayscale",
    )
    common.check_ok(result, "RAW14 Packed 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("raw_packed"))


if __name__ == "__main__":
    main()
