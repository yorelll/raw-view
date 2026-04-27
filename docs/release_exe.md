# 将 raw-view 打包为 EXE（Windows）

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
    raw_view/__main__.py
```

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
    --collect-all=qdarkstyle `
    --collect-all=qtawesome `
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

## 5. 建议发布内容

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

## 6. 完整参数说明

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
