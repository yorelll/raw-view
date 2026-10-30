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
- **打 tag（如 `v0.1.1`）**：`.github/workflows/build-release.yml` 自动打包 `raw-view.exe` + zip 校验和并发布到 GitHub Releases。
- 依赖：`requirements.txt` 宽松版；CI 用 `constraints.txt` 锁定版本可复现（注意 **constraints 文件必须保持纯 ASCII**，pip 以平台默认编码读取）。
- **Release 版本说明（重要）——必须"发布即最终版"，格式统一**：
  1. **标准模板**：`docs/release-template.md`。所有 Release body 必须遵循该模板格式（标题含版本号、来源行含测试状态、`### 🐛 修复` / `### 🆕 新功能` / `### 使用方式` / `### 功能总览` / `### 文件` / `### 系统要求`）。
  2. **打 tag 前填好**：发布者把当前版本的修复/新功能清单按模板填入 `.github/workflows/build-release.yml` 的 `body:`（Workflow 的 `Create or update GitHub Release` 步骤），连同版本号/文件名占位符一并更新。这样打 tag 后 Release **直接生成即最终版**，**不依赖事后 `gh release edit`**。
  3. **确需事后修改时**：`gh release edit <tag> --notes-file - <<'EOF' ... EOF` 覆盖整个 body，内容仍必须与 `docs/release-template.md` 格式一致。
  4. body 中版本号、zip 文件名、`name:` 中的版本号必须与 tag 一致（可用 `${{ steps.version.outputs.version }}` 动态占位符）。
- **远程监控一律用 `gh.exe`（必守）**：监控远端状态（CI / Build & Release 运行、Release 资产与 body、tag/远程 ref）时，统一使用 GitHub CLI 完整路径：
  ```powershell
  "D:\Program Files\GitHub CLI\gh.exe" run list --limit N
  "D:\Program Files\GitHub CLI\gh.exe" run view <run-id> --json status,conclusion
  "D:\Program Files\GitHub CLI\gh.exe" release view <tag> --json tagName,name,assets,body
  "D:\Program Files\GitHub CLI\gh.exe" release list --limit N
  ```
  - 不要再用 `curl https://api.github.com/...` 手拼 JSON（已踩过解析/限流/编码坑）；如需解析 JSON，让 gh 输出经 Python `json.load` 或写临时文件后读。
  - 说明：本机 `gh` 因 PATH 代理（rtk）有时解析不到，务必用全路径调用。

## 3. 测试执行分工（必守）

- **实现 agent（Agent A 等）**：交付前**必须**跑全量测试（`python -m pytest tests/ -q`，必要时加 `python -m unittest discover -s tests -q`）并确认全绿，附真实数字。未通过不得交付。
- **review agent（Agent B/E 等）**：**默认无需跑全量测试**——实现 agent 已保证全绿，重复全量只浪费时间（当前全量约 45–105 秒，见 §4）。
  - review 只做**只读审查**；"只读"的定义是 **review 结束后代码区域与 review 前保持一致**，而不是过程中不能改。
  - 为了更有效地审查，review 允许在过程中**临时修改代码/测试**来验证假设（例如 mutation testing、反向注入、临时探针），但**必须还原**，并在报告中说明改了什么、如何还原、还原后是否逐字节一致。
  - review 可以只跑**定向**测试（`-k` 选择相关用例）或写独立脚本做功能验证；不跑全量。
  - 若 review 期间发现实现 agent 的改动让某些测试失败，只需报告失败项，不必自己跑完整矩阵。
- 例外：review 若怀疑实现 agent 的测试结果不可信（例如怀疑测试被改弱），可**定向重跑**相关模块，但不需要全量。

## 4. GUI 测试 teardown 必须真正销毁 Qt 对象（必守）

- **禁止**：`widget.deleteLater()` 后跟 `app.processEvents()`。
  `QApplication.processEvents()` **不会消费** `DeferredDelete` 事件，Qt 对象永远不会被销毁。
- **后果**：每个未销毁的 `MainWindow` 会留下约 144 个 widget 存活；而 `MainWindow.__init__`
  里的 `_apply_theme()` → `app.setStyleSheet()` 会重新 polish **全进程所有存活 widget**，
  因此测试耗时随数量 O(n²) 增长（实测：58 分钟 → 修复后 37 秒）。
