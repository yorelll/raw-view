# raw-view 命令行使用手册

本手册面向**第一次使用 raw-view CLI** 的人：从「我要做什么」出发，到一条可直接
复制执行的命令。命令行的内建帮助与本文档一一对应，离线时也能查：

| 命令 | 内容 |
|---|---|
| `raw-view --help` / `-h` | 一级菜单：三个模式是什么、哪些参数通用于所有模式、二级帮助入口 |
| `raw-view --help-view` | view 模式完整用法 + 举例 |
| `raw-view --help-convert` | convert 模式完整用法 + 举例 |
| `raw-view --help-batch` | batch JSON 格式 + 可直接复制的完整示例（`--batch-help` 为兼容别名） |
| `raw-view --version` | 版本号 |

> 可执行文件名：zip 版里 `raw-view.exe` 是启动器（无参数 → GUI，带子命令 → 转发
> CLI），`raw-view-cli.exe` 只跑 CLI、绝不弹窗。源码目录里用 `python -m raw_view`。
> 本文档统一写 `raw-view`，三种写法参数完全一致。

---

## 目录

1. [先搞清楚三件事](#s1-start)
2. [一图看懂参数归属](#s2-params)
3. [view：RAW/YUV 二进制 → 图片](#s3-view)
4. [convert：编码 / 互转 / 解码](#s4-convert)
5. [batch：JSON 批量任务](#s5-batch)
6. [格式总表](#s6-formats)
7. [YUV 家族互转：哪些能转、哪些不能](#s7-transcode)
8. [常见错误排查](#s8-troubleshooting)
9. [退出码与输出约定](#s9-exit-codes)

---

<a id="s1-start"></a>

## 1. 先搞清楚三件事

命令行只有三个模式，**按「输入是什么、想得到什么」选模式**：

| 我想…… | 模式 | 典型命令 |
|---|---|---|
| 把 `.raw` / `.bin` / `.yuv` 二进制**看成图**，导出 PNG | `view` | `view -i a.bin -o a.png -t YUV --yuv-type YOnly10 --width 1296 --height 736` |
| 把 **PNG/JPG 转成** RAW/YUV 二进制 | `convert` | `convert -i p.png -o p.raw -t RAW --raw-type RAW12 --width 1920 --height 1080` |
| 把 **YUV 换成另一种 YUV 布局**（无损重排） | `convert` | `convert -i f.nv16 -o f.yuyv --input-type yuv --input-yuv-type NV16 --yuv-type YUYV --width 1920 --height 1080` |
| 把 **YUV 二进制解码成图** | `view` 或 `convert` | 见[第 4.3 节](#s43-yuv-to-image) |
| 一次处理**一批文件** | `batch` | `batch -b jobs.json` |
| 打开图形界面 | 无子命令 | `raw-view` |

### 三条最容易踩的坑

1. **`--width/--height` 的含义随输入类型变化**
   - 输入是**图片**（PNG/JPG）时，它是**输出尺寸**，图像会被缩放；默认 640×480。
   - 输入是**二进制**（RAW/YUV）时，它是**源帧尺寸**，用于切分字节布局；**不会**
     缩放。YUV 转码时**必填**，其它情况默认 640×480。
2. **RAW/YUV 二进制必须显式说明布局**：`--target`（RAW 还是 YUV）、`--raw-type`
   或 `--yuv-type`、以及 `--width/--height`。少了任何一个都会读出错误结果或直接
   报错。
3. **`convert` 的 `--input-type` 默认是 `image`**。拿 `.bin` 直接 `convert` 一定会
   失败——它去按 PNG/JPG 解码了。见[第 8 节](#s8-troubleshooting)第一条。

---

<a id="s2-params"></a>

## 2. 一图看懂参数归属

CLI 一共接受 26 个选项（30 种写法，含短选项与 `--batch-help` 别名），但**大部分
只对某一个模式有效**。一级 `--help` 只列核心参数，其余按模式分派到二级帮助。

### 2.1 核心参数（view / convert 接受；**batch 不读**）

| 参数 | 含义 | batch |
|---|---|---|
| `-i, --input PATH` | 输入文件 | ❌ 读 JSON 的 `files[].input` |
| `-o, --output PATH` | 输出文件；省略则自动命名 | ❌ 读 JSON 的 `files[].output` |
| `--width N` / `--height N` | 帧宽 / 帧高 | ❌ 读 JSON 的 `width` / `height` |
| `--offset N` | 跳过文件头部的字节数，默认 0 | ❌ 读 JSON 的 `offset` |
| `-h, --help` / `--version` | 一级帮助 / 版本号 | ✅ |
| `--help-view` / `--help-convert` / `--help-batch` | 三个二级帮助 | ✅ |

> **batch 会静默忽略**上表标 ❌ 的五项：它们没有全局含义，每个文件的尺寸/偏移
> 都由 JSON 条目决定。想让所有文件用同一组尺寸，就写 JSON 的顶层 `width`/`height`。

### 2.1.1 必须写 mode 词

```
raw-view --width 800          # ❌ exit 2：有 CLI 参数却没有 mode 词
raw-view view --width 800     # ✅
raw-view                      # ✅（无参数 = 打开 GUI）
```

历史上 `--width 800` 会**打开 GUI 并阻塞终端**；现在改为一行错误 + exit 2。
只有"确实一个 CLI 参数都没给"（裸 `raw-view`）才会进 GUI。

### 2.2 模式专属参数

| 参数 | view | convert（image→RAW/YUV） | convert（YUV→YUV / YUV→图片） | batch |
|---|:--:|:--:|:--:|:--:|
| `-t, --target {RAW,YUV}` | ✅ 说明**输入**类型 | ✅ 说明**输出**类型 | 只能 `YUV` | ✅ |
| `--raw-type` | ✅ 输入布局 | ✅ 输出布局 | — | ✅ |
| `--yuv-type` | ✅ 输入布局 | ✅ 输出布局 | ✅ 输出布局（转码时） | ✅ |
| `--alignment` / `--endianness` | ✅ RAW、YOnly 多 bit | ✅ | 兜底值 | ✅ |
| `--bayer-pattern` | ✅ Bayer 彩色预览 | ✅ | — | ✅ |
| `--preview-mode` | ✅ | — | — | ✅（view 条目） |
| `--source-mode {bayer,gray}` | — | ✅ | — | ✅ |
| `--input-type {image,yuv,raw}` | — | ✅ 默认 `image` | ✅ 必须是 `yuv` | ✅ |
| `--input-yuv-type` | 等价 `--yuv-type`（两者都行） | — | ✅ 源布局 | ✅ |
| `--input-alignment` / `--input-endianness` | 等价 `--alignment/--endianness` | — | ✅ 源参数 | ✅ |
| `--input-raw-type` | 等价 `--raw-type` | — | — | ✅ |
| `-b, --batch-file` | — | — | — | ✅ 必填 |

> **为什么 `view` 也有 `--input-*`？** 历史上 `view` 用 `--raw-type/--yuv-type`
> 描述输入，`convert` 又用同名参数描述输出，两者语义冲突。`--input-*` 一族是
> 统一的「输入叫什么」写法，`view` 与 YUV 转码都接受；不写时自动回落到
> `--raw-type/--yuv-type/--alignment/--endianness`。两种写法等价。

---

<a id="s3-view"></a>

## 3. view：RAW/YUV 二进制 → 图片

```bash
raw-view view -i INPUT -o OUTPUT.png --target {RAW|YUV} [格式参数]
raw-view view [文件…]          # 不带 -i：打开 GUI 并加载这些文件
```

- `-o` 省略时输出到 **输入文件同目录的 `view_out/`**，文件名模板
  `{stem}_{W}x{H}_{format}.png`（模板可在 GUI 设置里改）。
- 只解码**第一帧**。多帧文件请用 `convert --input-type yuv` 或 batch。

### 3.1 RAW 输入（`--target RAW`，默认）

| 参数 | 取值 | 默认 |
|---|---|---|
| `--raw-type` | `RAW8` `RAW10` `RAW12` `RAW16` `RAW32` `RAW10 Packed` `RAW12 Packed` `RAW14 Packed` | `RAW12` |
| `--alignment` | `msb` / `lsb` | `msb` |
| `--endianness` | `little` / `big` | `little` |
| `--bayer-pattern` | `RGGB` `GRBG` `GBRG` `BGGR`（大小写不敏感） | `RGGB` |
| `--preview-mode` | `Bayer Color`（去马赛克彩色）/ `Grayscale` | `Bayer Color` |

- `--alignment` 只对**存在 16-bit 容器里的** `RAW10/12/16` 有意义：`msb` = 有效位在
  高 N 位，`lsb` = 有效位在低 N 位。`RAW8`、`RAW32` 忽略它。
- Packed 格式（MIPI CSI-2）有宽度约束：`RAW10 Packed` 宽 % 4 == 0，
  `RAW12 Packed` 宽 % 2 == 0，`RAW14 Packed` 宽 % 4 == 0。

**示例（举一反三）**

```bash
# RAW12 · MSB 对齐 · 小端 · RGGB
raw-view view -i sensor.raw -o sensor.png -t RAW \
    --raw-type RAW12 --width 1920 --height 1080 \
    --alignment msb --endianness little --bayer-pattern RGGB

# 同一份文件是 LSB 对齐 + 大端？只改这两个词：
#   --alignment lsb --endianness big

# RAW10 Packed（4 像素 / 5 字节）
raw-view view -i packed.raw -o packed.png -t RAW \
    --raw-type "RAW10 Packed" --width 1920 --height 1080
# 换成 "RAW12 Packed"（2 像素/3 字节）或 "RAW14 Packed"（4 像素/7 字节）：
#   只改 --raw-type，命令形状完全一样

# 灰度预览（不猜 Bayer 颜色）
raw-view view -i sensor.raw -o gray.png -t RAW \
    --raw-type RAW12 --width 1920 --height 1080 --preview-mode Grayscale
```

### 3.2 YUV 输入（`--target YUV`）

| 参数 | 取值 | 默认 |
|---|---|---|
| `--yuv-type`（或 `--input-yuv-type`） | 见[第 6 节](#s6-formats) | `YUYV` |
| `--alignment` / `--endianness` | 只影响 `YOnly10/12/14/16` | `msb` / `little` |

几何约束：4:2:2 要求**宽为偶数**；4:2:0 要求**宽高均为偶数**；YOnly 任意正尺寸。

**示例（举一反三）**

```bash
# 4:2:2 打包格式：YUYV（每 4 字节 2 像素）
raw-view view -i frame.yuv -o frame.png -t YUV \
    --yuv-type YUYV --width 1920 --height 1080
# YVYU / UYVY / VYUY 是同样的 4 字节换了个顺序 → 只改 --yuv-type
# NV16 / NV61 是 Y 平面 + 交错色度平面，总字节数相同 → 同样只改 --yuv-type

# YOnly10（YUV 4:0:0，10 bit 左对齐存在 16-bit 字里，2 字节/像素）
raw-view view -i grey.bin -o grey.png -t YUV \
    --yuv-type YOnly10 --width 1296 --height 736
# YOnly12 / YOnly14 / YOnly16：同一条命令，只改位深，仍是 2 字节/像素
# YOnly8：1 字节/像素，忽略 alignment/endianness —— 直接 --yuv-type YOnly8
# 画面偏亮/有台阶，多半是 LSB 对齐：加 --alignment lsb（或 --endianness big）

# 4:2:0 半平面：NV12（宽高都要偶数）
raw-view view -i frame.nv12 -o frame.png -t YUV \
    --yuv-type NV12 --width 1280 --height 720
# I420 / YV12 / NV21 只差平面顺序 → 只改 --yuv-type

# 跳过 128 字节文件头
raw-view view -i a.raw -o a.png -t RAW --raw-type RAW12 \
    --width 640 --height 480 --offset 128
```

---

<a id="s4-convert"></a>

## 4. convert：编码 / 互转 / 解码

```bash
raw-view convert -i INPUT -o OUTPUT [参数]
```

**同一个子命令承担三件事，靠 `--input-type` 和输出文件扩展名区分：**

| # | 任务 | 触发条件 | `--width/--height` 含义 |
|---|---|---|---|
| 1 | 图片 → RAW/YUV | `--input-type image`（默认） | **输出尺寸**（会缩放），默认 640×480 |
| 2 | YUV → YUV | `--input-type yuv` + 输出**不是**图片扩展名 | **源帧尺寸**，**必填**，不缩放 |
| 3 | YUV → 图片 | `--input-type yuv` + 输出是 `.png/.jpg/.jpeg/.bmp/.tif/.tiff` | **源帧尺寸**，**必填**，不缩放 |

`--input-type raw` 为预留值（RAW↔YUV 的 bit 解包尚未实现），会明确报错。

### 4.1 任务 1：图片 → RAW/YUV

输出 RAW 时用 `--target RAW`（默认）；输出 YUV 时用 `--target YUV`。

```bash
# PNG → RAW12，MSB 对齐、小端、RGGB Bayer 马赛克
raw-view convert -i photo.png -o photo.raw -t RAW --raw-type RAW12 \
    --width 1920 --height 1080 \
    --alignment msb --endianness little \
    --source-mode bayer --bayer-pattern RGGB
# 举一反三：
#   LSB + 大端 → --alignment lsb --endianness big
#   灰度 RAW  → --source-mode gray（此时 --bayer-pattern 无关）

# PNG → RAW10 Packed（宽必须能被 4 整除）
raw-view convert -i photo.png -o photo.raw -t RAW \
    --raw-type "RAW10 Packed" --width 1920 --height 1080
# 举一反三："RAW12 Packed"（宽 % 2）、"RAW14 Packed"（宽 % 4）

# PNG → YUYV，缩放到 1280×720（注意：这里是缩放！）
raw-view convert -i photo.png -o photo.yuv -t YUV \
    --yuv-type YUYV --width 1280 --height 720
# 举一反三：任何 4:2:2 / 4:2:0 布局都只改 --yuv-type（NV12、I420、NV16…）
#   YOnly10 需要额外给存储参数：--yuv-type YOnly10 --alignment msb --endianness little

# 省略 -o：自动输出到 convert_out/
raw-view convert -i photo.jpg --target RAW --width 1920 --height 1080
```

### 4.2 任务 2：YUV → YUV（无损字节重排）

**不缩放、不重采样**，只是把同样的 Y/U/V 采样按另一种布局重新排。多帧文件逐帧
转换并全部保留。

```bash
# 4:2:2 家族：NV16 → YUYV
raw-view convert -i frame.nv16 -o frame.yuyv \
    --input-type yuv --input-yuv-type NV16 --yuv-type YUYV \
    --width 1920 --height 1080
# 举一反三：
#   目标换成 YVYU / UYVY / VYUY / NV61 → 只改 --yuv-type
#   源换成 YVYU / NV61 …              → 只改 --input-yuv-type
#   两个开关相互独立，所以 6×5 = 30 个方向全部可达

# 4:2:0 家族：I420 → NV12（宽高都必须偶数）
raw-view convert -i frame.i420 -o frame.nv12 \
    --input-type yuv --input-yuv-type I420 --yuv-type NV12 \
    --width 1920 --height 1080
# 举一反三：YV12、NV21 同理，只改 --yuv-type

# 跳过 128 字节文件头
raw-view convert -i frame.nv16 -o frame.yuyv \
    --input-type yuv --input-yuv-type NV16 --yuv-type YUYV \
    --width 1920 --height 1080 --offset 128
```

校验规则：

- `--width/--height` **必填**（源帧尺寸，不能猜）。
- 文件字节数必须是**单帧字节数的整数倍**，否则整份文件拒绝（不静默截断）。
- 源与目标必须在**同一个色度采样家族**内（见第 7 节）。
- 单帧 > 512 MB 在**写盘前**拒绝，不会留下半成品。

<a id="s43-yuv-to-image"></a>

### 4.3 任务 3：YUV 二进制 → 图片（解码）

`--input-type yuv` + **图片扩展名**的输出 = 解码第一帧到图片。
这条路径和 `view -t YUV` 等价，只是拼写不同——先看 `--help-convert` 的人不必再
去猜 `view`。

```bash
# 等价于问题单里的场景：YOnly10 的 .bin → PNG
raw-view convert -i grey.bin -o grey.png \
    --input-type yuv --input-yuv-type YOnly10 \
    --width 1296 --height 736

# 完全等价的 view 写法
raw-view view -i grey.bin -o grey.png -t YUV \
    --yuv-type YOnly10 --width 1296 --height 736
```

- 不需要 `--target`（会按 YUV 处理）。
- `--yuv-type` **这里不参与判断**（没有"目标布局"这回事）。源布局来自
  `--input-yuv-type`；**它缺省时回落到 `--yuv-type`**（默认 `YUYV`）——
  **没有自动检测**，写错布局不会报错，只会解出全错的画面。
- 只解码**第一帧**；`Frames found:` 会告诉你文件里**能切出几帧**，
  `(decoding frame 1 only)` 说明只解了第一帧。需要全部帧请用 batch。
- 尾部不足一帧的字节会被**忽略并打印 `Warning:`**（与任务 2 不同：任务 2 要求
  整倍数，否则拒绝整个文件）。这是有意的——解码只取第一帧，残帧无害。
- `--offset`、`--input-alignment`、`--input-endianness` 都生效（与 `view` 一致）。

### 4.4 convert 的默认值与回落关系

| 参数 | 默认 / 回落 |
|---|---|
| `--input-type` | `image` |
| `--target` | `RAW`；`--input-type yuv` 时默认 `YUV`。显式 `-t RAW` + YUV 源 = 报错 |
| `--input-yuv-type` | 回落到 `--yuv-type` |
| `--input-alignment` | 回落到 `--alignment` |
| `--input-endianness` | 回落到 `--endianness` |

---

<a id="s5-batch"></a>

## 5. batch：JSON 批量任务

```bash
raw-view batch -b jobs.json
raw-view batch --batch-file jobs.json     # 同一个选项的全名
```

`-b, --batch-file` 是**唯一必需**的命令行选项；其余一切都在 JSON 里。

> **`--width` / `--height` / `--offset` / `-i` / `-o` 在 batch 下不生效**，
> 且**不会报错**——每个文件的尺寸与偏移来自 JSON 的全局键或条目键。要让一批
> 文件共用尺寸，写 JSON 顶层的 `width`/`height`/`offset`。

JSON 必须是**严格 JSON + UTF-8**：**不能有 `//` 注释，不能有尾逗号**（会
`JSONDecodeError`）。`--help-batch` 里给出的三段示例都是合法 JSON，可直接复制。

**什么时候用 batch、什么时候用 shell 循环**：只有一个固定命令、逐文件套用时，
shell 循环（`for f in *.png; do raw-view convert ... "$f"; done`）更简单；当每个
文件的参数不同（尺寸/格式/mode 混排）、或需要 glob 展开与自动命名时，用 batch。

### 5.1 最小可用示例

```json
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "alignment": "msb",
  "endianness": "little",
  "bayer_pattern": "RGGB",
  "width": 1920,
  "height": 1080,
  "output_dir": "convert_out",
  "files": [
    {"input": "photos/*.png"}
  ]
}
```

### 5.2 混合任务示例（一条 batch 干五件事）

```json
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "width": 640,
  "height": 480,
  "output_dir": "out",
  "base_dir": "D:/work/frames",
  "files": [
    {"input": "shot1.png", "width": 1920, "height": 1080},
    {"input": "shot2.jpg", "target": "YUV", "yuv_type": "NV12",
     "width": 1280, "height": 720},
    {"input": "shot3.bmp", "raw_type": "RAW10 Packed", "source_mode": "gray"},
    {"input": "sensor.raw", "mode": "view", "alignment": "lsb",
     "width": 1920, "height": 1080, "bayer_pattern": "BGGR"},
    {"input": "frame.nv12", "mode": "view", "target": "YUV", "yuv_type": "NV12",
     "width": 1920, "height": 1080}
  ]
}
```

### 5.3 YUV 转码示例

```json
{
  "mode": "convert",
  "target": "YUV",
  "input_type": "yuv",
  "input_yuv_type": "NV16",
  "yuv_type": "YUYV",
  "width": 1920,
  "height": 1080,
  "output_dir": "yuv_out",
  "files": [
    {"input": "captures/*.nv16"},
    {"input": "extra.nv16", "yuv_type": "YVYU"}
  ]
}
```

### 5.4 字段总表

**全局键**（作为所有条目的默认值，条目可逐项覆盖）：

| 键 | 取值 | 说明 |
|---|---|---|
| `mode` | `"convert"`（默认）/ `"view"` | 操作类型 |
| `target` | `"RAW"`（默认）/ `"YUV"` | convert：**写什么**；view：**读什么** |
| `input_type` | `"image"`（默认）/ `"yuv"` / `"raw"` | 仅 convert；`yuv` 时 width/height 变必填的源帧尺寸 |
| `input_yuv_type` | YUV 布局名 | 源布局，默认 `yuv_type` |
| `input_raw_type` | RAW 布局名 | 源布局，默认 `raw_type` |
| `input_alignment` | `"lsb"` / `"msb"` | 默认 `alignment` |
| `input_endianness` | `"little"` / `"big"` | 默认 `endianness` |
| `raw_type` | RAW 布局 | 默认 `RAW12` |
| `yuv_type` | YUV 布局 | 默认 `YUYV` |
| `width` / `height` | 整数 | 默认 640 / 480 |
| `alignment` | `"lsb"` / `"msb"` | 默认 `msb` |
| `endianness` | `"little"` / `"big"` | 默认 `little` |
| `source_mode` | `"bayer"` / `"gray"` | 默认 `bayer` |
| `bayer_pattern` | `RGGB` / `GRBG` / `GBRG` / `BGGR` | 默认 `RGGB`，大小写不敏感 |
| `preview_mode` | `"Bayer Color"` / `"Grayscale"` | 仅 `mode="view"` |
| `offset` | 整数 | 默认 0 |
| `output_dir` | 路径 | 自动输出放这里 |
| `base_dir` | 路径 | `files[].input` 的相对路径基准 |
| `files` | 数组 | **必填** |

**条目键**（`files[]` 内）：

| 键 | 说明 |
|---|---|
| `input` | 必填；含 `*` `?` `[` `]` 时按 glob 展开，每个匹配一条 |
| `output` | 可选；省略则自动命名（convert → `.raw`/`.yuv`，view → `.png`） |
| 任意全局键 | 覆盖该条目的全局值 |

**行为要点**

- 条目按顺序执行；单条失败打印 `FAIL` 并继续，最终只要有失败就退出码 1。
- `mode="view"`：解码 RAW/YUV → PNG。
- `mode="convert"` + `input_type="yuv"`：YUV→YUV 重排；若条目给了图片扩展名的
  `output`（如 `"x.png"`），则解码成图片（规则与单文件命令一致）。
- 自动输出名在**整个 batch 内唯一**：重名会变成 `..._2`、`..._3`，绝不静默覆盖。
- 单帧 > 512 MB 在写盘前拒绝。

---

<a id="s6-formats"></a>

## 6. 格式总表

### 6.1 RAW

| 格式 | 每像素字节 | 宽度约束 | 对齐/端序 | 备注 |
|---|---|---|---|---|
| `RAW8` | 1 | 任意 | 不适用 | |
| `RAW10` | 2（16-bit 容器） | 任意 | `--alignment`、`--endianness` 生效 | 10 bit 有效位 |
| `RAW12` | 2（16-bit 容器） | 任意 | 同上 | 12 bit 有效位 |
| `RAW16` | 2 | 任意 | `--endianness` | |
| `RAW32` | 4 | 任意 | `--endianness`（忽略 alignment） | |
| `RAW10 Packed` | 1.25 | 宽 % 4 == 0 | 不适用 | MIPI CSI-2，4 像素 / 5 字节 |
| `RAW12 Packed` | 1.5 | 宽 % 2 == 0 | 不适用 | 2 像素 / 3 字节 |
| `RAW14 Packed` | 1.75 | 宽 % 4 == 0 | 不适用 | 4 像素 / 7 字节 |

### 6.2 YUV

| 家族 | 格式 | 每像素字节 | 宽度约束 | 备注 |
|---|---|---|---|---|
| 4:0:0 | `YOnly8`（别名 `YOnly`） | 1 | 任意 | 纯灰度，忽略 alignment/endianness |
| 4:0:0 | `YOnly10` / `12` / `14` / `16` | 2（16-bit 容器） | 任意 | `--alignment`、`--endianness` 生效 |
| 4:2:2 | `YUYV` `UYVY` `YVYU` `VYUY` | 2 | 宽为偶数 | 每 4 字节 2 像素，打包 |
| 4:2:2 | `NV16` `NV61` | 2 | 宽为偶数 | Y 平面 + 交错 UV/VU 平面 |
| 4:2:0 | `I420` `YV12` `NV12` `NV21` | 1.5 | 宽高均为偶数 | 三平面 / 半平面 |

### 6.3 图片

输入（convert/batch）：PNG、JPEG、BMP（OpenCV 能解的其它格式也可用）。
输出（view/convert 解码）：`.png` / `.jpg` / `.jpeg` / `.bmp` / `.tif` / `.tiff`。

---

<a id="s7-transcode"></a>

## 7. YUV 家族互转：哪些能转、哪些不能

### 7.1 能转的方向（共 42 个）

同家族内**任意两种格式两两互转**，纯字节重排，**逐字节可还原**：

| 家族 | 格式 | 方向数 |
|---|---|---|
| 4:2:2 | `YUYV` `UYVY` `YVYU` `VYUY` `NV16` `NV61` | 6 × 5 = 30 |
| 4:2:0 | `I420` `YV12` `NV12` `NV21` | 4 × 3 = 12 |

<a id="s72-refused"></a>

### 7.2 不能转的方向与原因

| 方向 | 为什么不行 |
|---|---|
| 4:2:2 ↔ 4:2:0（如 `NV16 → NV12`） | 色度采样率不同（水平 2:1 vs 水平+垂直 2:1）。要转换必须做**有损**色度重采样，不再是「字节重排」，无法逐字节还原 |
| YOnly ↔ 4:2:2 / 4:2:0 | YOnly 是 4:0:0，**根本没有 U/V 平面**。要生成彩色只能重采样或凭空造色，不是无损操作 |
| RAW ↔ YUV | 涉及 bit 解包与色彩空间转换，**预留后续版本**（当前 `--input-type raw` 明确报错） |
| `X → X`（同格式） | 无操作。不报错只会产出与输入逐字节相同的文件，容易误以为做了转换 |

**需要跨家族或改采样率时怎么办？** 先转成图片再编码：
`view`（YUV → PNG）→ `convert`（PNG → 目标 YUV）。这条路是有损的，但可控且能
看到中间结果。

### 7.3 尺寸与帧数规则

- 源帧字节数由 `格式 × 宽 × 高` 决定，`--width/--height` **必须**是源帧真实尺寸。
- 文件字节数必须是单帧字节数的**整数倍**；否则整份文件拒绝，错误信息会给出
  期望字节数、实际字节数和余数字节。
- 多帧文件**逐帧**转换，帧数不变。
- 源和目标在同一家族内单帧字节数相同，这也是「无损」的由来。

---

<a id="s8-troubleshooting"></a>

## 8. 常见错误排查

### 8.1 `Error: failed to read image: xxx.bin`（最常见）

**现象**

```bash
raw-view convert -i ./input_bins/output_0x0b_grey.bin -o ./output/output_0x0b_grey.png \
    --target YUV --yuv-type YOnly10 --width 1296 --height 736
# Error: failed to read image: ./input_bins/output_0x0b_grey.bin
```

**原因**：`convert` 的 `--input-type` 默认是 `image`，程序去按 PNG/JPG 解码一个
YUV 二进制，必然失败。

**解决**：明确告诉 CLI 输入是什么。

```bash
# 方式 A：view（推荐，最直观）
raw-view view -i ./input_bins/output_0x0b_grey.bin \
    -o ./output/output_0x0b_grey.png \
    -t YUV --yuv-type YOnly10 --width 1296 --height 736

# 方式 B：convert + --input-type yuv（保留原命令形状）
raw-view convert -i ./input_bins/output_0x0b_grey.bin \
    -o ./output/output_0x0b_grey.png \
    --input-type yuv --input-yuv-type YOnly10 --width 1296 --height 736
```

> 现在这条错误信息本身就会打印上面几行可直接复制的命令，并回显你传的
> `--yuv-type` 与 `--width/--height`。

### 8.2 `Error: --input-type yuv cannot target RAW`

YUV 源只能转成 YUV 或图片。去掉 `-t RAW`（转码时 `--target` 可省略，自动为
`YUV`），或改用 `--input-type image`。

### 8.3 `Error: YOnly10 -> YOnly10 is a no-op`

同格式互转没有意义。要么换 `--yuv-type`，要么（如果本意是解码）把输出扩展名
改成 `.png`。

**输出扩展名决定走哪条路**：`.png/.jpg/.jpeg/.bmp/.tif/.tiff` → 解码成图片；
其它（含 `.webp`、无扩展名）→ 当作 YUV→YUV 重排。后者若你本意是解码，会看到这
条 no-op 错误，并在下面附一句 `... does not end in a writable image extension`。

### 8.3.1 `Error: a mode word is required: raw-view {view|convert|batch} ...`

命令里给了 CLI 参数（`--width` / `-i` / `-t` 等）但**没写 mode 词**。历史上这会
打开 GUI 并阻塞终端；现在直接报错并返回 **2**。加上 `view` / `convert` / `batch`
即可；只想开 GUI 就敲一个裸 `raw-view`。

### 8.4 `Error: ... is YUV 4:0:0 (luma only, no chroma planes)`

YOnly ↔ 4:2:x 不能无损互转，见[第 7.2 节](#s72-refused)。

### 8.5 `Error: ... changes chroma sampling`

跨 4:2:2 / 4:2:0 家族，见[第 7.2 节](#s72-refused)。先解码成图片。

### 8.6 `Error: input size N bytes is not an integer multiple of the M-byte ... frame`

尺寸填错了，或者文件带文件头。核对：

1. `--width/--height` 是否是源帧真实尺寸（不是期望的输出尺寸）；
2. 文件是否有头部 → 用 `--offset`；
3. 是否多帧文件 → 那是合法的，整倍数即可。

### 8.7 `Error: --input-type raw is reserved`

本轮不实现 RAW↔YUV。先把 RAW 解码成图片（`view`），再从图片编码成目标格式。

### 8.8 `Error: --width required with --input-type yuv`

`--input-type yuv` 下 width/height 是源帧布局，不能默认。

### 8.9 `Error: data too short: need N bytes, got M`

`view` 模式下文件不够一帧。检查 width/height/格式/offset。

### 8.10 `Error: invalid batch JSON: ... Expecting property name ...`

batch JSON 里有 `//` 注释或尾逗号。删掉它们（或用 `--help-batch` 里的示例）。

### 8.11 画面明显不对（偏亮、台阶、条纹）

- RAW/YUV 多 bit 格式的 `--alignment` 猜反了 → 试试 `lsb` / `msb`。
- `--endianness` 反了 → 试试 `big` / `little`。
- `--width` 差了 1 像素 → 整幅图的 stride 都会错位。
- RAW 彩色模式看到假彩色 → `--bayer-pattern` 与实际不符，或改用
  `--preview-mode Grayscale` 先确认明暗是否正常。

---

<a id="s9-exit-codes"></a>

## 9. 退出码与输出约定

| 退出码 | 含义 |
|---|---|
| `0` | 成功（batch 表示全部条目成功） |
| `1` | 运行失败（读不到文件、格式不支持、尺寸非法等） |
| `2` | 参数用法错误（argparse 的未知/缺参数/非法枚举值；以及**给了 CLI 参数却漏写 mode 词**） |

- **失败时报错本身只有一行 `Error: ...`**（其后可能跟随多行可复制的修复建议；
  ERROR 级日志不重复打印到终端），完整 traceback 写入日志文件
  （`%LOCALAPPDATA%\raw-view\logs\raw-view.log`，可用 `RAW_VIEW_LOG_DIR` 重定向）。
  `INFO:` 进度行是否出现在终端，由 `RAW_VIEW_LOG_LEVEL` 决定（默认会打印）。
- `RAW_VIEW_LOG_LEVEL` 控制控制台/文件日志级别。
- `RAW_VIEW_NO_GUI=1` 由 `raw-view-cli.exe` 自动设置：该可执行文件永远不会弹窗，
  需要 GUI 的调用会直接报 usage 并返回 2。
- 帮助文本全部为 ASCII，窄编码控制台（cp1252 / GBK）也能完整打印。
- 单帧 > 512 MB 在**任何写盘动作之前**拒绝。
- 可执行/脚本后缀（`.exe` `.dll` `.bat` `.ps1` 等）在 view / convert / batch 三条
  路径都被拒绝；无后缀和 `.dat`/`.bin` 等普通后缀不受影响。
