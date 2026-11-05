# 将 raw-view 打包为 EXE（Windows）

> 仓库托管在 GitHub（`github.com/yorelll/raw-view`），**推荐用 GitHub Actions 自动发布**
> （打 tag → 自动构建 → 生成 exe + 校验和 → 发布到 Releases）。见文末《GitHub Release 版本发布》。
> 本文件前 6 节描述本地手动打包方式，供离线/调试场景参考。

## 0. 推荐：GitHub Actions 一键发布（0.x 版本正式发布路径）

1. 推送代码到任意分支 / 提 PR，CI（`.github/workflows/ci.yml`）自动跑**全量测试**（Windows + Ubuntu × Python 3.11/3.12 矩阵，含 CLI 冒烟与 view/convert round-trip），确保改动无回归。
2. 推送到 `main`、自测通过后打 tag 并推送：
   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```
3. GitHub Actions 工作流 `.github/workflows/build-release.yml`（tag 触发）自动：
   - Windows 环境安装 Python 3.12 + 全部依赖（叠加 `constraints.txt` 锁定版本）；
   - 运行 `python -m unittest discover -s tests -q`（**测试不过不发布**）；
   - 用 PyInstaller **打包一次**（同一份 `raw-view.spec`），一次 Analysis 产出全部四个 exe：
     | 构建 | 入口 | 产物 |
     |---|---|---|
     | onedir `--windowed` | `raw_view/launcher.py` | `raw-view.exe`（launcher） |
     | onedir `--windowed` | `raw_view/gui_entry.py` | `raw-view-gui.exe` |
     | onedir `--console` | `raw_view/cli_entry.py` | `raw-view-cli.exe` |
     | onefile `--windowed` | `raw_view/gui_entry.py` | `raw-view.exe`（单文件，仅 GUI） |
   - 三个 onedir exe 由**同一次 `COLLECT`** 产出，因此天然与**同一份 `_internal/`** 同级，
     不需要任何 staging / move / copy 组装步骤；
   - 校验 PE subsystem 必须为 **2 / 2 / 3**（launcher / GUI / CLI）；
   - 把目录压缩为 `raw-view-<版本>-windows-x64.zip`（解压即用）；
   - 生成 `raw-view.exe.sha256` 与 zip 的 `.sha256` 校验和；
   - 创建/更新 Release（名称 `raw-view <版本>`，包含 **zip + exe + 两个校验和** + 版本说明）。
4. 到仓库 **Releases** 页面即可看到并下载 `raw-view-<版本>-windows-x64.zip`（推荐，解压即用）或 `raw-view.exe`（单文件）。

手动触发（不打 tag）也可在 Actions 页面选 `workflow_dispatch` 构建设置。

> 说明：`raw-view.spec` 因含本机绝对路径被 `.gitignore` 忽略，CI 使用等价命令行参数打包；
> 两者产物一致。相关代码文档见本地 `docs/{review,summary,improvement}`（已加入 `.gitignore`，不上传远程）。

---

## 0.5 建议：双击 `release.bat` 本地打包

仓库根目录的 [`release.bat`](../release.bat) 是推荐的**本地发布打包助手**。在
Windows 中双击（或在 `cmd` 中执行 `release.bat`）会自动：

1. 激活项目 `.venv`；
2. 运行 `python -m pytest tests/ -q`，测试失败则中止；
3. 读取 `raw_view.models.APP_VERSION`；
4. 清理旧的 `build/`、`dist/`（**清理失败直接中止**，不再静默继续）；
5. 用 `raw-view.spec` **打包一次**，一次 Analysis 产出全部四个 exe，
   PyInstaller 直接把三个 onedir exe 与同一份 `_internal/` 写进 `dist/raw-view/`；
6. 生成 `dist/raw-view.exe`、`dist/raw-view-<版本>-windows-x64.zip` 及各自 `.sha256`。

> **为什么第 4 步必须中止而不是继续**：旧脚本用 `rmdir /s /q dist` 清理，失败时
> 不报错就继续；紧接着 `move` 一个构建目录到 `dist\raw-view`，若该路径已存在
> （例如被资源管理器或终端占用而没删掉），`move` 会把目录**多套一层**，exe 就落在
> `dist\raw-view\raw-view\`，与 `_internal` 差一层，运行时报
> `Failed to load Python DLL ...\_internal\python312.dll`。现在布局由 PyInstaller
> 直接产出、没有 `move`，并在清理失败时明确报错停下。

> 这是**本地打包**工具，不会提交、推送或创建 GitHub Release。正式远程发布仍应按上文流程：
> 提交 → push main → CI 通过 → 打 `v<版本>` tag，使 GitHub Actions 生成官方 Release。
>
> 前提：已创建 `.venv` 并安装依赖与 PyInstaller（见下方准备环境）。脚本根据当前
> `APP_VERSION` 自动命名产物，避免手工修改版本号。

---

## 0.6 三种 exe 与共享 `_internal`（重要）

zip 包里是**三个可执行文件 + 一份 `_internal/`**：

```
raw-view/
├── raw-view.exe        launcher   GUI 子系统  无参数/view → GUI；命令/选项 → 转交 CLI
├── raw-view-gui.exe    GUI only   GUI 子系统
├── raw-view-cli.exe    CLI only   Console 子系统（不会弹窗）
└── _internal/          三者共用（依赖树只存一份）
```

**为什么需要 launcher**：Windows 由**被启动的那个 exe** 的 PE subsystem 决定控制台行为。
GUI 子系统写不了终端，Console 子系统双击时弹黑框，一个二进制无法兼顾，所以用 launcher 分流。

**为什么必须共享 `_internal`**：三个 exe 的依赖树实质相同（早期实测两次独立 onedir
构建的 `_internal` 共 3177 个文件、3176 个逐字节相同）。若各带一份，zip 会从约 114 MB
涨到约 380 MB。现在这不再靠"事后组装"来保证——三个 onedir exe 由 `raw-view.spec`
里**同一次 `COLLECT`** 产出，共享同一份 `_internal/` 是构建方式的必然结果。

**launcher 的终端处理**（最容易做错的地方）：必须**先包装继承来的 std 句柄，再按需
`AttachConsole`**。顺序反过来会让 `> file` 与 `| pipe` 都变成 0 字节——`AttachConsole`
会覆盖继承的重定向/管道句柄。实现见 `raw_view/launcher.py` 的 `_forward()`。

**分流规则**（`raw_view/launcher.py::decide_target`，纯函数、可单测）：

| 输入 | 目标 |
|---|---|
| 无参数 / `view` / `view f1 f2…` | GUI |
| `-h` `--help` `--version` `--batch-help` | CLI |
| `convert …` / `batch …` / `view -i …` | CLI |
| 任一 CLI 选项（含 `--width=800` 形式） | CLI |
| 其它（如裸文件名 `a.raw`） | CLI，由 argparse 报 usage（rc=2） |

> 刻意**不做参数改写**：`raw-view.exe a.raw` 不会变成 `view a.raw`。若把无法识别的参数
> 判成 GUI，`raw-view.exe --width 800`（拼错的选项）会真的弹窗并阻塞终端。

`raw-view-cli.exe` 通过环境变量 `RAW_VIEW_NO_GUI=1`（由 `cli_entry.main()` 设置）保证
**任何路径都不会启动 GUI**：缺 `-i` 或没有子命令时直接报 usage 并返回 2，而不是挂起。

---

## 0.7 已知限制：PowerShell 7 下对 `raw-view.exe` 做文件重定向会得到空文件

**这是 PowerShell 的平台行为，不是 raw-view 的缺陷。请不要据此修改 `launcher.py`。**

### 现象

在 PowerShell 7 里执行文件重定向：

```powershell
.\raw-view.exe --version > out.txt      # out.txt 是 0 字节
```

同时退出时可能看到：

```
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
OSError: [Errno 22] Invalid argument
```

换成 `cmd` 就完全正常：

```cmd
raw-view.exe --version > out.txt        :: 26 字节，内容正确
```

### 原因

1. Windows 由**被启动的那个 exe** 的 PE subsystem 决定控制台行为。`raw-view.exe`
   是 **GUI 子系统**（subsystem=2）——必须如此，否则双击时会闪一个黑框。
2. **PowerShell 7 不等待 GUI 子系统的进程**。实测：一个 `sleep 2` 的 GUI 子系统
   程序，pwsh 在约 0.2 秒就返回了；同样内容的 console 子系统程序要等满约 2 秒。
   （具体数字随机器负载浮动，两个数量级的差异在多次测量中稳定成立。）
3. pwsh 返回时**立即拆掉自己建的 stdout 重定向管道**。launcher 之后任何写 stdout
   的动作都命中一条已断开的管道，于是 `WriteFile` 返回 `ERROR_NO_DATA (232)`，
   Python 层表现为 `OSError: [Errno 22]`。

实测证据（在 GUI 子系统进程内直接读句柄）：

| 启动方式 | stdout 句柄类型 | 写入结果 |
|---|---|---|
| `cmd` + 重定向 | `FILE_TYPE_DISK (1)` | 成功 |
| `pwsh` + 重定向 | `FILE_TYPE_PIPE (3)` | `ERROR_NO_DATA (232)`，重试 6 次 / 3 秒均失败 |

> 附带说明：`$LASTEXITCODE` 同理——只有 PowerShell **等待过**的原生命令才会设置它，
> 未等待的 GUI 子系统进程不会，所以 pwsh 里读它是空值。

### 正确用法

| 需求 | 写法 |
|---|---|
| 抓输出到文件 | 用 `cmd`：`cmd /c "raw-view.exe --version > out.txt"`，或直接用 **`raw-view-cli.exe`** |
| 管道处理 | **`pwsh` 下可用**（管道不会被提前拆除）：`raw-view.exe -h \| Select-String batch-help` |
| 交互式终端直接看输出 | 正常可用（launcher 会 `AttachConsole` 到父终端） |
| 脚本/CI 里取退出码 | 用 **`raw-view-cli.exe`**（console 子系统，同步等待，`$LASTEXITCODE` 可靠） |

`raw-view-cli.exe` 是 console 子系统，pwsh 会正常等待它，因此重定向、管道、退出码
全部可靠。**在自动化脚本里优先用它。**

### 对 CI 的影响

`.github/workflows/build-release.yml` 的 launcher 冒烟步骤因此：
- 文件重定向走 `cmd /c`；
- 管道断言保留在 pwsh（可用）；
- 退出码（`bare.raw` → 2）改用 `raw-view-cli.exe` 断言。

launcher 自身的参数分流正确性由 `tests/test_launcher.py`（26 个用例）覆盖，
不依赖这条冒烟测试。

---

## 1. 准备环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

## 2. 执行打包

> **首选：直接用仓库根目录的 `raw-view.spec`。** 一条命令产出全部四个 exe
> （launcher / GUI / CLI / onefile），一次 Analysis 共享依赖，布局由 PyInstaller 直接产出：
>
> ```powershell
> pyinstaller --noconfirm --clean --distpath dist --workpath build raw-view.spec
> ```
>
> 产物：
> ```
> dist/raw-view/{raw-view.exe, raw-view-gui.exe, raw-view-cli.exe, _internal/}
> dist/raw-view.exe        (单文件，仅 GUI)
> ```
>
> 下面三种"手工命令"仅用于调试单个形态。**注意不要照抄加上 `--collect-all=PyQt5`**：
> 它会把整个 QML 树、全部 Qt 插件与 44 个 Qt DLL（含 Designer/Quick/XmlPatterns）
> 拉进来，实测多出 2411 个文件、zip 从 102 MB 涨到 129 MB，而本程序只用
> QtCore/QtGui/QtWidgets/QtSvg。让 PyInstaller 的 Qt hook 按实际 import 收集即可
> （spec 里就是这么做的）。

### 方式一：单文件打包（推荐，无需目标电脑安装 Python）

打包为单个 exe 文件，生成的文件较大（约 100MB+），但**无需在目标电脑安装 Python**。

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name raw-view `
    --hidden-import=cv2 `
    --collect-all=cv2 `
    --collect-all=qt_material `
    --collect-all=qtawesome `
    --collect-all=pypinyin `
    --icon assets/raw-view.ico `
    --add-data "assets;assets" `
    raw_view/gui_entry.py
```

