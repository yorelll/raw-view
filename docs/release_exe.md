# 将 raw-view 打包为 EXE（Windows）

## 1. 准备环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

## 2. 执行打包

在仓库根目录执行：

```powershell
pyinstaller --noconfirm --clean --windowed --name raw-view `
    --paths "D:\work\jira\generate_raw\raw-view" `
    --hidden-import=cv2 `
    --hidden-import=PIL `
    --collect-all=cv2 `
    --collect-all=PyQt5 `
    --add-data ".venv/Lib/site-packages/PyQt5/Qt5/translations;Qt5/translations" `
    raw_view/__main__.py
```

> 注意：
> - 将 `D:\work\jira\generate_raw\raw-view` 替换为你实际的仓库根目录路径
> - 如果是 Bash，将 `` 改为 `^`，或使用一行命令

产物默认在：

- `dist/raw-view/raw-view.exe`

## 3. 验证打包结果

```powershell
# 方式1：直接运行查看是否报错
dist\raw-view\raw-view.exe

# 方式2：在命令行模式运行（能看到错误输出，用于调试）
pyinstaller --noconfirm --clean --console --name raw-view ^
    --paths "D:\work\jira\generate_raw\raw-view" ^
    --hidden-import=cv2 ^
    --hidden-import=PIL ^
    --collect-all=cv2 ^
    --collect-all=PyQt5 ^
    raw_view/__main__.py
dist\raw-view\raw-view.exe
```

## 4. 常见问题

| 问题 | 解决方案 |
|------|----------|
| 启动缺少 DLL | 确认在 Windows 环境重新打包，不要跨平台拷贝 |
| ImportError: No module named 'cv2' | 添加 `--hidden-import=cv2` |
| Pillow 相关的 ImportError | 添加 `--hidden-import=PIL` |
| Qt 平台插件缺失 | 添加 `--add-data` 包含 translations 目录 |
| attempted relative import with no known parent package | 添加 `--paths` 指向项目根目录 |
| 窗口闪退 | 使用 `--console` 模式运行查看报错 |
| 图标与版本信息 | 使用 `--icon your.ico` 和 `--version-file` |

## 5. 建议发布内容

```
发布目录/
├── raw-view.exe          # 主程序
├── README.md             # 简版使用说明
└── 示例文件/             # 可选
```

## 6. 完整参数说明

| 参数 | 说明 |
|------|------|
| `--noconfirm` | 不询问确认，直接覆盖已有文件 |
| `--clean` | 打包前清理 build 目录 |
| `--windowed` / `-w` | 窗口模式（无控制台） |
| `--console` / `-c` | 控制台模式（可看输出，用于调试） |
| `--name` | 输出的 exe 名称 |
| `--hidden-import` | 强制包含隐式导入的模块 |
| `--collect-all` | 收集指定包的所有资源 |
| `--add-data` | 附加数据文件（格式：`源:目标`） |
| `--icon` | 程序图标 (.ico) |
| `--version-file` | 版本信息文件 |
