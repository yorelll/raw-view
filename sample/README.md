# 示例代码（sample）

本目录是**可以立刻跑起来**的 raw-view 命令行示例：每个脚本都是独立、完整、
自带输入素材的，`python xxx.py` 就能看到结果，不需要先读文档或准备文件。

## 快速开始（约 10 秒）

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 进任意示例目录，跑任意脚本
cd sample/view
python view_raw8_to_png.py
```

就这样。脚本会用自己的 Python 调用 raw-view 命令行，**不需要先打包 exe**。

> **想要 exe？** 打包是可选的，事后随时可做：在仓库根目录运行 `release.bat`
> （它会先跑全量测试再打包，约 5-8 分钟）。打包完成后示例会自动优先使用
> `dist/raw-view/raw-view-cli.exe`，不用改任何代码。

### 脚本如何找到 raw-view 命令行

| 顺序 | 来源 | 说明 |
|---|---|---|
| 1 | `raw-view-cli.exe` | 打包版（`release.bat` 的产物），有就优先用 |
| 2 | **`python -m raw_view`** | **源码运行兜底**——仓库里直接可用，无需构建 |
| 3 | `RAW_VIEW_CLI` 环境变量 | 指向自备的可执行文件（优先级最高，见下） |

环境变量优先于前两者：
```bash
set RAW_VIEW_CLI=D:\path\to\raw-view-cli.exe
```
它必须是 **`raw-view-cli.exe`**（命令行版）。指向 `raw-view-gui.exe` 会被拒绝并
说明原因——那是纯 GUI 程序，当命令行调用会弹窗并挂住。

## 三个目录

| 目录 | 做什么 | 建议从这开始 |
|---|---|---|
| [`view/`](view/) | RAW/YUV **二进制 → 图片** | `view_raw8_to_png.py`（最简单） |
| [`convert/`](convert/) | **图片 → RAW/YUV**、YUV 互转、YUV → 图片 | `convert_png_to_raw12.py` |
| [`batch/`](batch/) | **批量**处理（JSON 描述任务）；**整个文件夹也用它** | `batch_image_to_raw.py` |

## 每个脚本的结构

所有脚本都是同一个形状，改自己的文件时只需要动「配置区」：

```python
"""脚本说明：演示什么、输入是什么、输出是什么"""

# ============================================================
# 使用说明（中文）
# ============================================================
# 1. 先确保已用 release.bat 生成 exe（脚本会检查，缺失时也可源码运行）
# 2. 在本目录运行：python xxx.py
# 3. 输出会生成在 output/ 目录

# ============================================================
# 配置区（用户通常只需要改这里）
# ============================================================
CLI = None                    # None = 自动查找（exe 优先，源码兜底）
INPUT_FILE = "input/xxx.bin"  # 输入文件
OUTPUT_FILE = "output/xxx.png"  # 输出文件
WIDTH = 640                   # 帧宽
HEIGHT = 480                  # 帧高
RAW_TYPE = "RAW12"            # 或 YUV_TYPE = "NV12"

# ============================================================
# 以下为实现（用户通常不需要修改）
# ============================================================
```

`CLI` 也可以直接写成一个路径字符串（等价于设置 `RAW_VIEW_CLI`）：
```python
CLI = r"D:\some\where\raw-view-cli.exe"
```

## 怎么判断"输出对不对"

每个 `view` 脚本在结尾都会打印**应该看到什么、看到别的说明哪里错了**，例如：

```text
打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。
     若明显偏亮发白或有台阶，通常是对齐方式（alignment）写错了：
     把 msb 改成 lsb 再试。若出现斜纹/错行，是宽度不对。