> 推荐直接使用现成的 `raw-view.spec`：`pyinstaller --noconfirm --clean raw-view.spec`（已内置 qt_material / qtawesome / assets / 图标）。
> 修改 `assets/logo.svg` 后，先运行 `python scripts/make_icon.py` 重新生成 `assets/raw-view.ico` 与 `logo.png`。

产物：`dist/raw-view.exe`（单文件，约 99MB）

---

### 方式二：目录打包（onedir，解压即用）

打包为目录形式，`raw-view.exe` 与 `_internal/` 依赖目录一起分发。**目标电脑无需安装 Python**，只需保留整个目录结构；其优点是启动不必像单文件版一样先解压到临时目录。

```powershell
# 打包命令（在仓库根目录执行；--paths 用当前目录即可，无需写死绝对路径）
pyinstaller --noconfirm --clean --windowed --name raw-view `
    --hidden-import=cv2 `
    --collect-all=cv2 `
    --collect-all=qt_material `
    --collect-all=qtawesome `
    --collect-all=pypinyin `
    --icon assets/raw-view.ico `
    --add-data "assets;assets" `
    raw_view/launcher.py
```

产物：`dist/raw-view/raw-view.exe`（目录）

> **目标电脑环境要求**：
> - Windows 10 1809+ (64位)
> - Python 3.12（必须与打包环境版本一致）
> - Visual C++ Redistributable 2015-2022

