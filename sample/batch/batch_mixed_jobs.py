"""一个 batch 文件里混排"编码 + 解码 + YUV 互转"三类任务。

这是 batch 最有价值的能力：**每个条目各自带 mode / target / 格式**，所以
一次运行可以同时完成多种转换，不必拆成多次调用。

本脚本的 5 个条目分别是：
1. PNG → RAW12（image -> RAW）
2. PNG → NV12（image -> YUV）
3. PNG → RAW10 Packed（换 raw_type + 灰度源）
4. RAW12 → PNG（view 解码）
5. NV16 → YUYV（YUV 无损互转）

输入：自动生成 input/ 下的 png/raw/nv16 测试文件
输出：output/ 下 5 个不同类型的结果"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python batch_mixed_jobs.py
# 3. 输出在 output/ 目录；input/mixed_jobs.json 会保留下来供你参考/修改
#
# 改用自己的文件：直接编辑生成的 input/mixed_jobs.json 再单独运行
#     raw-view-cli.exe batch -b input/mixed_jobs.json

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_DIR = "input"
OUTPUT_DIR = "output"
BATCH_FILE = "input/mixed_jobs.json"
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

    common.banner("步骤 1/3：生成三类输入文件")
    _generate.write_png(input_dir / "photo.png", WIDTH, HEIGHT)
    common.report_output(input_dir / "photo.png")
    _generate.write_raw12(input_dir / "sensor.raw", WIDTH, HEIGHT)
    common.report_output(input_dir / "sensor.raw")
    _generate.write_nv16(input_dir / "frame.nv16", WIDTH, HEIGHT)
    common.report_output(input_dir / "frame.nv16")

    common.banner("步骤 2/3：写包含 5 类任务的 batch JSON")
    spec = {
        # 全局默认：条目未覆盖时生效
        "output_dir": str(output_dir),
        "width": WIDTH,
        "height": HEIGHT,
        "files": [
            # 1. 图片 -> RAW12（默认 mode=convert / target=RAW）
            {"input": str(input_dir / "photo.png"),
             "output": str(output_dir / "1_photo_raw12.raw"),
             "target": "RAW", "raw_type": "RAW12"},

            # 2. 图片 -> NV12
            {"input": str(input_dir / "photo.png"),
             "output": str(output_dir / "2_photo_nv12.yuv"),
             "target": "YUV", "yuv_type": "NV12"},

            # 3. 图片 -> RAW10 Packed（灰度源：不做 Bayer 马赛克）
            {"input": str(input_dir / "photo.png"),
             "output": str(output_dir / "3_photo_raw10p.raw"),
             "target": "RAW", "raw_type": "RAW10 Packed",
             "source_mode": "gray"},

            # 4. RAW12 -> PNG（切换到 view 模式）
            {"input": str(input_dir / "sensor.raw"),
             "output": str(output_dir / "4_sensor.png"),
             "mode": "view", "target": "RAW", "raw_type": "RAW12",
             "alignment": "msb", "preview_mode": "Grayscale"},

            # 5. NV16 -> YUYV（YUV 无损互转）
            {"input": str(input_dir / "frame.nv16"),
             "output": str(output_dir / "5_frame.yuyv"),
             "target": "YUV", "input_type": "yuv",
             "input_yuv_type": "NV16", "yuv_type": "YUYV"},
        ],
    }
    batch_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    common.report_output(batch_file)

    common.banner("步骤 3/3：执行混合 batch")
    result = common.run_cli(cli, "batch", "-b", batch_file)
    common.check_ok(result, "混合批处理")

    # 只列本脚本自己产出的 5 个文件（output/ 里可能还有其它示例留下的结果）。
    expected = sorted(Path(name) for name in (
        "1_photo_raw12.raw", "2_photo_nv12.yuv", "3_photo_raw10p.raw",
        "4_sensor.png", "5_frame.yuyv",
    ))
    print(f"\n本脚本生成 {len(expected)} 个文件（都在 {OUTPUT_DIR}/ 下）：")
    for name in expected:
        path = output_dir / name
        if path.is_file():
            print(f"  {name}  ({path.stat().st_size} 字节)")
        else:  # pragma: no cover - 出现即说明 batch 少写了文件
            print(f"  {name}  缺失！", file=sys.stderr)


if __name__ == "__main__":
    main()
