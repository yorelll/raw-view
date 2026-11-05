"""演示 batch 的两个目录选项：``base_dir`` 与 ``output_dir``。

* ``base_dir``：``files[].input`` 里写**相对路径**时，从这个目录解析。
  这样同一份 JSON 可以在不同机器/不同数据集上复用，不必改路径。
* ``output_dir``：所有自动命名的输出放这里。不写则"输出到输入文件旁边"。

本脚本把输入放在 ``input/frames/``，JSON 里只写 ``"frame1.raw"`` 这样的
相对名，靠 ``base_dir`` 指到 ``input/frames``；输出统一进 ``output/from_base_dir/``。

输入：自动生成 input/frames/frame1..3.raw
输出：output/from_base_dir/*.png"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python batch_base_dir_output_dir.py
# 3. 输出在 output/from_base_dir/ 目录
#
# 关键：input/frames/ 与 output/ 的目录结构在仓库里可见，方便你照抄布局。

# ============================================================
# 配置区
# ============================================================
CLI = None
BASE_DIR = "input/frames"                    # 相对路径的基准
OUTPUT_DIR = "output/from_base_dir"          # 统一输出目录
BATCH_FILE = "input/base_dir_jobs.json"
WIDTH = 640
HEIGHT = 480
FRAME_COUNT = 3

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
    base_dir = here / BASE_DIR
    output_dir = here / OUTPUT_DIR
    batch_file = here / BATCH_FILE
    common.ensure_dir(base_dir)
    common.ensure_dir(output_dir)

    common.banner(f"步骤 1/3：在 {BASE_DIR}/ 下生成 {FRAME_COUNT} 个 RAW12 帧")
    for index in range(1, FRAME_COUNT + 1):
        path = base_dir / f"frame{index}.raw"
        _generate.write_raw12(path, WIDTH, HEIGHT)
        common.report_output(path)

    common.banner("步骤 2/3：写 JSON（input 只写相对名，靠 base_dir 解析）")
    spec = {
        "mode": "view",
        "target": "RAW",
        "raw_type": "RAW12",
        "alignment": "msb",
        "preview_mode": "Grayscale",
        "width": WIDTH,
        "height": HEIGHT,
        # 相对 input 从 base_dir 解析；绝对路径则忽略 base_dir
        "base_dir": str(base_dir),
        # 所有自动命名的输出都进这里
        "output_dir": str(output_dir),
        "files": [
            {"input": "frame1.raw"},
            {"input": "frame2.raw"},
            {"input": "frame3.raw"},
        ],
    }
    batch_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    common.report_output(batch_file)
    print(f'     files[].input 写的是 "frame1.raw"，实际解析为 {base_dir / "frame1.raw"}')

    common.banner("步骤 3/3：执行 batch")
    result = common.run_cli(cli, "batch", "-b", batch_file)
    common.check_ok(result, "base_dir / output_dir 批处理")

    # 只列本脚本的 3 个输出（该目录下可能还有其它示例留下的结果）。
    produced = sorted(output_dir.glob("frame*_*.png"))
    print(f"\n本脚本生成 {len(produced)} 张图片（都在 {OUTPUT_DIR}/ 下）：")
    for path in produced:
        print(f"  {path.name}  ({path.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
