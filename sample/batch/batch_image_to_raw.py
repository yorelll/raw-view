"""批量把多张图片转换成 RAW12（纯 image 任务）。

batch 适合"同一套参数、多个文件"：一条命令处理一整批，比 shell 循环更省事，
而且自带 glob 展开、自动命名、以及跨条目的输出名去重。

本脚本会自己造 3 张不同颜色的 PNG，写一个 batch JSON，然后调用
``raw-view batch -b jobs.json``。

输入：自动生成 input/img1.png / img2.png / img3.png + jobs.json
输出：output/*.raw"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查）
# 2. 在本目录运行：python batch_image_to_raw.py
# 3. 输出在 output/ 目录（JSON 也会写到 input/jobs.json，方便你查看）
#
# 改用自己的文件：把 FILES 换成你的图片路径，或直接改 input/jobs.json。

# ============================================================
# 配置区
# ============================================================
CLI = None
INPUT_DIR = "input"
OUTPUT_DIR = "output"
BATCH_FILE = "input/jobs.json"
WIDTH = 640
HEIGHT = 480
RAW_TYPE = "RAW12"
ALIGNMENT = "msb"
ENDIANNESS = "little"
BAYER_PATTERN = "RGGB"
SOURCE_MODE = "bayer"

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
    np = common.require_numpy()
    # 先确认 exe 存在：缺 exe 时在生成任何文件之前就失败，
    # 而不是跑完一半才发现。
    cli = common.require_cli(CLI)
    import cv2

    here = Path(__file__).resolve().parent
    input_dir = here / INPUT_DIR
    output_dir = here / OUTPUT_DIR
    batch_file = here / BATCH_FILE
    common.ensure_dir(input_dir)
    common.ensure_dir(output_dir)

    common.banner("步骤 1/3：生成 3 张不同颜色的测试图片")
    colours = {
        "img1.png": (0, 0, 255),     # 红 (BGR)
        "img2.png": (0, 255, 0),     # 绿
        "img3.png": (255, 0, 0),     # 蓝
    }
    for name, colour in colours.items():
        image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        image[:, :] = colour
        # 叠一条横向渐变，避免纯色无法判断 stride 是否正确
        ramp = np.linspace(0.5, 1.0, WIDTH, dtype=np.float32)[None, :, None]
        image = np.clip(image.astype(np.float32) * ramp, 0, 255).astype(np.uint8)
        path = input_dir / name
        ok, encoded = cv2.imencode(".png", image)
        if not ok:  # pragma: no cover
            raise OSError(f"failed to encode {path}")
        encoded.tofile(str(path))
        common.report_output(path)

    common.banner("步骤 2/3：写 batch JSON")
    spec = {
        "mode": "convert",
        "target": "RAW",
        "raw_type": RAW_TYPE,
        "alignment": ALIGNMENT,
        "endianness": ENDIANNESS,
        "source_mode": SOURCE_MODE,
        "bayer_pattern": BAYER_PATTERN,
        "width": WIDTH,
        "height": HEIGHT,
        "output_dir": str(output_dir),
        # glob 展开：每个匹配自成一条，输出名自动带输入名，不会互相覆盖。
        # 用 "img*.png" 而不是 "*.png"：sample/batch/input 里还可能有其它示例
        # 生成的图片，本示例应该只处理它自己造的这三个。
        "files": [{"input": str(input_dir / "img*.png")}],
    }
    batch_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    common.report_output(batch_file)
    print("      JSON 内容与你该关心的字段：mode / target / raw_type / files")

    common.banner("步骤 3/3：执行 batch")
    result = common.run_cli(cli, "batch", "-b", batch_file)
    common.check_ok(result, "批量 PNG -> RAW12")

    # 只列本脚本的 3 个输出（output/ 里可能还有其它示例留下的结果）。
    produced = sorted(output_dir.glob("img*_*.raw"))
    print(f"\n本脚本生成 {len(produced)} 个文件：")
    for path in produced:
        print(f"  {path.name}  ({path.stat().st_size} 字节)")


if __name__ == "__main__":
    main()
