# Interview Quality V1 — T55 自动审查

## 结论

T55 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为
**NOT_REQUIRED**，真实外部 Provider 调用数为 **0**。

本阶段在既有 Plan Revision、InterviewPlanEditor、ProviderPlanRegenerator 和
唯一 revision store 之上实现可编辑计划工作台。服务端 plan/revision/hash 始终是权威，
浏览器本地草稿只用于在请求期间、失败和冲突时保护用户输入。T55 没有增加第二套
revision 创建、session 启动、问题 ID 分配或 Provider 调用链路。

## 编辑工作台

每道题支持编辑问题与 focus、单题保存、Provider 换题、删除、上移和下移；页面也支持
添加自定义题、全量重新生成、查看历史和恢复历史 revision。排序按钮可通过键盘触发，
不依赖拖拽；移动端保留明确的上下移动按钮和触控尺寸。长题显示字符数及结构提示，
不设置任意字符截断上限。

覆盖性操作均需要显式确认：

- 删除题目前确认；
- 单题有未保存文本时，换题前确认；
- 全量 regenerate 前显示会被替换的手工、自定义和换题调整，以及未保存草稿数量；
- 恢复历史版本前确认，并明确恢复会创建新 revision。

自定义题使用后端允许的安全默认值，且不传入 `knowledge_binding`，不能伪造 grounding。
单题与全量 regenerate 均通过既有服务端 Provider 边界；T55 测试只使用确定性替身，
没有真实 Provider 调用。

## Reducer 与并发状态

新增 `interviewPlanState` reducer，分别表达：

- `serverPlan`：最后一次成功服务端响应；
- `localDrafts`：尚未保存的题目草稿；
- `pendingOperation`：当前待完成操作；
- `conflict`：409 冲突及服务端 winner 元数据；
- `failure`：非冲突请求失败；
- `history` / `historyStatus` / `historyError`：历史摘要状态；
- `serverPreview`：冲突时显式查看的服务端版本。

成功响应替换服务端 plan/revision/hash，并只清除已经成功保存的草稿；请求失败和 409
冲突都保留用户输入。冲突界面提供“查看服务端版本”和“复制我的内容”，不会自动覆盖。
刷新服务端版本也保留本地草稿。只有 latest valid revision 且不存在本地草稿、pending、
conflict 或 failure 时才允许开始面试；开始请求本身若返回 409，也会进入可见冲突状态并
禁用开始按钮。

## 历史 revision 安全接口

新增只读接口 `GET /api/interview-plans/{plan_family_id}/revisions`。它复用同一
`revision_store.list_revisions()`，按 newest-first 返回安全摘要：revision ID、revision、
parent revision ID、plan hash、创建时间/原因、source kind、标题、题数和 latest 标记。

该接口不返回问题正文、JD、简历、受保护 source payload、Provider 输出、evidence 内容或
内部 Knowledge Binding hash。未知 family 返回 404。恢复仍通过既有 editor 创建单调递增
的新 revision，不引入另一套历史或恢复存储。

## 自动审查发现与修复

### 刷新可能丢失本地草稿

最初 refresh 路径会以服务端响应初始化编辑状态。自动审查后改为 reducer 内的 server
refresh：更新权威 revision，同时保留尚未保存的本地题目草稿。

### 开始时的 revision 竞争

计划在按钮启用后仍可能被另一个标签页推进。自动审查补充 start-time 409 处理：保留本地
状态、展示服务端 winner、进入 conflict，且不自动重试或覆盖。

### 未保存题目的单题换题

单题 regenerate 会替换当前题。自动审查补充 dirty-question 确认，避免用户无提示丢失
尚未保存的题目文字。

### 对话框键盘行为

确认框增加 `role=dialog`、`aria-modal=true`、Escape 取消和默认聚焦取消按钮。页面未引入
原生 `window.confirm`，以便后续 T59 继续完成完整 focus trap 与返回焦点验收。

## T55 验收

- 每题编辑、换题、删除、上移、下移：PASS；
- 添加自定义题并禁止伪造 grounding：PASS；
- revision 与已保存/本地修改/保存中/冲突/失败状态：PASS；
- 全量 regenerate 与历史恢复前显式确认：PASS；
- 两标签页冲突保留输入并提供查看/复制动作：PASS；
- 键盘排序与移动端上下移动控制：PASS；
- 长问题提示且不任意截断：PASS；
- reducer 区分 server revision/local draft/pending/conflict：PASS；
- 失败保留输入、成功采用服务端 plan/hash：PASS；
- 仅 latest valid revision 可开始：PASS。

## 测试与证据

T55 专用前端 reducer 与页面交互共 20 个测试：

    20 passed, 0 failed, 0 skipped

全前端 Vitest：

    48 passed, 0 failed, 0 skipped

Vite 生产构建为 PASS。构建产物为 CSS 286.99 kB（gzip 38.01 kB）和 JavaScript
526.23 kB（gzip 142.23 kB）。Rollup 如实报告 JavaScript chunk 超过 500 kB 的非阻塞
警告；T55 未通过提高 warning limit 隐藏该警告，也未把代码分割扩展为本阶段验收项。

历史摘要接口新增 2 个 Python API 测试。T55 后端专用/API 回归为 124 passed；扩大邻接回归
覆盖 drafts、revision/editor、配置生成、预算、audit/binding、API、PostgreSQL revision
store、prep/context 和 session serialization：

    242 passed, 0 failed, 0 skipped

全仓回归：

    2705 passed, 9 failed, 3 skipped, 1 warning

相较 T54 的 2703 passed、9 failed、3 skipped，仅增加本阶段 2 个 API 测试，未新增 T55
相关失败。九个既有失败均如实保留：旧 `perf_counter` mock ticks 耗尽；四类 PostgreSQL
cleanup 跨时钟；interview graph 旧 `next_question` 期望；historical publication allowlist
与 quality branch 差异；旧 latest migration 对 `followup_decision_v1` 的期望；既有 dependency
lock hash 漂移。本轮测得 PostgreSQL `clock_timestamp()` 比 Python UTC 快 4.960948 秒，
仅作为跨时钟失败的环境证据，不作为 T55 acceptance gate。

其他验证：

- `compileall app tests`：PASS；
- `git diff --check`：PASS（仅 Windows LF/CRLF 提示）；
- 风险模式扫描：PASS；
- 唯一 `/api/interviews` 启动实现：保持；
- revision 创建继续通过既有 protocol/in-memory/PostgreSQL store 与 editor；
- `npm run check`：TOOLING_MISSING，项目未安装可执行 ESLint；
- 真实 Provider 调用：0。

机器证据：`docs/interview-quality-v1-t55-evidence.json`。

## 边界与后续

- T55 不声明真实 Provider 问题质量、成本、token 使用或 Provider Quality PASS；
- Provider Quality 仍为 `BLOCKED_MODEL_VERSION_DRIFT`，不会阻塞无关 Engineering；
- T56、T57 及 T72 尚未完成；T55 提交后直接进入 T56；
- Goal 在 T72 真正完成前保持 active。

## 回滚

回滚 T55 提交会移除可编辑计划 reducer、工作台 UI、安全历史摘要接口及相关测试和样式。
回滚不得删除既有 plan family/revision/session 数据，也不得恢复 raw-input 启动路径、客户端
Provider 输出注入或自动覆盖冲突的行为。
