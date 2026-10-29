# raw-view

Python RAW/YUV 图像查看与格式转换工具。

> **当前版本：0.5.1** —— 正式发布通过 GitHub Releases 分发 Windows **单文件 exe + zip 压缩包**（见下方
> *发布与下载* 与 `docs/release_exe.md`）。

## 功能

- RAW 查看：RAW8/10/12/16/32、RAW10/12/14 Packed，支持 LSB/MSB 对齐、大小端与 Bayer(RGGB/GRBG/GBRG/BGGR)彩色预览
- YUV 查看：**YOnly**（YUV 4:0:0 灰度，单个格式 + Bit depth 配置 8/10/12/14/16——8 为 1 字节/像素，10/12/14/16 为 16-bit 存储，支持 LSB/MSB 对齐与大小端）、I420/YV12/NV12/NV21/YUYV/UYVY/YVYU/VYUY/NV16/NV61
- 文件大小校验、偏移解析、缩放查看、导出 PNG/JPEG（支持设置 DPI）
- 图片转换：PNG/JPEG/BMP -> RAW（支持 Bayer Pattern 选择，可选灰度）或 YUV
- **批量转换**：支持多文件批量转换，进度条显示，统一参数设置，转换报告
- **多变体一键生成**：可在 Settings 中开启 *Enable multi-variant generation*，之后 Convert / Batch Convert 对话框会出现勾选面板——一张图片一次生成多种 **format**（RAW8/10/12/… + YUV）× **bayer pattern**（RGGB/GRBG/GBRG/BGGR）× **size**（预设常用分辨率 + 自定义）的组合。YUV 无 Bayer，因此每个尺寸只出一个文件；RAW 灰度源（`source_mode=gray`）同样不按 Bayer 展开。默认关闭，即每次只生成一个文件
- **转换预览**：Convert 对话框中显示原图缩略图及目标格式帧大小信息
- **输出模板命名**：支持丰富占位符（`{format}` `{bayer}` `{bits}` `{packed}` `{raw_type}` `{yuv_type}` `{alignment}` `{endianness}` 等），默认 `{input_stem}_{width}x{height}_{format}{ext}` → 例如 `image_2560x1440_BGGR10P.raw` / `image_1920x1080_YUYV.yuv`，可在 Settings 中自定义（详见 *输出文件名模板* 小节）
- **CLI 模式**：支持命令行解码 RAW/YUV→PNG/JPEG（`python -m raw_view view`）、编码 image→RAW/YUV（`convert`）、批量模式（`batch`）、启动 GUI 并打开文件
- 支持主界面拖拽打开文件、拖拽文件夹自动扫描 RAW/YUV 文件、拖入时高亮窗口边框视觉反馈
- 支持转换输入拖拽
- 支持多标签页 item：可同时打开多文件、独立参数、关闭单个 item；**点住标签名称可左右拖动排序**
- 支持 Recent Files 最近文件列表
- Convert 输出支持默认 `convert_out` 目录（可在 Settings 调整）与手动更改
- 内置 Help：格式排列、Packed bit 规则与示例
- 默认显示为 Fit to Window，可自行缩放
- **帧导航**：图像下方提供 ⏮ 首帧 / ‹ 上帧 / 帧号输入框（`1 / N`）/ › 下帧 / ⏭ 末帧 控件；键盘 ←/↑ 上一帧、→/↓ 下一帧、Home 首帧、End 末帧；自动检测总帧数，帧号从 1 开始
- **缩放控件**：缩放滑块（10%-1000%，实线轨道 + 圆形手柄）+ 可编辑数值框（滑动粗调、输入精调）、双击图像切换 Fit/1:1
- **可拖拽分栏**：左侧参数面板与右侧图像预览之间为可拖动分隔条，自由调整两者宽度
- **全屏模式**：F11 进入/退出全屏（菜单项动态切换 Fullscreen / Exit Fullscreen 并显示勾选），Escape 退出
- **图像旋转/翻转**：旋转 90°（Ctrl+R / Ctrl+Shift+R）、水平翻转（Ctrl+H）、垂直翻转（Ctrl+Shift+V）
- **标签页切换**：Ctrl+Tab / Ctrl+Shift+Tab 或右键菜单切换多文件标签页
- **现代主题**：基于 qt-material 的 Material Design 界面，统一 Material 蓝（`#1976D2`）主色（滑块/滚动条/菜单高亮/下拉箭头/复选勾选/按钮全部同色），卡片式圆角面板叠加层，Light/Dark 双主题（**默认 Dark**，深色下输入框/文字满足 WCAG AA 对比度）；Windows 标题栏随主题变深/变浅（DWM 沉浸式深色）；按钮分三级：主操作实心（Primary）、次操作描边（Secondary）、危险操作红色描边（Danger）、跳转操作文字链接
- **状态栏状态点**：Ready/解码成功=绿色、解码中=琥珀色、参数已改未 Apply=琥珀色、错误=红色圆点，一眼感知系统状态；未加载文件时预览区显示空状态引导（图标 + 说明 + Open File 按钮）
- **转换反馈**：Convert 转换中按钮显示 “Converting…” 并禁用防重复点击，完成后提示 ✅ 输出路径 + 文件大小、失败提示 ❌ 原因；Generate Variants 显示 “Generating n/N…” 进度；批量转换显示逐文件进度与汇总报告
- **应用图标 / Logo**：内置 indigo/violet 传感器 Bayer 马赛克风格 logo（`assets/logo.svg`），用作窗口/任务栏图标与工具栏品牌标识；打包 exe 使用 `assets/raw-view.ico`
- **日志系统**：文件日志（RotatingFileHandler，最大 5MB，保留 3 份）+ 控制台日志，记录解码错误、转换异常、崩溃信息
- **Sensor 预设**：可把任意一组解码参数（type / format / alignment / endianness / preview / Bayer / width / height / offset）保存为命名预设；下次打开 RAW 时在面板顶部 **Preset** 下拉框中选择，即可自动填充所有字段（**填充后需点击 Apply 才会生效渲染**，避免误刷新）。主界面只负责选择：右侧漏斗按钮可在 **All / RAW / YUV / Standard images** 四类间切换浏览范围，下拉列表按最近使用优先、其余按名称排序；名称/结构化搜索只在 **Manage presets** 管理对话框中进行。保存与管理通过左侧控制面板的右键菜单进入；管理对话框支持 Rename / Import / Export 与右键操作，双击列表项可就地重命名
- **RAW Packed 标准布局**：RAW10P / RAW12P / RAW14P 的解码与编码均遵循 MIPI CSI-2 标准布局（B0..Bn-1 = 高 8bit，末字节 = LSBs，MSB-first），与真实 sensor 数据互通
- **FourCC 查找工具**：Tools → FourCC Lookup，浏览 FourCC ↔ 描述 ↔ MBUS 名称/值的对应关系，支持实时搜索（任意字段）和自定义格式管理（添加/编辑/删除，持久化存储）

