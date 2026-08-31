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
   - `pyinstaller --onefile --windowed` 打包；
   - 生成 `raw-view.exe.sha256` 校验和；
   - 创建/更新 Release（名称 `raw-view <版本>`，包含 exe + 校验和 + 版本说明）。
4. 到仓库 **Releases** 页面即可看到并下载 `raw-view.exe`。

手动触发（不打 tag）也可在 Actions 页面选 `workflow_dispatch` 构建设置。

> 说明：`raw-view.spec` 因含本机绝对路径被 `.gitignore` 忽略，CI 使用等价命令行参数打包；
> 两者产物一致。相关代码文档见本地 `docs/{review,summary,improvement}`（已加入 `.gitignore`，不上传远程）。

---

## 1. 准备环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

## 2. 执行打包

### 方式一：单文件打包（推荐，无需目标电脑安装 Python）

打包为单个 exe 文件，生成的文件较大（约 100MB+），但**无需在目标电脑安装 Python**。

```powershell
pyinstaller --noconfirm --clean --onefile --windowed --name raw-view `
    --hidden-import=cv2 `
    --hidden-import=PIL `
    --collect-all=cv2 `
    --collect-all=PyQt5 `
    --collect-all=qt_material `
    --collect-all=qtawesome `
    --icon assets/raw-view.ico `
    --add-data "assets;assets" `
    raw_view/__main__.py
```

> 推荐直接使用现成的 `raw-view.spec`：`pyinstaller --noconfirm --clean raw-view.spec`（已内置 qt_material / qtawesome / assets / 图标）。
> 修改 `assets/logo.svg` 后，先运行 `python scripts/make_icon.py` 重新生成 `assets/raw-view.ico` 与 `logo.png`。

产物：`dist/raw-view.exe`（单文件，约 114MB）

---

### 方式二：目录打包（需要目标电脑安装 Python 环境）

打包为目录形式，文件较小，但**目标电脑需要安装 Python 3.12** 和相同版本的依赖库。

```powershell
# 打包命令
pyinstaller --noconfirm --clean --windowed --name raw-view `
    --paths "D:\work\jira\generate_raw\raw-view" `
    --hidden-import=cv2 `
    --hidden-import=PIL `
    --collect-all=cv2 `
    --collect-all=PyQt5 `
    --collect-all=qt_material `
    --collect-all=qtawesome `
    --icon assets/raw-view.ico `
    --add-data "assets;assets" `
    --add-data ".venv/Lib/site-packages/PyQt5/Qt5/translations;Qt5/translations" `
    raw_view/__main__.py
```

产物：`dist/raw-view/raw-view.exe`（目录）

> **目标电脑环境要求**：
> - Windows 10 1809+ (64位)
> - Python 3.12（必须与打包环境版本一致）
> - Visual C++ Redistributable 2015-2022

---

> 注意：
> - 将 `D:\work\jira\generate_raw\raw-view` 替换为你实际的仓库根目录路径
> - 如果是 Bash，将 `` 改为 `^`，或使用一行命令

## 3. 验证打包结果

```powershell
# 方式1：直接运行查看是否报错
dist\raw-view.exe

# 方式2：在命令行模式运行（能看到错误输出，用于调试）
pyinstaller --noconfirm --clean --onefile --console --name raw-view ^
    --hidden-import=cv2 ^
    --hidden-import=PIL ^
    --collect-all=cv2 ^
    --collect-all=PyQt5 ^
    raw_view/__main__.py
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
