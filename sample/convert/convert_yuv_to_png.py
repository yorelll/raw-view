"""把 YUV 二进制解码成 PNG（convert 模式的"任务 3"）。

同一个 ``convert`` 子命令能做三件事，靠**输出文件扩展名**区分：

1. ``--input-type image``（默认）→ 图片转 RAW/YUV
2. ``--input-type yuv`` + 非图片扩展名 → YUV 转 YUV
3. ``--input-type yuv`` + **图片扩展名**（.png/.jpg/.bmp/.tif）→ YUV 解码成图片

本脚本演示第 3 种。它的效果与 ``view -t YUV`` 完全等价——两种写法任选。

**这正是"直接用 convert 解 .bin 报 failed to read image"的解法。**

输入：自动生成 yonly10.bin（1296x736 渐变）
输出：output/yonly10_decoded.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python convert_yuv_to_png.py
# 3. 输出在 output/ 目录
#
# 改用自己的文件：修改「配置区」。「输出扩展名必须是图片扩展名」是触发解码的
# 关键——如果你把它改成 .yuv，就会变成第 2 种任务（YUV 转 YUV）。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_FILE = "input/yonly10.bin"
OUTPUT_FILE = "output/yonly10_decoded.png"   # ← 扩展名决定"解码"
WIDTH = 1296
HEIGHT = 736
INPUT_YUV_TYPE = "YOnly10"
ALIGNMENT = "msb"
ENDIANNESS = "little"

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

    common.banner("步骤 1/2：生成测试输入（YOnly10，2 字节/像素）")
    _generate.write_yonly_multi_bit(
        input_path, WIDTH, HEIGHT, 10,
        alignment=ALIGNMENT, endianness=ENDIANNESS,
    )
    common.report_output(input_path)

    common.banner("步骤 2/2：convert --input-type yuv -> PNG")
    result = common.run_cli(
        cli, "convert",
        "-i", input_path, "-o", output_path,
        "--input-type", "yuv",
        "--input-yuv-type", INPUT_YUV_TYPE,
        "--width", WIDTH, "--height", HEIGHT,
        "--input-alignment", ALIGNMENT,
        "--input-endianness", ENDIANNESS,
    )
    common.check_ok(result, "YUV -> PNG")
    common.report_output(output_path)
    print("验证：打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。")
    print("\n要点：")
    print("  * 输出是 .png，所以 CLI 走「解码」而不是「重排」")
    print("  * 不需要 --target，也不需要 --yuv-type（没有目标布局）")
    print("  * 等价写法：view -i ... -o ... -t YUV --yuv-type YOnly10")


if __name__ == "__main__":
    main()