## 安装

```bash
pip install -r requirements.txt
```

> 若要复现 CI/发布所用的精确版本，叠加 `constraints.txt` 锁定：
> `pip install -r requirements.txt -c constraints.txt`
> （默认本地宽松安装会拿到当时最新版本，可能与 CI 锁定版本组合略有差异。）

## 运行

### GUI 模式

```bash
python -m raw_view
python -m raw_view view    # same as above
```

### CLI View 模式（RAW/YUV → PNG/JPEG）

```bash
# RAW → PNG，指定所有参数
python -m raw_view view \
    -i input.raw -o output.png \
    --target RAW \
    --raw-type RAW12 \
    --width 1920 --height 1080 \
    --alignment msb \
    --endianness little \
    --preview-mode "Bayer Color" \
    --bayer-pattern RGGB

# YUV → JPEG
python -m raw_view view \
    -i input.yuv -o output.jpg \
    --target YUV \
    --yuv-type NV12 \
    --width 1280 --height 720

# 仅指定输入，自动生成输出到 view_out/ 目录
python -m raw_view view -i input.raw --target RAW --width 1920 --height 1080

# 灰度预览输出
python -m raw_view view -i input.raw -o gray.png --target RAW --preview-mode Grayscale

# 无 -i 时启动交互式 GUI（可同时打开文件）
python -m raw_view view
python -m raw_view view file1.raw file2.png
python -m raw_view file1.raw file2.png
```

