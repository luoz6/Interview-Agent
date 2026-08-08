# Interview Quality V1 — T58 自动审查

## 结论

T58 Engineering 为 **PASS**，自动审查为 **PASS**。本阶段没有真实 Provider 调用，Provider 请求数、模型 fallback 数和外发数据请求数均为 **0**。

关键确认、模式说明和报告状态文案已经按 T58 的全部要求落地：结束面试、跳题、整份计划重新生成、历史 revision 恢复、手工题覆盖、匿名草稿恢复和清空当前画布均具有明确的后果说明；取消操作不发送面试 command 或覆盖请求，并把焦点返回触发按钮；`fixed_v1` 与 `adaptive_v1` 不再共享错误的动态路径文案；`unscored`、`partial`、初次生成失败和重评分失败继续保持不同语义，重评分失败明确说明旧报告仍有效。

本阶段只修改代码、测试和文本证据，没有截图或其他图像操作。

## T58 要求逐项核验

| 要求 | 实现与证据 | 状态 |
|---|---|---|
| “结束面试”二次确认 | `InterviewPage` 先打开 `结束面试并生成报告？` 对话框，确认后才调用 `/finish` | PASS |
| 显示未完成、已回答和覆盖影响 | 使用后端公开快照的 `answered_questions`、`skipped_questions`、`unanswered_questions`；对话框同时说明未回答/跳过题不产生对应能力分并降低报告覆盖 | PASS |
| cancel 不发送 command | finish 与 skip 测试均在 cancel/Escape 后断言 `postJson` 调用数为 0 | PASS |
| focus 返回触发按钮 | 共享 `useConfirmationDialog` 保存触发元素，取消、Escape 或 backdrop 关闭后恢复焦点；专注模式 Escape 也返回原触发按钮 | PASS |
| 全量 regenerate、恢复和清空覆盖确认 | 既有全量 regenerate、题目换题、删除、revision 恢复统一接入共享确认；手工恢复匿名草稿和清空画布改为 dialog | PASS |
| 清空草稿语义准确 | UI 明确操作只清空当前画布，不删除已保存匿名草稿；保存草稿仍可恢复 | PASS |
| fixed_v1 固定节奏 | 固定模式显示“固定节奏”，说明每道主问题按固定追问策略推进，不宣称动态决策 | PASS |
| adaptive_v1 动态路径 | 只有 `followup_policy_version=adaptive_v1` 显示“动态路径”和回答驱动分支说明 | PASS |
| 跳题覆盖影响 | 跳题确认明确写入“不产生该题能力分并降低报告覆盖”，并说明不是回答或 0 分 | PASS |
| unscored/partial/failed 不混淆 | unscored 不显示数字；partial 明确只覆盖已评估题目且未评估项不补 0；failed job 不伪装成评分状态 | PASS |
| 重评分失败旧报告有效 | active artifact 存在且最新 rescore failed 时显示“重评分失败，旧报告仍有效”，继续展示 active revision | PASS |

## 主要实现

### 共享确认对话框

新增 `frontend/src/components/ConfirmationDialog.jsx`，统一提供：

- `role="dialog"`、`aria-modal="true"`、独立 title/description 关联；
- 取消按钮默认焦点；
- Escape 和 backdrop 取消；
- 关闭后返回实际触发按钮；
- 危险操作语气、后果列表与明确确认按钮；
- 关闭时捕获当前触发元素，避免连续打开对话框时异步焦点恢复覆盖新的 trigger。

完整 focus trap、移动端实测和屏幕阅读器验收仍属于 T59，不在 T58 冒充完成。

### 面试结束与跳题

`InterviewPage` 不再直接从按钮调用 `runCommand("finish")` 或 `runCommand("skip")`。结束确认展示：

```text
已回答 X 道
已跳过 Y 道
仍未完成 Z 道
未回答和已跳过题不会产生对应题目的能力分，并会降低报告覆盖
```

若当前浏览器存在未提交回答草稿，还会说明该草稿不会进入报告证据。跳题确认同时说明：

```text
跳过后不产生该题能力分并降低报告覆盖
该题会被记录为已跳过，而不是已回答或评分为 0 分
```

统计优先使用后端已经公开的精确计数字段，并保留旧快照的保守 fallback。工作台完成进度仍将 answered 与 skipped 都视为流程完成，但逐题评审分母只使用真正 answered 的题目，避免把 skipped 误写为已回答。

### 模式一致性

题目计划侧栏读取 `followup_policy_version`，必要时从 `configuration_snapshot` 回退：

```text
fixed_v1   → 固定节奏；回答不会切换为动态决策路径
adaptive_v1 → 动态路径；回答会决定追问或进入下一题
```

未知或旧快照默认按 `fixed_v1` 呈现，不会把没有证据的模式标成动态路径。

### 准备页覆盖动作

准备页删除了旧的五秒“两次点击清空”状态，改为与计划覆盖动作一致的 confirmation dialog：

- 手工恢复草稿在当前画布存在资料、计划、本地题目修改或非默认配置时必须确认；
- 对话框逐项说明将被替换的资料、revision、本地题目输入和配置；
- 清空画布明确重置 JD、简历、当前 plan 和配置，但不删除已保存匿名草稿；
- 仅存在非默认配置时，清空按钮仍可用，不会留下无法重置的配置状态；
- 自动刷新恢复在初始空画布上继续直接执行，避免制造没有覆盖风险的多余确认。

### 报告状态