---

### 方式三：zip 压缩包（onedir，解压即用，推荐分发）

将上面的 onedir 目录（方式二）压缩成 zip，**任何目标电脑解压后直接运行 `raw-view.exe`，
无需安装 Python**。与单文件 exe（方式一）的关系：
- **方式一（单文件 exe）**：体积最小、方便拷贝分享，但启动时要自解压到临时目录，**首次启动慢**；
- **方式三（zip/onedir）**：体积更大、需整体解压，但**启动快、目录内已含全部依赖（`_internal/`）**；
- 两者**功能与依赖完全一致**（同一次 CI 构建产物），按场景二选一即可。

**CI 已自动产出 zip**：打 tag 后 `build-release.yml` 自动把 onedir 目录压缩为
`raw-view-<版本>-windows-x64.zip` 并连同校验和一起发布到 GitHub Releases，
一般无需手动执行。本地手动制作（离线场景）：

```powershell
# 先按方式二命令生成 dist/raw-view/，然后（把 0.2.0 换成当前版本号）：
Compress-Archive -Path dist\raw-view -DestinationPath dist\raw-view-0.2.0-windows-x64.zip
```

> 提示：zip 内是 `raw-view/` 目录（内含 `raw-view.exe` 与 `_internal/`），解压后进入该目录运行即可；
> 若希望"解压到桌面即出 exe"，可把 zip 改为直接打包 `dist/raw-view\*` 内容（`Compress-Archive -Path dist\raw-view\*`），
> CI 默认采用整目录打包（目录名即版本标识，多版本共存不冲突）。