### CLI 转换模式（Image → RAW/YUV）

```bash
# 单文件转换（所有参数）
python -m raw_view convert \
    -i image.png \
    -o output.raw \
    --target RAW \
    --raw-type RAW12 \
    --width 1920 --height 1080 \
    --alignment msb \
    --endianness little \
    --source-mode bayer \
    --bayer-pattern RGGB

# 转换为 YUV
python -m raw_view convert \
    -i image.png \
    --target YUV \
    --yuv-type NV12 \
    --width 640 --height 480

# 仅指定输入，自动生成输出到 convert_out/ 目录
python -m raw_view convert -i image.jpg --target RAW --width 1920 --height 1080

# 灰度模式 + 大端
python -m raw_view convert -i image.png --target RAW --source-mode gray --endianness big
```

### CLI 批量模式（支持 view + convert 混合，每文件独立参数）

```bash
python -m raw_view batch --batch-file batch.json
python -m raw_view --batch-help   # 查看 JSON 格式说明
```

`batch.json` 支持**全局默认 + 每文件覆盖**，且每个文件可独立指定 `mode`：

```json
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "yuv_type": "YUYV",
  "width": 640,
  "height": 480,
  "alignment": "lsb",
  "endianness": "little",
  "source_mode": "bayer",
  "bayer_pattern": "RGGB",
  "preview_mode": "Bayer Color",
  "offset": 0,
  "files": [
    {"input": "img1.png"},
    {
      "input": "img2.jpg",
      "output": "custom_out.raw",
      "width": 1920,
      "height": 1080,
      "alignment": "msb"
    },
    {
      "input": "img3.png",
      "target": "YUV",
      "yuv_type": "NV12",
      "width": 1280,
      "height": 720
    },
    {
      "input": "image.raw",
      "mode": "view",
      "width": 1920,
      "height": 1080,
      "bayer_pattern": "BGGR",
      "preview_mode": "Grayscale"
    },
    {
      "input": "video.yuv",
      "mode": "view",
      "target": "YUV",
      "yuv_type": "NV12",
      "width": 1920,
      "height": 1080
    }
  ]
}
```

- `files[].output` 可选，省略时自动生成
- 每文件可通过 `"mode": "view"` 或 `"mode": "convert"` 指定操作
- 每文件可覆盖任意字段（`target`, `width`, `height`, `raw_type`, `yuv_type`, `alignment`, `endianness`, `source_mode`, `bayer_pattern`, `preview_mode`, `offset`）

## 设置（Settings）

- `Default output folder`：转换默认输出子目录名（默认 `out`），旁边提供 **Browse...** 按钮选择文件夹
- `Output filename template`：输出文件命名模板（默认 `{input_stem}_{width}x{height}_{format}{ext}`），右侧 **Reset** 按钮（重置前二次确认）+ **ⓘ** 图标 hover 显示完整占位符列表；模板过长时字段悬停显示完整内容
- `Saved image DPI`：导出 PNG/JPEG 的目标 DPI（默认 300，范围 72–2400）
- `UI font size`：主界面字体大小（默认 13，范围 10–24 px）
- `UI theme`：界面主题下拉框（`Light` / `Dark`，默认 `Dark`，基于 qt-material Material 蓝 + 自定义叠加层）
- `Convert variants`：复选框，开启 *Enable multi-variant generation* 后，Convert / Batch Convert 对话框出现多选面板（旁有 **ⓘ** 说明），一张图片一次生成多种 format × bayer × size 组合（默认关闭，详见 *多变体一键生成* 小节）
- `Manage sensor presets`：文字链接，打开 Sensor 预设管理对话框（详见下文 *Sensor 预设*）
- 存在未保存修改时关闭对话框（× / Esc / Cancel）会弹出 **保存 / 不保存 / 取消** 确认
- 按钮统一为 主操作实心 / 次操作描边 两级样式，且与相邻输入框等高；工具栏图标基于 QtAwesome Font Awesome 图标集（PyQt5 兼容）

## 输出文件名模板

