# CLAUDE.md — raw-view 项目协作规则

本文件供 Claude Code / agent 在参与本仓库开发时复用。**核心约定：代码 Review 与回复必须版本化、解耦、可追溯。**

## 1. 版本化 Review 文档规则（重要）

`docs/review/` 下存放**代码审查**与**处置回复**两类文档，二者严格解耦，并按代码版本归档；另有一份**提交级登记表**作为 commit ↔ review 的索引。

### 目录与命名

```
docs/review/
├── code-review-commit.md       # 提交级登记表：每个 commit 的 review 状态（已审/未审/意见在哪），见下《提交级登记表》
├── code-review-<版本>.md       # 纯审查：只记录问题（位置/问题/失败场景/建议），不含任何回复
└── review-response-<版本>.md   # 纯回复：按 review 章节逐条回复处置结果（是否修复 / 原因）
```

- 版本号使用代码发布版本，如 `0.1.0`、`0.1.1`。
- 例：对 v0.1.0 的审查 → `code-review-0.1.0.md` + `review-response-0.1.0.md`。

### 铁律（必须遵守）

1. **解耦**：`code-review-*.md` **只写问题**，绝不夹带"已修复/无需修改"等回复；`review-response-*.md` **只写回复**，绝不新增问题。新发现的问题必须写进对应版本的 `code-review-*.md`。
2. **版本快照不回溯**：已完成版本的 `code-review-*.md` / `review-response-*.md` **不修改、不追加**。后置版本产生的新问题 / 新回复，一律写入**新版本**的文件。
3. **问题 ID 稳定**：问题编号（H-x / M-x / L-x）在同一版本内唯一，跨文件一一对应；回复必须覆盖审查中的全部问题 ID。
4. **章节管理**：`review-response-<版本>.md` 的一级章节 = 审查基线版本号（如 `## 0.1.0`），二级章节对应审查里的分类与问题 ID（`### 2. High 级问题 → #### H-1 ...`）。同版本审查若有跨版本追加章节，用 `## <版本>.1` 等继续命名，便于追溯。
5. **无需修改 ≠ 跳过**：判定"无需修改"也必须写进回复，并给出**明确原因**（设计如此 / 影响极小 / 风险不值得）。用 `[ ]` 未勾选标记，与 `[x]` 已修复区分。
6. **提交级登记必读、必更**：做 code review 的 agent **第一步必须先 `Read docs/review/code-review-commit.md`**，了解哪些 commit 已审/未审、意见在哪个文件，避免重复审或漏审；**review 完成后必须更新该登记表**，把本次覆盖的 commit 标记为已审并关联审查/回复文档。

### 提交级登记表（code-review-commit.md）

- **角色**：版本级文档（`code-review-<版本>.md` / `review-response-<版本>.md`）保存**问题清单与处置回复**；提交级登记表是“**哪个 commit 对应哪次审查**”的索引，记录每个 commit 是否已 review、意见存于哪个文件。
- **何时登记**：新增/修改提交后，从 `git log --oneline`（`main` 分支，勿臆造 commit 号）登记短 sha、日期、说明，状态默认「⬜ 未审」。
- **何时更新**：每次 review 结束，把本次覆盖的 commit 改为「✅ 已审」，并填入对应的 `code-review-<ver>.md` / `review-response-<ver>.md`。
- **与版本级的关系**：版本级 = 一版一册的问题清单；提交级 = 逐 commit 的索引，两者配合**秒懂进度**。

### 新增审查的流程（agent 必须遵循）

当对本仓库做一次新的 code review（例如发布新版本、大改动后）：

1. **先读 `docs/review/code-review-commit.md`**：确认哪些 commit 已审、哪些未审、历史意见在哪个文件，确定本次要审的 commit 范围（避免重复审 / 漏审）。
2. 确定审查基线版本号 `<ver>`（通常等于当前最新发布版本的下一版本，或审查对象的代码版本）。
3. `Read` 项目根 `CLAUDE.md`，确认规则与现有 `docs/review/` 文件，避免与既有版本冲突。
4. **只读核验**：逐条核验问题是否真实存在（看源码与测试），区分 `CONFIRMED / NOT_FOUND / ALREADY_FIXED / BY_DESIGN`，避免误报。
5. 写 `docs/review/code-review-<ver>.md`（纯问题清单，含严重程度分级 + 各模块专项 + 测试基线）。
6. 需要修复的，修复代码 + 补测试；无需修改的，确定原因。
7. 写 `docs/review/review-response-<ver>.md`（逐条回复，含 `[x]`/`[ ]` 状态与说明）。
8. **更新 `docs/review/code-review-commit.md`**：把本次 review 覆盖的 commit 标记为「✅ 已审」，关联审查/回复文档。
9. 同步更新 `README.md` / 相关 `docs/`，并在此文件中按需补充版本演进说明。

> 这些文件已加入 `.gitignore`（本地维护、不随仓库分发），但规则本身（本文件）随仓库提交，供所有协作方复用。

## 2. GitHub Actions（CI / 发布）

- **每次 push / PR**：`.github/workflows/ci.yml` 自动跑全量测试
  （Windows + Ubuntu × Python 3.11/3.12 矩阵 + CLI 冒烟）。**提交前请在本地 `python -m unittest discover -s tests -q` 确认全绿。**
- **打 tag（如 `v0.1.1`）**：`.github/workflows/build-release.yml` 自动打包 `raw-view.exe` + SHA-256 校验和并发布到 GitHub Releases。
- 依赖：`requirements.txt` 宽松版；CI 用 `constraints.txt` 锁定版本可复现（注意 **constraints 文件必须保持纯 ASCII**，pip 以平台默认编码读取）。
- 发布版本说明：使用 `gh release edit <tag> --notes-file - <<'EOF' ... EOF` 补充 changelog，需包含测试/CI 验证状态。

## 3. 常用命令（本地）

```bash
# 全量测试（优先以 pytest 跑全量，Claude Code 下也支持 unittest）
python -m pytest tests/ -q
python -m unittest discover -s tests -q

# CLI 冒烟
python -m raw_view --batch-help

# 打包（离线调试用；发布走 CI）
pyinstaller --noconfirm --clean --onefile --windowed --name raw-view \
  --hidden-import=cv2 --hidden-import=PIL --collect-all=cv2 --collect-all=PyQt5 \
  --collect-all=qt_material --collect-all=qtawesome --icon assets/raw-view.ico \
  --add-data "assets;assets" raw_view/__main__.py
```

## 4. 仓库要点

- **技术栈**：Python 3.12 + PyQt5 5.15 + numpy + opencv-python + qt-material/qtawesome，PyInstaller 打包。
- **入口**：`python -m raw_view`（GUI）/ `view` / `convert` / `batch`（CLI）。
- **目录**：`raw_view/` 源码（`formats` 纯编解码、`converter` 文件转换、`models` 数据/设置、`gui/` 界面+对话框+worker）、`tests/` 测试、`assets/` 资源、`docs/` 文档。
- **Windows 注意**：CLI 打印 Unicode 的路径必须经过 `_make_utf8_stdio()`（对窄编码 stdout 鲁棒），新增 CLI 打印时保持该防御。