---

> 注意：
> - 上面的"方式一/二/三"是**调试单个形态**用的手工命令；发布请直接用 `raw-view.spec`
>   （`release.bat` 与 CI 都用它），这样布局与体积才与正式发行物一致
> - 如果是 Bash，将 `` 改为 `^`，或使用一行命令
> - spec 用 `SPECPATH` 推导仓库根目录，本机与 CI 都不需要写死绝对路径

## 3. 验证打包结果

```powershell
# 方式1：直接运行查看是否报错
dist\raw-view.exe

# 方式2：在命令行模式运行（能看到错误输出，用于调试）
pyinstaller --noconfirm --clean --onefile --console --name raw-view ^
    --hidden-import=cv2 ^
    --collect-all=cv2 ^
    raw_view/cli_entry.py
dist\raw-view.exe
```

## 4. 常见问题

| 问题 | 解决方案 |
|------|----------|
| 启动缺少 DLL | 确认在 Windows 环境重新打包，不要跨平台拷贝 |
| failed to load python dll | 使用 `--onefile` 方式打包 |
| ImportError: No module named 'cv2' | 添加 `--hidden-import=cv2` |
| Pillow 相关的 ImportError | 添加 `--hidden-import=PIL` |
| Qt 平台插件缺失 | 添加 `--add-data` 包含 translations 目录 |
| attempted relative import with no known parent package | 添加 `--paths` 指向项目根目录 |
| 窗口闪退 | 使用 `--console` 模式运行查看报错 |
| 图标与版本信息 | 使用 `--icon your.ico` 和 `--version-file` |
| onefile 打包后运行报错 | 目标电脑安装 Visual C++ Redistributable |