Convert / Batch convert / CLI 全部走同一个模板系统。模板字符串保存在 Settings 的 `Output filename template` 字段（QSettings key `convert/output_template`），默认值：

```
{input_stem}_{width}x{height}_{format}{ext}
```

### 占位符全集

| 占位符 | 含义 | 何时为空 |
|---|---|---|
| `{input_stem}` | 输入文件名（不含扩展名） | 几乎不为空 |
| `{width}` | 输出图像宽度 | — |
| `{height}` | 输出图像高度 | — |
| `{ext}` | 输出文件扩展名（`.raw` / `.yuv` / `.png` / …） | — |
| `{date}` | 当前日期 `YYYYMMDD` | — |
| `{time}` | 当前时间 `HHMMSS` | — |
| `{format}` | **简短综合标签**：RAW Bayer 源 → `{bayer}{bits}{packed}`，例 `BGGR10P`；RAW 灰度源 → `{raw_type}`，例 `RAW12`；YUV → `{yuv_type}`，例 `YUYV` | 未指定 target 时 |
| `{bayer}` | Bayer 排列大写 `RGGB` / `BGGR` / `GRBG` / `GBRG` | RAW 灰度源 / YUV / 未指定时为空 |
| `{bits}` | 位深度 `8` / `10` / `12` / `14` / `16` | YUV / 未知 RAW 类型时为空 |
| `{packed}` | RAW packed 标记 `P` | 非 packed 格式时为空 |
| `{raw_type}` | 原 RAW 类型去空格，例 `RAW10Packed` | YUV / 未指定时为空 |
| `{yuv_type}` | YUV 子格式大写，例 `YUYV` / `NV12` | RAW / 未指定时为空 |
| `{alignment}` | `lsb` / `msb` | 未传时为空 |
| `{endianness}` | `little` / `big` | 未传时为空 |

> 占位符大小写敏感；未识别的占位符会原样保留。`{bayer}` 仅在源是 Bayer 模式（`source_mode = bayer`）时输出，灰度源不会写入误导性的 Bayer 名称。

### 常用样例

| 想要的文件名 | 模板 |
|---|---|
| `image_2560x1440_BGGR10P.raw`（默认） | `{input_stem}_{width}x{height}_{format}{ext}` |
| `image_2560x1440_BGGR_10P_msb.raw`（含对齐） | `{input_stem}_{width}x{height}_{bayer}_{bits}{packed}_{alignment}{ext}` |
| `image_BGGR10P_msb_little_2560x1440.raw`（全细节） | `{input_stem}_{bayer}{bits}{packed}_{alignment}_{endianness}_{width}x{height}{ext}` |
| `20260601_142500_image_RAW12.raw`（保留时间戳） | `{date}_{time}_{input_stem}_{format}{ext}` |
| `image_1920x1080_YUYV.yuv` | `{input_stem}_{width}x{height}_{format}{ext}`（同默认） |

> Tip：默认模板有意把 `{alignment}` / `{endianness}` 排除——大多数同事一眼想看的是"分辨率 + Bayer + bit 位 + 是否 packed"。如果你的工作流需要在文件名中区分大小端 / 对齐，按需把这两个占位符加进自定义模板即可。

### 升级行为：默认模板自动迁移

修改 `DEFAULT_OUTPUT_TEMPLATE` 不会自动覆盖已经写入 QSettings 的旧值（QSettings 的 `value(key, default)` 只在 key 不存在时才用默认值），所以**老用户首次升级后，Settings → Output filename template 仍会显示旧默认**。raw-view 在 `AppSettings.output_template` 读取时做了一次性迁移：

- 如果存储值正好等于历史默认（`{date}_{time}_{input_stem}_{width}x{height}{ext}` 等），自动改写为当前 `DEFAULT_OUTPUT_TEMPLATE` 并回写注册表。
- 如果是用户自定义的模板，**不会**被改写——你的个性化设置一定会保留。

如果你想立刻强制回到默认，在 Settings 对话框里点 **Reset** 按钮（紧挨着模板输入框），保存后即可生效。

## Sensor 预设（一键 apply）

为了避免每次打开新 sensor 的 raw 文件都要重复填写一整套参数，主面板顶部提供 **Preset** 行：

