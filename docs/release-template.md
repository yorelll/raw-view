# raw-view Release 版本说明模板

> **用途**：GitHub Releases 的版本说明（body）统一使用本模板。发布者应在打 tag 前，把模板填好为
> 当前版本的最终说明，并写入 `.github/workflows/build-release.yml` 的 `body:` 中，使 **Release 直接
> 生成即最终版**，避免发布后再用 `gh release edit` 事后修改。若确需事后更新，格式必须与模板一致。
>
> 规则已写入项目 `CLAUDE.md` §发布。

---

## 标题模板（一级标题 = 版本号）

```markdown
## raw-view <版本号>

Python 实现的 RAW / YUV 图像查看与格式转换工具（Windows，提供**单文件 exe 与 zip 压缩包**两种形态）。

> 来源：Git tag `v<版本号>` · GitHub Actions 自动构建 · CI（Windows+Ubuntu × Py3.11/3.12）测试全绿（**<N> passed**）。
```

## 正文模板（按需保留小节，**格式与用词保持一致**）

```markdown
### 🐛 修复
- <#ID> <简明现象>：<根因>。**修复**：<做法>（<回归测试说明>）。

### 🆕 新功能 / 增强
1. <功能名>：<一句话能力>；<关键细节/示例>
2. …

### 使用方式
- **zip 版（推荐）**：下载 `raw-view-<版本号>-windows-x64.zip` 解压，进入 `raw-view/` 运行 `raw-view.exe`。
- **单文件版**：下载 `raw-view.exe` 双击运行。
- 命令行示例：`raw-view.exe view -i in.raw -o out.png --width 2560 --height 1440`；`raw-view.exe --batch-help`。
- 传感器预设保存在本机注册表/配置目录；团队共享可 *Manage sensor presets → Export / Import*。

### 文件
- **`raw-view-<版本号>-windows-x64.zip`** — zip 压缩包（onedir，解压即用，约 <大小> MB）
- **`raw-view.exe`** — 单文件 exe（约 <大小> MB）
- 各附 `.sha256` 校验和

### 系统要求
- Windows 10 1809+（64 位）
- Visual C++ Redistributable 2015-2022（多数系统已自带）
```

## 使用步骤

1. 发布前：把 `<版本号>`、`<N> passed`、修复/新功能清单、文件大小填入上述模板。
2. 将完整说明作为 `body:` 字符串写入 `.github/workflows/build-release.yml` 的
   `Create or update GitHub Release` 步骤（`tag_name` / `name` / zip 文件名中的版本号也应一致）。
3. 打 tag 推送 → 工作流自动发布 → Release 即最终版，无需 `gh release edit`。
4. 若某版本确实需要补充说明，用 `gh release edit <tag> --notes-file - <<'EOF' ... EOF` 覆盖整个 body，
   **内容仍遵循本模板格式**。
