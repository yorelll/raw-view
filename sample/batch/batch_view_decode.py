"""批量把 RAW/YUV 二进制解码成 PNG（纯 view 任务）。

典型用途：相机一次导出几十个 .bin，想一次性全部转成能看的图。

batch 的 view 条目里，``target`` 描述**输入**是 RAW 还是 YUV，
``width``/``height`` 是源帧尺寸。每个条目可覆盖任意全局字段——
本脚本演示"一批里混着 RAW12 和 YOnly10 两种格式"。

输入：自动生成 input/shot1.raw / shot2.raw（RAW12）+ grey1.bin（YOnly10）
输出：output/*.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python batch_view_decode.py
# 3. 输出在 output/ 目录
#
# 注意：batch 的 view 条目不会读取命令行上的 --width/--height，
#       尺寸必须写在 JSON 里（全局或条目级）。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_DIR = "input"
OUTPUT_DIR = "output"
BATCH_FILE = "input/view_jobs.json"
WIDTH = 640
HEIGHT = 480

# ============================================================
# 实现
# ============================================================
import json
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
    input_dir = here / INPUT_DIR
    output_dir = here / OUTPUT_DIR
    batch_file = here / BATCH_FILE
    common.ensure_dir(input_dir)
    common.ensure_dir(output_dir)

    common.banner("步骤 1/3：生成两种格式的测试输入")
    # 两张 RAW12（不同亮度），走 bayer 彩色预览
    for index, bayer in enumerate(("RGGB", "BGGR"), start=1):
        path = input_dir / f"shot{index}.raw"
        mosaic = _generate.bayer_mosaic_from_color(WIDTH, HEIGHT, bayer)
        values = (mosaic.astype("uint32") * 4095 // 255).astype("uint16") << 4
        path.write_bytes(values.astype("<u2").tobytes())
        common.report_output(path)
    # 一张 YOnly10
    grey = input_dir / "grey1.bin"
    _generate.write_yonly_multi_bit(grey, WIDTH, HEIGHT, 10)
    common.report_output(grey)

    common.banner("步骤 2/3：写 batch JSON（一条里混两种格式）")
    spec = {
        "mode": "view",
        "target": "RAW",
        "raw_type": "RAW12",
        "alignment": "msb",
        "endianness": "little",
        "bayer_pattern": "RGGB",
        "preview_mode": "Bayer Color",
        "width": WIDTH,
        "height": HEIGHT,
        "output_dir": str(output_dir),
        "files": [
            {"input": str(input_dir / "shot1.raw")},
            # 逐条覆盖：第二张用 BGGR 去马赛克
            {"input": str(input_dir / "shot2.raw"), "bayer_pattern": "BGGR"},
            # 逐条切换成 YUV 输入
            {"input": str(grey), "target": "YUV", "yuv_type": "YOnly10",
             "preview_mode": "Grayscale"},
        ],
    }
    batch_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    common.report_output(batch_file)

    common.banner("步骤 3/3：执行 batch（一次解 3 个文件）")
    result = common.run_cli(cli, "batch", "-b", batch_file)
    common.check_ok(result, "批量解码")

    # 只列本脚本的 3 个输出（output/ 里可能还有其它示例留下的结果）。
    produced = sorted(
        p for p in output_dir.glob("*.png")
        if p.name.startswith(("shot1_", "shot2_", "grey1_"))
    )
    print(f"\n本脚本生成 {len(produced)} 张图片：")
    for path in produced:
        print(f"  {path.name}  ({path.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