- **Preset 下拉框**：只用于选择，不能输入搜索。选择后会把所有字段写入面板；请再点击 **Apply** 才会渲染当前文件。列表按**最近使用优先**排序，未使用过的名称按 A→Z / 拼音排序。
- **筛选按钮（漏斗图标）**：点击后在 `All`、`RAW`、`YUV`、`Standard images` 四类间切换浏览范围；tooltip 显示当前类型。若当前已选 preset 不在新范围内，下拉框显示 `(Current preset hidden by filter)`，面板已填字段保持不变，也不会静默加载其它 preset。
- **右键菜单**：在左侧控制面板空白/非文本控件上右键，可选择 **Save current as preset…**、**Manage presets…**、**Pin parameters**、**Apply these parameters to all items**、**Forget remembered parameters for this file**；文本框、数值框与下拉框保留各自的原生右键菜单。
- **管理对话框**：独立保留 **Preset type** 行与搜索框；列表按名称 A→Z（中文按拼音）排序。名称可直接输入搜索，也支持仅尖括号内的结构化条件：`<type:VALUE format:VALUE>名称`。`type`/`t`、`format`/`f` 是唯一支持的键，条件之间用**空格**分隔（旧写法 `+` 仍兼容），条件与尖括号外的名称 AND。例如 `<t:yuv f:yuyv>`、`<type:raw format:raw10 packed>name`、`< t : yuv   f : raw10packed > NAME`。大小写不敏感，格式比较忽略空白；裸 `type:yuv` 只是普通名称搜索。语法错误、未知键或未知 type 显示无结果，避免意外扩大范围。

未打开文件时也可以通过 **Settings → Manage sensor presets** 进入相同的管理对话框。

## 按文件名记忆参数（0.5.0）

- **记录时机**：只有**用户显式 Apply** 且该次解码成功才会记录。仅修改面板、选择 preset、翻帧、打开文件时的自动/静默解码、恢复记忆后的自动解码都不会写入。**Apply these parameters to all items** 属于显式批量 Apply：每个成功项按各自文件名记录（失败项不记录）。关闭标签或退出程序时，会把已成功 Apply 的快照再同步一次（幂等）。
- **记录键**：只取**文件名**（basename，不含路径），并用 `str.casefold()` 做**平台无关**的大小写归一（Windows 与 Linux 行为一致，CI 可覆盖该语义）。同一文件名在不同目录打开时共用记录。
- **大小写归一的边界**：常规 ASCII / 中文文件名（如 `Capture.RAW` ≡ `capture.raw`、`测试.RAW` ≡ `测试.raw`）与 Windows 原生规则一致。`casefold()` 对少数非 ASCII 字符与 Windows 原生规则有差异：
  - **扩张折叠**（1 个字符变多个）：`ß`→`ss`、`ﬁ`→`fi`。此时 `straße.raw` 与 `strasse.raw` 在 NTFS 上是两个不同文件，却会共用同一条记忆。
  - **简单折叠**（1 对 1，但结果与 Windows 不同）：`µ`→`μ`、`ς`→`σ`、`ſ`→`s`、U+212A `K`→`k`。

  这是为「平台一致 + CI 可验证」而有意选择的语义。
- **上限**：最多 **100** 条，超出时丢弃 `last_used` 最旧的条目（LRU）；更新已有记录会刷新时间戳并移到最新。
- **读取时机**：`_open_item`（打开对话框 / 拖放 / CLI / 最近文件）命中记录时，直接把 item 初始参数设为该记录并**自动解码一次**；沿用首次自动解码的静默错误策略。状态栏分别显示：
  - 成功：`Restored remembered parameters (文件名)`
  - 失败：`Restored remembered parameters, but decoding failed`（不弹窗，用户改参数再 Apply 即可）
- 未命中时维持默认 2560×1440。
- **忘记单条**：左侧参数面板右键 → *Forget remembered parameters for this file*（仅当当前文件有记录时可用）。
- 损坏/缺失/非法 JSON 均安全退化，不影响启动。

## 参数钉（Pin parameters，0.5.0）

