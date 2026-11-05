"""把 YV12（YUV 4:2:0 三平面）二进制解码成 PNG。

YV12 与 I420 的唯一区别是**色度平面顺序**：

| 格式 | 平面顺序 |
|---|---|
| I420 | Y, U, V |
| YV12 | Y, V, U   ← 本例 |

字节总数完全相同，但顺序错了会导致整片图偏色或红蓝互换。这是 4:2:0 家族里
最容易搞混的一对，所以单独一个示例。

输入：自动生成 yv12.yuv（640x480）
输出：output/yv12.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时也可源码运行）
# 2. 在本目录运行：python view_yv12_420_planar_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yv12.yuv"
OUTPUT_FILE = "output/yv12.png"
WIDTH = 640        # 4:2:0 要求宽高均为偶数
HEIGHT = 480
YUV_TYPE = "YV12"  # 同族可换：I420 / NV12 / NV21

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

    if WIDTH % 2 or HEIGHT % 2:
        print(f"错误：4:2:0 要求宽高均为偶数，当前 {WIDTH}x{HEIGHT}", file=sys.stderr)
        raise SystemExit(1)

    common.banner(f"步骤 1/2：生成测试输入（{YUV_TYPE}：Y + V + U）")
    size = _generate.write_yv12(input_path, WIDTH, HEIGHT)
    common.report_output(input_path)
    print(f"     期望大小 {WIDTH * HEIGHT * 3 // 2} 字节 / 实际 {size} 字节")

    common.banner("步骤 2/2：调用 CLI 解码")
    result = common.run_cli(
        cli, "view",
        "-i", input_path, "-o", output_path,
        "-t", "YUV", "--yuv-type", YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
    )
    common.check_ok(result, f"{YUV_TYPE} 解码")
    common.report_output(output_path)

    print("\n" + common.expected_view_hint("yuv_420"))
    print("     注意：把 --yuv-type 改成 I420 解同一个文件，U/V 顺序反了，画面会偏色。")


if __name__ == "__main__":
    main()
