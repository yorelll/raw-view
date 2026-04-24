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

```bash
pyinstaller --noconfirm --clean --windowed --name raw-view --collect-all PyQt5 -m raw_view
```

产物默认在：

- `dist/raw-view/raw-view.exe`

## 3. 常见问题

- 启动缺少 DLL：确认在 Windows 环境重新打包，不要跨平台拷贝构建产物。
- 窗口闪退：在命令行先运行 exe，查看报错并补齐依赖。
- 图标与版本信息：可在命令中增加 `--icon your.ico`，并配合 `--version-file`。

## 4. 建议发布内容

- `raw-view.exe`
- `README.md`（简版使用说明）
- 示例输入文件（可选）