- **两套参数**：每个 item 有自己的本体参数；另有一个全局“参数钉参数”。钉住后切换标签时，面板显示钉参数，**item 本体不被修改**。
- 点 **Apply** 才把面板（钉）参数写入当前 item 的本体并解码；解码成功后钉参数随之更新为刚应用的参数（“钉子跟随最新成功 Apply”）。
- **取消钉住**：面板立即恢复显示当前 item 的本体参数。
- **入口**：Preset 行右侧图钉按钮，或快捷键 **Ctrl+N**（文本输入框、数值框、下拉框获得焦点时不劫持）；按钮带 hover/pressed 背景，两种主题下都清晰可见。
- **三态配色**（随 Light/Dark 主题刷新，均保证与背景对比度 ≥3:1）：
  - **未钉**：主题正文色（Dark 亮灰 / Light 深灰）；
  - **已钉且当前文件已是钉参数**：蓝色（Light `#1976D2` / Dark `#4A90D9`），无需再操作；
  - **已钉但当前文件尚未应用**（含面板有未应用修改）：橙色（Light `#B45309` / Dark `#FBBF24`），提示按 Apply。
  - 悬停 tooltip 与无障碍文案同步说明三态。
- **状态栏不显示钉状态**：状态栏只反映当前 item 的解码状态（Ready / Decoded / Decode failed / Unapplied changes 等）；开关钉前后状态栏文本与颜色完全不变，钉状态只由图钉颜色与 tooltip 表达。
- **未成功的 Apply 不改本体参数**：取消尺寸不匹配弹窗（选 No）、格式不支持、Frame Too Large、读取失败、解码 worker 报错等任何未成功的 Apply，都会让该 item 保持 Apply 之前的本体参数与帧号；解锁钉后看到的仍是该文件原有的值。只有真正解码成功才提交参数。
- **钉住时打开新文件**：只让面板显示钉参数（图标转为橙色 pending），**不会**改写新打开文件的本体参数；点 Apply 后才会写入。若该文件名有“上次 Apply 参数”记忆，本体仍按记忆值初始化并静默解码（记忆查询与钉住无关），面板显示的才是钉参数。
- **Standard Image 的蓝色可达**：标准图在解码后才拿到真实尺寸（例如 7×5），面板里用户输入的 8×8 只是无意义的占位。**Apply 成功后**，真实宽高会回写到面板宽度/高度框（静默回写，不会把标签标成未保存），这样“钉 = 本体 = 面板”三者在标准图上也能成立，图钉可以真正变蓝。RAW/YUV 不受影响，因为它们的宽高是用户真实输入。
- **关闭全部标签时复位**：`Close All Items` 后没有当前 item，图钉立刻回到未钉/待应用状态，不会残留上一个文件的蓝色与“already applied to this file”提示。
- **持久化**：钉住状态与钉参数值写入 Settings（`params/pinned`），重启后保留；无打开文件时钉参数仍在，打开第一个文件即按钉语义显示。
- **应用全部**：右键 → *Apply these parameters to all items*，会把当前面板参数写入所有打开的 item 并**串行逐个解码**（单 worker 排队）；失败项不弹窗、不打断其它项，最终状态栏汇总“Applied to N item(s), M failed”。首次执行有确认框，可勾选 “Apply and don't ask again” 记住本次会话。

### 存储位置

预设以 JSON 形式存储在 **QSettings**（不在仓库 / 不在 exe 内部）：

| 平台 | 位置 |
|---|---|
| Windows | 注册表 `HKEY_CURRENT_USER\Software\yorelll\raw-view`，键 `presets\sensors` |
| macOS | `~/Library/Preferences/com.yorelll.raw-view.plist` |
| Linux | `~/.config/yorelll/raw-view.conf` |

跨会话自动保留，重启 / 升级 exe 后仍然存在；卸载 / 删除注册表键后会丢失。

### Import / Export（团队共享）

Preset 管理对话框底部提供 **Import** 与 **Export** 两个按钮：

- **Export**：把当前所有预设序列化为 JSON 文件（默认文件名 `raw-view-presets.json`），可发给同事或随 exe 一起分发；导出包含尚未保存的对话框内编辑。
- **Import**：选择 JSON 文件后，逐条合并到本机预设。若发现重名，弹窗询问 **Overwrite** / **Skip duplicates** / **Cancel**。导入完成后还需要点 **Save** 才会真正写入注册表/配置文件。