```

`convert` 脚本则说明怎么回读验证（多数会指向对应的 `view/` 脚本）。
`batch` 示例里，前两个处理的是同一批素材，可以对照看。

## 目录里的两个下划线文件

* `_common.py` —— 定位命令行（exe → 源码）、跑子命令、检查退出码、打印步骤标题、
  给出"应看到什么"的提示。所有示例共用。
* `_generate.py` —— 构造测试用的 RAW/YUV/图片。**内容是带图案的**（渐变 / 彩条 /
  Bayer 马赛克），不是纯色，这样解码结果对不对一眼就能看出来。

这两个文件用户不需要改；示例脚本把它们当库用。

## 生成的文件会进 git 吗

不会。`.gitignore` 已忽略 `sample/**/input/*` 与 `sample/**/output/*`
（只保留 `.gitkeep` 以维持目录结构）。所以你可以放心反复运行脚本。

## 全部示例一览

### view/ —— 二进制 → 图片

按**难度**从易到难排列，建议按顺序读：

| 难度 | 脚本 | 演示什么 |
|---|---|---|
| ★ | `view_raw8_to_png.py` | RAW8，最简单的 RAW 格式（无对齐/字节序） |
| ★ | `view_yonly8_yuv_alias_to_png.py` | YOnly8（1 字节/像素）+ `YOnly` 别名等价性 |
| ★ | `view_yonly10_yuv_to_png.py` | YOnly10（4:0:0 灰度，1296×736） |
| ★ | `view_yonly12_yuv_to_png.py` | YOnly12（位深差异：8/10/12/14/16） |
| ★★ | `view_raw12_msb_to_png.py` | RAW12 + MSB + 小端（最常见） |
| ★★ | `view_raw12_lsb_bigendian_to_png.py` | RAW12 + LSB + 大端 |
| ★★ | `view_raw10_lsb_bigendian_to_png.py` | RAW10 + LSB + 大端 |
| ★★ | `view_raw16_to_png.py` | RAW16（满量程 16 bit） |
| ★★ | `view_raw32_to_png.py` | RAW32（ToF / HDR 传感器） |
| ★★ | `view_yuyv_422_to_png.py` | YUYV（4:2:2 打包） |
| ★★ | `view_uyvy_422_to_png.py` | UYVY（4:2:2 打包，顺序 U Y0 V Y1） |
| ★★ | `view_vyuy_422_to_png.py` | VYUY（4:2:2 打包，顺序 V Y0 U Y1） |
| ★★ | `view_nv16_422_semiplanar_to_png.py` | NV16（4:2:2 半平面） |
| ★★ | `view_nv12_420_to_png.py` | NV12（4:2:0 半平面） |
| ★★ | `view_i420_420_planar_to_png.py` | I420（4:2:0 三平面） |
| ★★ | `view_yv12_420_planar_to_png.py` | YV12（4:2:0 三平面，V/U 顺序与 I420 相反） |
| ★★★ | `view_raw10_packed_to_png.py` | RAW10 Packed（4 像素 / 5 字节） |
| ★★★ | `view_raw12_packed_to_png.py` | RAW12 Packed（2 像素 / 3 字节） |
| ★★★ | `view_raw14_packed_to_png.py` | RAW14 Packed（4 像素 / 7 字节） |
| ★★★ | `view_raw12_bayer_color_rggb.py` | Bayer 彩色去马赛克（RGGB） |
| ★★★ | `view_yonly10_with_header_offset.py` | 文件头 + 多帧：`--offset`（含错误示范对照） |

### convert/ —— 图片 → RAW/YUV、互转、解码

| 难度 | 脚本 | 演示什么 |
|---|---|---|
| ★ | `convert_png_to_raw8.py` | PNG → RAW8（灰度） |
| ★ | `convert_png_to_raw12.py` | PNG → RAW12（Bayer RGGB） |
| ★ | `convert_png_to_yuyv.py` | PNG → YUYV（4:2:2） |
| ★ | `convert_png_to_nv12.py` | PNG → NV12（4:2:0） |
| ★ | `convert_png_to_yonly10.py` | PNG → YOnly10（10 bit 灰度） |
| ★★ | `convert_png_to_raw10_packed.py` | PNG → RAW10 Packed（省 37.5% 空间） |
| ★★ | `convert_yuv_to_png.py` | **YUV → PNG**（修掉 "failed to read image" 的写法） |
| ★★★ | `convert_nv16_to_yuyv_lossless.py` | **无损互转** + 回转逐字节验证 |
| ★★★ | `convert_nv12_to_i420_lossless.py` | 半平面 → 三平面 + 回转验证 |
| ★★★ | `convert_i420_to_nv21_lossless.py` | 三平面 → VU 半平面 + 回转验证 |

### batch/ —— 批量任务

| 难度 | 脚本 | 演示什么 |
|---|---|---|
| ★ | `batch_image_to_raw.py` | 纯 image 批量转换 + glob 展开 |
| ★ | `batch_view_decode.py` | 纯 view 批量解码（一条里混 RAW 与 YUV） |
| ★★ | `batch_mixed_jobs.py` | **混合任务**：一次跑 5 类转换 |
| ★★ | `batch_base_dir_output_dir.py` | `base_dir`（相对路径基准）+ `output_dir` |

### 为什么 YUV 示例比 RAW 少

RAW 家族只有 8 种格式（8/10/12/16/32 + 3 种 Packed），**已全部覆盖**。
YUV 有 16 种，本目录覆盖了 12 种，其余 4 种（YOnly14 / YOnly16 / YVYU / NV61 / NV21）
的用法与已给出的成员**完全同构**——只改 `YUV_TYPE` 一个变量即可，因此不再重复。
完整清单随时可查：

```bash
raw-view-cli.exe --help-view      # 或 python -m raw_view --help-view
```

## 常见疑问

**为什么 `convert` 解 `.bin` 会报 `failed to read image`？**
`convert` 的 `--input-type` 默认是 `image`（按 PNG/JPG 解码）。要么加
`--input-type yuv`，要么改用 `view`。见 `convert/convert_yuv_to_png.py` 与
`view/view_yonly10_yuv_to_png.py`。

**`--width` / `--height` 到底指什么？**
输入是**图片**时是**输出尺寸**（会缩放）；输入是**二进制**时是**源帧尺寸**
（不缩放）。YUV 互转时必填。见 `convert/convert_png_to_raw12.py` 与
`convert/convert_nv16_to_yuyv_lossless.py` 的对比。

**batch 能用命令行的 `--width/--height` 吗？**
不能，batch 忽略它们；尺寸要写进 JSON。

**能不能一次处理一整个文件夹？**
可以，**用 `batch`**。`view` / `convert` 的 `--input` 只接受单个文件，指向目录
会直接报错并提示改用 batch（这是有意的取舍，见
[`docs/cli.md`](../docs/cli.md) 第 5 节）。

在 JSON 里让某个条目的 `input` 指向一个 glob 即可覆盖整个目录，例如
`"files": [{"input": "input/photos/*.png"}]`（`batch_image_to_raw.py` 就是这个写法）；
共用参数写 JSON 顶层的 `width`/`height`/`target`，个别文件不同就在该条目里覆盖。
可运行的例子见 `batch/` 目录下其余 4 个脚本。

更完整的参数说明：`raw-view-cli.exe --help`、`--help-view`、`--help-convert`、
`--help-batch`，以及 [`docs/cli.md`](../docs/cli.md)。
