# code review 提交级登记表（commit ↔ review 索引）

> **用途**：登记**每一个 commit 的 review 状态**——哪些已审、审查意见存于哪个文件、哪些尚未审，
> 让后续做 code review 的 agent 一打开就知道进度，**避免重复 review 或漏 review**。
>
> **与版本级文档的关系**：
> - 版本级：`docs/review/code-review-<版本>.md` / `review-response-<版本>.md` 保存**问题清单与处置回复**（一个版本一册）。
> - 提交级：本表（`code-review-commit.md`）是“哪个 commit 对应哪次审查”的**索引**，不重放问题内容。
> - review 完成后：**必须同步更新本表**（把本次覆盖的 commit 标记为「已审」，关联审查/回复文档），
>   再按 §1 流程生成/追加版本级文档。

## 登记规则

1. **新增/修改 commit 后**：在下方表格补一行（从 `git log` 取短 sha、日期、提交说明），状态默认「⬜ 未审」。
2. **做 code review 的 agent，第一步必须先读本表**：确认本次要审的 commit 有哪些已审/未审、
   历史意见在哪个文件，避免重复审已审过的或漏掉未审的。
3. **review 完成后更新本表**：把本次覆盖的 commit 状态改为「✅ 已审」，填写对应的
   `code-review-<ver>.md` / `review-response-<ver>.md` 文件名与备注。
4. **只读核验**：commit 号以 `git log --oneline`（`main` 分支）实测为准，不臆造。

## 表格（最新在上，`main` 分支）

| Commit (short sha) | 日期 | 是否已 review | 审查文档（code-review-\*.md） | 回复文档（review-response-\*.md） | 备注 |
|---|---|---|---|---|---|
| `2ea2d87` | 2026-09-03 | ✅ 已审 | `ui-review-report-0-3-1.md` | `ui-review-response-0-3-1.md` | follow-up code review：修复 info icon 裁切、FourCC 未绑定 `?`，补充 UI 无障碍/批量门禁/对话框一致性回归测试 |
| `686ddc9` | 2026-09-03 | ✅ 已审 | `ui-review-report-0-3-1.md` | `ui-review-response-0-3-1.md` | fix：v0.3.1 UI review 处置；本次核验已覆盖 UI-01~UI-16，问题由 `2ea2d87` 直接修复或按回复判定无需修改 |
| `9f36c6a` | 2026-09-01 | ✅ 已审 | `code-review-0.2.1.md` | `review-response-0.2.1.md` | fix+feat：v0.2.1 多帧偏移、标签关闭、YOnly 多 bit、面板折叠；当前 review 基线 |
| `ae6528f` | 2026-09-01 | ✅ 已审 | `code-review-0.2.0.md` | `review-response-0.2.0.md` | feat：v0.2.0 YOnly、标签拖拽、release zip；当前 review 覆盖 |
| `69cc177` | 2026-09-01 | ✅ 已审 | `code-review-0.2.0.md` | `review-response-0.2.0.md` | docs：新增本仓库版本化 review/回复规则；仅登记为本次审查流程相关提交 |
| `cd03cdb` | 2026-09-01 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：防御 `sys.std*` 缺 reconfigure + `_run_*` 入口强制 UTF-8 stdout；v0.1.1 区间审查 |
| `9b54577` | 2026-09-01 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：跨平台测试路径修复 + RAW32 编码 cast 警告消除；v0.1.1 区间审查 |
| `58a1d16` | 2026-09-01 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：pip `-c constraints.txt` 必须与安装需求同一条命令；v0.1.1 区间审查 |
| `ec686bc` | 2026-09-01 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：constraints.txt 改纯 ASCII，修复 CI GBK/cp1252 runner pip 解析失败；v0.1.1 区间审查 |
| `0fa652e` | 2026-08-31 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：修复 review 确认的 22 项问题 + 每次 push 全量 CI + 文档移出远程；v0.1.1 区间审查 |
| `e04124b` | 2026-08-31 | ✅ 已审 | `code-review-0.1.1.md` | `review-response-0.1.1.md` | fix：CLI 窄单字节编码 stdout（cp1252/GBK）打印 Unicode 崩溃；v0.1.1 区间审查 |
| `0aa347b` | 2026-08-31 | ⬜ 未审 | — | — | docs：新增代码 review/总结/改进文档 + GitHub Actions 自动发布 v0.1.0（文档/CI 提交，本身不在代码审查范围） |
| `0938d3f` | 2026-07-20 | ✅ 已审 | `code-review-0.1.0.md` | `review-response-0.1.0.md` | **v0.1.0 基线**；本仓库唯一一次完整 review（2026-08-31 审，01 回复），30 项 → 已修复 23 / 无需修改 7 |
| `caaaf31` | 2026-07-20 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（在 `0938d3f` 之前提交，已含入基线） |
| `06192ce` | 2026-07-14 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |
| `58dc899` | 2026-07-14 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |
| `33df853` | 2026-07-13 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |
| `12392f3` | 2026-07-09 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |
| `636d0bb` | 2026-05-28 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |
| `a85851d` | 2026-05-28 | ⬜ 未审 | — | — | 早于 v0.1.0 基线（已含入基线） |

> 更早的历史提交（`955d49f` 及以前）均早于 v0.1.0 基线 `0938d3f`，已含入基线审查，不再逐条登记。
> 基线含义：v0.1.0 的 review 审查对象 = commit `0938d3f` 全量代码；`0938d3f` **之后** v0.1.0 修订 +
> v0.1.1 修复链（`e04124b`…`cd03cdb`）已在 `review-response-0.1.0.md` 逐条处置（H-1~H-4 / M-1~M-14 / L-1~L-12）。

## 使用示例：开始一次新的 review

```text
1. Read docs/review/code-review-commit.md         ← 先看登记表
2. git log --oneline -N                            ← 确认新增/未审 commit
3. 只读核验问题（源码 + 测试，CONFIRMED / NOT_FOUND / ALREADY_FIXED / BY_DESIGN）
4. 生成 docs/review/code-review-<ver>.md（纯问题清单）
5. 修复 + 补测试；写 review-response-<ver>.md（逐条回复）
6. 更新本表：把本次覆盖的 commit 标为 ✅ 已审并关联文档
```