**只需要导入一次**：写入注册表后跟用户自己手动 Save 出来的预设没有区别，下次打开 exe 仍在；用户也可以继续新增、修改自己的预设。
重新导入只在换电脑、重装系统、或想恢复"团队标准版本"时才需要。

> 注意：导出的 `*.json` 文件已经加入 `.gitignore`（`raw-view-presets*.json` / `*sensor-presets*.json` 等），默认不会被 commit。打包发布的 exe 也**不会**带上这个 JSON——团队预设需要单独分发，详见 `docs/release_exe.md`。

> 示例：创建 `401ai`：Type=RAW、Format=RAW10 Packed、Alignment=msb、Endianness=little、RAW preview=Bayer Color、Bayer pattern=BGGR、Width=2560、Height=1440、Offset=0。

## 多变体一键生成

一张源图想同时导出 RAW8 / RAW10 / RAW12、多个 Bayer 排列、多个分辨率时，无需重复操作。

1. 打开 **Settings**，勾选 **Enable multi-variant generation**，保存。
2. 打开 **Convert Image...**（单图）或 **Batch Convert...**（多图），底部会出现勾选面板：
   - **Formats (RAW)** / **Formats (YUV)**：勾选要生成的所有格式。
   - **Bayer patterns**：仅对 RAW 且 `RAW source = bayer` 生效；灰度源与 YUV 会被忽略。
   - **Sizes**：勾选常用分辨率；在该区域 **右键** 可 *Add custom size…* 添加自定义尺寸或删除已添加的自定义尺寸（自定义尺寸以蓝色标注，重复添加会高亮闪烁提示，标题旁 **ⓘ** 有说明）。
3. Convert 对话框点 **Generate Variants**；Batch 对话框点 **Start Batch Convert**（每个输入文件都会展开成全部组合）。

生成规则示例：勾选 `RAW8` `RAW10` `YUYV` + `RGGB` `GRBG` + `2560x1440` `1920x1080`，得到：

- RAW8：2×2 = 4 个（两个 Bayer × 两个尺寸）
- RAW10：4 个
- YUYV：无 Bayer，只按尺寸 = 2 个

共 10 个文件。文件命名沿用 *输出文件名模板*，默认模板已包含 `{format}`（含 Bayer + bit + packed），因此不会互相覆盖。输出目录为 Settings 中的默认转换目录（Batch 勾选 *Same directory as input* 时改为输入文件同目录）。

> 关闭该设置后，Convert / Batch 恢复默认行为——每次只生成一个文件。

## FourCC 查找工具

Tools → **FourCC Lookup** 打开 FourCC 格式查找对话框，用于快速查询视频/图像 sensor 格式的 FourCC 编码、别名、描述、MBUS 名称和 MBUS 值之间的对应关系。

### 功能

- **格式列表**：以表格形式列出所有支持的格式（YUV 系列、Bayer 8/10/12/16-bit packed/unpacked、Monochrome 等），每行显示 FourCC、Alias、Description、MBUS Name、MBUS Value。
- **实时搜索**：在搜索框中输入任意关键词（FourCC、别名、描述、MBUS 名称或 MBUS 值），表格即时过滤匹配的行。支持匹配任意字段。
- **自定义格式管理**：用户可添加、编辑、删除自定义格式条目。自定义条目以*斜体*显示，与内置条目视觉区分。自定义格式通过 QSettings 持久化存储，重启后保留。
- **增删改操作**：
  - **Add Custom**：弹出表单输入 FourCC、Aliases（逗号分隔）、Description、MBUS Name、MBUS Value，保存为自定义条目。
  - **Edit**：仅可用于自定义条目，修改已有条目的任意字段。
  - **Delete**：删除选中的自定义条目（内置条目不可删改）。

### 内置格式

工具内置 40+ 常见格式，涵盖：