- **必须**：使用 `tests/_qt_cleanup.destroy_widget(widget)`，它内部用 `QTest.qWait(0)`
  真正跑一轮事件循环（无需 sleep，实测比 `qWait(1)` 快约 7 倍且回收效果等价），
  并额外 `sendPostedEvents(None, QEvent.DeferredDelete)` 双保险。
- 若测试本身刻意不能调用 `close()`（例如要保留 `closeEvent` 未被重复触发的语义），
  用 `destroy_widget(widget, close=False)`。

正例：
```python
from _qt_cleanup import destroy_widget

def tearDown(self):
    destroy_widget(self.w)
```

反例（会被 L2 静态守卫与 L3 运行时守卫拦截）：
```python
def tearDown(self):
    self.w.close()
    self.w.deleteLater()
    _APP.processEvents()   # ← 对象永不销毁
```

**三层守卫**（改测试时请一并维护）：

| 层 | 位置 | 作用 |
|---|---|---|
| L1 | 本节 + `tests/_qt_cleanup.py` 模块文档 | 规则说明与原理 |
| L2 | `tests/test_qt_cleanup_guard.py` | 静态检查：**任何** `deleteLater()` 调用直接 fail（报 `文件:行号`）；`setUp` 覆盖基类却未 `super().setUp()` 也 fail |
| L3 | `tests/conftest.py`（pytest）+ `_qt_cleanup` 的 `begin/end_widget_leak_check`（unittest） | 运行时检查：用例结束后存活 widget 增长超 `WIDGET_LEAK_TOLERANCE`（4）则 fail |

- 本仓库已完成迁移的 teardown 共 **52 处**（`destroy_widget(...)` 调用点，覆盖 6 个测试文件）；
  另有 3 处 `item_tabs` 由 `_destroy_stub_tabs()` 集中收割（原 55 处 `.deleteLater()` 调用已全部消除）。
- L3 在 pytest 路径由 autouse fixture 自动生效；`unittest discover` 不加载 `conftest.py`，
  因此 GUI 测试基类（如 `tests/test_ui_interactions.py` 的 `_StubWindowCleanupMixin`、
  `tests/test_params_memory_pin.py` 的 `_IsolatedWindowTest`）在 `setUp` 里调
  `begin_widget_leak_check()`、`tearDown` 里调 `end_widget_leak_check(...)`。
- **硬性要求：子类覆盖 `setUp` 时必须调用 `super().setUp()`**（首行），否则该类的 L3
  守卫不会 arm。未 arm 时 `end_widget_leak_check` 会 **fail-loud**（历史上 CI 的
  unittest 路径正因漏掉这行而完全没有 L3 保护）；L2 静态检查也会同时拦截。
- **新增 GUI 测试时**：继承上述基类（或自行配对 `begin/end_widget_leak_check`），
  否则 CI 的 unittest 路径会失去 L3 保护。
- 本机若同时跑多个 pytest 进程，`qt_material` 会争用 `~/.qt_material` 导致随机
  `FileNotFoundError`；**跑全量请串行**。

## 5. 常用命令（本地）

```bash
# 全量测试（实现 agent 必跑；review agent 默认不跑）
python -m pytest tests/ -q
python -m unittest discover -s tests -q

# CLI 冒烟（两者都可跑，成本极低）
python -m raw_view --batch-help

# 打包（离线调试用；发布走 CI）
pyinstaller --noconfirm --clean --onefile --windowed --name raw-view \
  --hidden-import=cv2 --hidden-import=PIL --collect-all=cv2 --collect-all=PyQt5 \
  --collect-all=qt_material --collect-all=qtawesome --icon assets/raw-view.ico \
  --add-data "assets;assets" raw_view/__main__.py
```

## 6. 仓库要点

- **技术栈**：Python 3.12 + PyQt5 5.15 + numpy + opencv-python + qt-material/qtawesome，PyInstaller 打包。
- **入口**：`python -m raw_view`（GUI）/ `view` / `convert` / `batch`（CLI）。
- **目录**：`raw_view/` 源码（`formats` 纯编解码、`converter` 文件转换、`models` 数据/设置、`gui/` 界面+对话框+worker）、`tests/` 测试、`assets/` 资源、`docs/` 文档。
- **Windows 注意**：CLI 打印 Unicode 的路径必须经过 `_make_utf8_stdio()`（对窄编码 stdout 鲁棒），新增 CLI 打印时保持该防御。