报告页保留既有五轴状态契约：

- `unscored`：证据不足，不发布数字；
- `partial`：发布已评估题目的部分评分，显示分子/分母，未评估题目和维度不按 0 分；
- initial job `failed`：没有 active artifact 时属于生成失败；
- rescore `failed` + active artifact：旧报告继续有效且继续显示，不被失败任务覆盖。

重评分失败提示从容易含糊的“新版本处理失败，当前版本仍可使用”改为：

```text
重评分失败，旧报告仍有效
失败的重评分没有覆盖或使这份 active 报告失效
```

## 自动审查发现与修复

### 1. 草稿恢复可能形成半恢复画布

首次实现沿用旧顺序：先把草稿 JD/简历写入状态，再请求关联 revision。如果第二个请求失败，用户已经确认的恢复动作会留下文档已替换、计划未替换的半状态。

修复后先完整读取 draft 和关联 revision、完成 plan 规范化，再一次性应用到界面。中途失败时当前画布保持不变。只有 404/410 这类明确不存在的草稿才清除本地 draft ID；临时 500 或网络错误保留关联，允许之后重试。

新增测试覆盖 draft 请求成功、revision 请求失败的场景，验证当前 JD 不变且 draft ID 仍存在。

### 2. 旧静态契约要求已经被 Plan 淘汰的两次点击清空

全仓测试最初出现一个 T58 相关失败：`tests/test_react_frontend.py` 硬编码检查 `clearArmed` 的旧两次点击状态。T58 明确要求覆盖动作使用确认框，该断言已经与权威 Plan 冲突。

静态契约已更新为检查 `requestClearWorkspace`、`清空当前画布？` 和共享 dialog 前缀。相关 Python 邻接测试随后 75/75 通过。

### 3. 延迟焦点恢复可能抢占新对话框 trigger

共享 hook 的初版在 `requestAnimationFrame` 回调执行时才读取可变 trigger ref。如果旧 dialog 关闭后立即打开新 dialog，旧回调可能清除新 trigger。

修复后在关闭瞬间捕获并清空旧 trigger，异步回调只处理捕获值，不会影响后来打开的对话框。

## 验证结果

### 定向前端

```text
npm test -- --run src/pages/StartPage.test.jsx src/pages/InterviewPage.test.jsx src/pages/ReportDetailPage.test.jsx
3 files passed
35 tests passed
```

### 全量前端

```text
npm test -- --run
7 files passed
64 tests passed
```

### 邻接后端与静态契约

```text
py -3.11 -m pytest -q tests/test_react_frontend.py tests/test_session_service.py tests/test_static_report_ui.py tests/test_utf8_text_contract.py tests/test_report_view.py tests/test_report_coverage.py tests/test_report_actions.py
75 passed
```

### 生产构建

```text
npm run build
PASS
4596 modules transformed
JS chunk 539.61 kB, gzip 146.61 kB
```

构建仍有 `>500 kB` chunk warning。这是已知性能告警，不是 T58 功能或构建失败；未在本阶段通过伪造阈值隐藏。

### 前端静态检查

```text
npm run check
TOOLING_MISSING
'eslint' is not recognized
```

仓库 `package.json` 定义了 eslint 命令，但当前 frontend 依赖没有安装 eslint。该结果原样保留，不能声称 lint PASS。

### 浏览器工具链预检

```text
npm run test:browser:preflight
PASS
Node 22.21.0
Playwright 1.61.1
Chromium 149.0.7827.55
```

T58 更新了两个 local-v1 E2E 流程，在点击“结束面试”后显式确认。完整浏览器、移动端、focus trap、屏幕阅读器和断网矩阵属于紧随其后的 T59；本阶段没有以 preflight 冒充完整浏览器 PASS。

### 全仓回归

```text
py -3.11 -m pytest -q
2668 passed
5 failed
218 skipped
1 warning
```

最终 5 个失败均不是 T58 引入：

1. `test_agent_runtime_hardening.py::test_stream_latency_clock_starts_at_first_next`：旧 perf-counter mock 只有两个 tick，当前 runner 消耗更多计时点；
2. `test_interview_graph.py::test_runner_finishes_after_last_question_followup_answer`：旧断言期待终态 decision 为 `next_question`，当前实现为 `finish`；
3. `test_local_v1_hardening_publication_contract.py::test_publication_diff_is_allowlisted_and_implementation_tree_is_unchanged`：历史发布 allowlist 不允许当前 quality 分支的累计改动；
4. `test_postgres_session_deletion.py::test_latest_migration_contract_requires_deletion_lease_and_indexes`：旧断言期待 `followup_decision_v1` 是最新 migration，当前最新为 `followup_prompt_lineage_v1`；
5. `test_reproducibility_preflight.py::test_dependency_source_generator_and_lock_metadata_are_bound`：既有 dependency lock hash 与 metadata 不一致。

首次全仓运行曾有第 6 个、与 T58 相关的旧 `clearArmed` 静态断言；该问题已在自动审查中修复。最终全仓结果由 2667 passed / 6 failed 改善为 2668 passed / 5 failed，没有 T58 相关失败。

## 阶段边界

- T58 Engineering：完成；
- T58 自动审查：完成；
- T58 Provider 调用：不适用，实际为 0；
- T59 浏览器、移动端和无障碍体验验收：尚未完成，下一阶段自动进入；
- T72 最终验收：尚未完成；
- Goal：继续保持 `active`。