| 类别 | 格式 |
|---|---|
| YUV 4:2:0 | I420, YV12, NV12, NV21, NM12 |
| YUV 4:2:2 | YUYV, UYVY, YVYU, VYUY, NV16, NV61, NM16, NM61 |
| Monochrome | GREY, Y10, Y12 |
| Bayer 8-bit | BA81(BGGR8), GBRG(GBRG8), GRBG(GRBG8), RGGB(RGGB8) |
| Bayer 10-bit Packed | pBAA(BGGR10P), pGAA(GBRG10P), pgAA(GRBG10P), pRAA(RGGB10P) |
| Bayer 10-bit @16-bit | BG10(BGGR10), GB10(GBRG10), BA10(GRBG10), RG10(RGGB10) |
| Bayer 12-bit Packed | pBCC(BGGR12P), pGCC(GBRG12P), pgCC(GRBG12P), pRCC(RGGB12P) |
| Bayer 12-bit @16-bit | BG12(BGGR12), GB12(GBRG12), BA12(GRBG12), RG12(RGGB12) |
| Bayer 16-bit | BYR2(BGGR16), GB16(GBRG16), GR16(GRBG16), RG16(RGGB16) |

> 自定义格式由用户通过 Add Custom 添加，不会在此次重置或升级时丢失（存储在 QSettings 中）。常见的 Realtek FBC compressed 格式（如 FBA8/FGA8/FBC8 等）可作为自定义格式自行添加。

## 默认参数

- RAW 格式默认：RAW12、MSB 对齐、2560×1440
- YUV 格式默认：YUYV
- 帧号显示与状态栏均从 1 开始计数

## 打包为 EXE

详见：`docs/release_exe.md`

- **建议的本地打包**：双击仓库根目录 `release.bat`。脚本会先运行全量测试，再生成单文件 exe、解压即用 zip 与 SHA-256 校验和（详见 `docs/release_exe.md`）。
- **手动本地打包**：`pyinstaller --noconfirm --clean --onefile --windowed --name raw-view ...`（完整命令见文档）。
- **GitHub Actions 自动发布**：推送形如 `v0.5.1` 的 tag 后自动构建并发布到 Releases（见下）。

## 发布与下载

发布由 GitHub Actions 自动完成（`.github/workflows/build-release.yml`）：

1. 提交并推送代码；
2. 打 tag：`git tag v0.2.0 && git push origin v0.2.0`；
3. 工作流自动运行测试 → 打包 **两种发行物** → 生成 SHA-256 校验和 → 发布到 **Releases**；
4. 到仓库 [Releases 页面](https://github.com/yorelll/raw-view/releases) 下载即可。

**发行物形态**：
- **`raw-view.exe`** — 单文件 exe（约 100-130 MB，无需安装 Python，自解压启动）
- **`raw-view-<版本>-windows-x64.zip`** — onedir 压缩包（解压即用；内含 `raw-view/_internal/` 全部依赖；
  适合需要快速分发整个目录、或单文件版启动慢的场景）

手动触发（不打 tag）也可以在 Actions 页选择 `workflow_dispatch` 构建设置开发版。

## 代码文档

> 说明：`docs/review/`、`docs/summary/`、`docs/improvement/` 为**本地产出文档**（已加入
> `.gitignore`，不随仓库分发），用于记录代码审查、架构总结与改进建议，随开发迭代在本地维护。

| 文档 | 说明 |
|---|---|
| `docs/review/review_report.md` | 详尽代码 Review 报告（H/M/L 分级问题、取舍结论与修复状态 checkbox）——本地 |
| `docs/summary/architecture_summary.md` | 代码架构 / 功能 / 实现逻辑总结——本地 |
| `docs/improvement/improvement_proposals.md` | 功能、逻辑、UI、用户体验改进建议与路线图——本地 |
| `docs/future_extensions.md` | 深层次功能扩展建议（随仓库） |
| `docs/release_exe.md` | EXE 打包与 GitHub Actions 发布说明（随仓库） |

## 后续功能扩展建议

详见：`docs/future_extensions.md` 与本地的 `docs/improvement/improvement_proposals.md`

## 测试

```bash
python -m unittest discover -s tests -q
```

> 每个 push / PR 由 GitHub Actions（`.github/workflows/ci.yml`）自动跑全量测试
> （Windows + Ubuntu × Python 3.11/3.12 矩阵），tag 则触发 `build-release.yml` 打包发布。