## 5. Sensor 预设与发布

### 预设是否会随 exe 一起打包？

**不会**。预设通过 `QSettings` 存储在每台机器的注册表 / 用户配置目录里，与 `dist/` 下的可执行文件完全分离：

| 平台 | 预设位置 |
|---|---|
| Windows | `HKEY_CURRENT_USER\Software\yorelll\raw-view`，键 `presets\sensors` |
| macOS | `~/Library/Preferences/com.yorelll.raw-view.plist` |
| Linux | `~/.config/yorelll/raw-view.conf` |

PyInstaller 不会扫描这些位置；exe 启动后第一次读取 `presets/sensors` 时若键不存在，下拉框就是空的。**这是有意为之**——exe 不夹带任何用户私有数据，任何人在自己机器上保存的预设也只留在他那台机器。

### 如何把团队预设随 exe 一起分发？

推荐方式：**JSON 旁路分发，不打进 exe**。

1. 在某台已经配置好预设的机器上，打开 *Manage sensor presets → Export*，把全部预设导出为 `team-presets.json`（默认文件名 `raw-view-presets.json`）。
2. 把 `team-presets.json` 放进发布目录，告诉同事：首次启动后通过 *Manage sensor presets → Import* 选择该文件 → 处理重名（一般选 *Overwrite*）→ Save。
3. 此后预设直接进入对方注册表/配置文件，**只需导入一次**——重启、升级 exe、新增自己的预设都不会丢。

> 已在 `.gitignore` 中 ignore 了 `raw-view-presets*.json` / `*sensor-presets*.json` / `presets*.json`，避免不小心把团队配置或个人导出文件提交到代码仓库。

发布目录推荐结构：

```
发布目录/
├── raw-view.exe              # 主程序
├── README.md
└── presets/
    └── team-presets.json     # 由用户首次启动后手动 Import
```

如果未来希望"开箱即用"（首次启动自动导入），可以扩展为：把 `team-presets.json` 用 `--add-data "presets/team-presets.json;presets"` 打进 exe，并在 `MainWindow` 启动时检测 `AppSettings.sensor_presets` 为空时自动调用 `import_sensor_presets(path, mode="merge", on_conflict="skip")`。当前默认实现没有内置此自动导入，保留对用户个人预设的"零干扰"。

## 6. 建议发布内容

### 方式一（单文件）发布
```
发布目录/
├── raw-view.exe          # 主程序（单文件，约114MB）
├── README.md             # 简版使用说明
└── 示例文件/             # 可选
```

### 方式二（目录）发布
```
发布目录/
├── raw-view/             # 整个目录
│   ├── raw-view.exe
│   └── _internal/        # 依赖文件
├── Python312/            # 需要打包 Python 环境（可选）
└── README.md
```

### 方式三（zip 压缩包，onedir）发布（推荐）
```
发布目录/
├── raw-view-<版本>-windows-x64.zip      # 解压即用（内含 raw-view/ 整目录）
├── raw-view-<版本>-windows-x64.zip.sha256
├── raw-view.exe                        # 可选的单文件版
└── README.md
```

## 7. 完整参数说明

| 参数 | 说明 |
|------|------|
| `--noconfirm` | 不询问确认，直接覆盖已有文件 |
| `--clean` | 打包前清理 build 目录 |
| `--onefile` | 打包为单个可执行文件 |
| `--windowed` / `-w` | 窗口模式（无控制台） |
| `--console` / `-c` | 控制台模式（可看输出，用于调试） |
| `--name` | 输出的 exe 名称 |
| `--paths` | 添加额外的 Python 模块搜索路径 |
| `--hidden-import` | 强制包含隐式导入的模块 |
| `--collect-all` | 收集指定包的所有资源 |
| `--add-data` | 附加数据文件（格式：`源:目标`） |
| `--icon` | 程序图标 (.ico) |
| `--version-file` | 版本信息文件 |
