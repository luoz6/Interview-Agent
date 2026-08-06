# Interview Quality V1 — T53 自动审查

## 结论

T53 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为
**NOT_REQUIRED**，真实外部 Provider 调用数为 **0**。

T53 已把 T50–T52 的配置化计划接入 T12 已发布的唯一端到端链路，并用公开 API
验证：

```text
selected revision response plan_sha256
== start request plan_sha256
== persisted session plan_sha256
== immutable session plan_snapshot 的 canonical hash
```

四种时长、三种难度和四种 focus 共 48 个组合均从 `/api/prep` 创建 V2 revision，
再通过既有 `POST /api/interviews` 启动。所有组合的启动 Provider 调用为 0，完整
configuration 和 plan snapshot 均写入 session。T53 没有增加第二个 start endpoint、
第二套 revision store、第二套 hash 算法或旁路 session 创建逻辑，也没有修改生产代码。

## 唯一权威链路

T53 复用的链路是：

```text
POST /api/prep
  → configured prepare service
  → InterviewPlanRevisionStore.create_initial()
  → plan_revision_id / revision / plan_sha256

PATCH or regenerate existing revision
  → InterviewPlanEditor / ProviderPlanRegenerator
  → InterviewPlanRevisionStore.create_next_revision()

POST /api/interviews
  → selected revision + expected_revision + plan_sha256 校验
  → session_plan_binding_from_revision()
  → InterviewSessionStore.start()
  → immutable plan/configuration snapshot
```

启动接口不接受原始 JD/简历作为替代输入，也不会调用 plan generator。启动所需的
JD、简历、job tags、plan 和 configuration 全部从已保存 revision 及其 source record
读取。

## 48 组合配置矩阵

专用矩阵逐一覆盖：

```text
duration:   15 / 30 / 45 / 60
difficulty: foundation / intermediate / advanced
focus:      technical_depth / system_design / project_review / balanced
```

每个 focus 使用代表性的题型顺序，再按该时长的题数预算形成
`question_type_budget`。每个组合均验证：

- `/api/prep` 返回的 configuration snapshot 与请求完全相同；
- 15/30/45/60 分钟分别生成 3/5/7/9 道题；
- prep response 的 revision identity/hash 与 store 中 revision 相同；
- start request 原样携带所选 revision 的 ID、revision 和 hash；
- start 前后的 prep Provider 调用数不变；
- session 使用的 Provider spy 调用数保持 0；
- session 的 revision ID、family ID、revision、plan hash 和 configuration 与已保存
  revision 完全相同；
- 内部 session 保存完整不可变 V2 `plan_snapshot`。

矩阵之外还覆盖 10 题配置；因此 9 题和 10 题都能由同一个启动接口正常启动。

## 阻塞 E2E 场景

### 预览后直接开始

48 个配置组合全部执行真实 API preview→start，并验证 session snapshot/hash。不存在
只验证服务函数而绕开路由的“伪 E2E”。

### 手工编辑后开始

编辑题目文本创建下一 immutable revision。测试确认 latest revision hash 已变化，start
request 使用新 revision/hash，session snapshot 也固定为新 revision，而不是 prep 时的
旧计划。

### 换题后开始

既有单题 regenerate endpoint 创建 replacement question。测试确认新 question ID、
`origin=regenerated` 和 `replaces_question_id`，随后使用该 revision/hash 启动。启动本身
不再次调用 Provider。

### 排序后开始

移动第一题到末尾会创建下一 revision。所有 opaque question ID 保持不变，position
重新成为连续 `1..N`，session snapshot 使用排序后的 hash 和顺序。

### 两标签页冲突

两个请求从同一 expected revision 编辑。先到请求创建 revision 2；后到请求返回 409，
响应包含 winner 的 revision ID、revision 和 hash，且不会创建第三个 revision。winner
随后可以正常启动。

### 修改 JD 后旧计划 stale

Draft 先以与 prep source 完全相同的 JD/简历绑定 revision，状态为 active。修改 JD 后
重新保存，Draft 保留原 plan identity 但状态变为 stale。该测试验证 active→stale 的
真实转换，不通过一开始就使用不匹配 source 来制造假阳性。

### Provider 在 prep 后不可用

prep 成功后同时将 prep generator 和 regeneration double 设为 unavailable。已保存
revision 仍然正常启动；generator 调用数不增加、regenerator 调用数为 0、session
Provider spy 调用数为 0。这证明 Provider 故障不能中断已保存计划的启动。

### Configured fallback

Provider test double 返回少于 exact budget 的题目，prepare service 走 T52 冻结的完整
configured deterministic fallback。fallback 保留 60 分钟、advanced、system_design
和 10 题预算，保存为正常 V2 revision，再由同一启动接口创建 session。

## 内部完整 snapshot 与公开隐私投影

首轮自动审查发现，公开 `GET /api/interviews/{session_id}` 的 `plan_snapshot` 会按既有
隐私契约删除 `prep_context.binding_snapshot`。因此公开 JSON 不应与内部完整 snapshot
做逐字节相等比较，否则会把正确的脱敏行为误判为一致性缺陷。

最终测试分两层验证：

1. 从 session store 验证内部 `plan_snapshot` 与 persisted revision 的完整 V2 JSON
   完全相同；
2. 从公开 GET 响应验证 revision identity、plan hash、configuration、questions 和
   schema 不变，同时确认私有 `binding_snapshot` 未公开。

这同时证明了不可变 session snapshot 和公开数据最小化，没有为了通过 hash 测试而
泄露 grounding 内部绑定。

## 自动审查发现与修正

1. **公开脱敏快照被错误当作内部完整快照**：将完整一致性断言移到 session store，
   公开 API 单独验证 identity/hash 和隐私投影。
2. **stale fixture 初始 source 已不一致**：改为先用 prep 的 exact JD/简历创建 active
   Draft，再修改 JD，证明真实的 active→stale 状态转换。
3. **共享服务审查**：未发现需要生产修复的 T53 缺陷。按任务要求保留唯一 start
   路径，只新增回归矩阵，没有用测试专用入口或第二套服务绕过 T10–T12。

## 测试与证据

T53 专用文件收集并通过 **55 tests**：

```text
48 configuration matrix cases
+ 7 blocking-scenario test functions
= 55 pytest cases
```

其中一个 10题/fallback 用例执行两次 start，而 stale 用例不启动，因此总计执行
**55 次 synthetic start operations**；启动边界 Provider spy 调用数为 **0**。

宽邻接回归覆盖 T50–T53 generation/budget/revision/editor/API、draft、prep/context、
session serialization、PostgreSQL Plan Revision Store 和 PostgreSQL Session Store：

```text
296 passed, 0 failed, 0 skipped
```

全仓回归：

```text
2696 passed, 7 failed, 3 skipped
```

总用例数相对 T52 恰好增加 55，T53 专用和邻接测试没有新增失败。本次全仓失败为：

1. `test_agent_runtime_hardening.py`：旧 `perf_counter` mock ticks 耗尽；
2. `test_interview_generation_store.py`：PostgreSQL cleanup cutoff 跨时钟偏差；
3. `test_interview_graph.py`：旧断言期望 `next_question`，当前最终态为 `finish`；
4. `test_interview_workflow_store.py`：PostgreSQL cleanup cutoff 跨时钟偏差；
5. `test_local_v1_hardening_publication_contract.py`：历史 publication allowlist 与 quality
   branch 的实现差异不兼容；
6. `test_postgres_session_deletion.py`：旧 latest migration 断言仍期望
   `followup_decision_v1`，当前为 `followup_prompt_lineage_v1`；
7. `test_reproducibility_preflight.py`：既有 dependency lock hash 漂移。

本轮测得 PostgreSQL `clock_timestamp()` 比 Python UTC 快 **5.098216 秒**，而两个
cleanup 测试仍使用 Python `now + 1s` cutoff。T52 中另两个同类时钟敏感失败本轮没有
复现；这不被描述为永久修复，也不改变它们的环境敏感性质。全仓失败和 skip 没有被
伪装成 PASS，且未作为 T53 acceptance gate。

其他验证：

```text
compileall app tests: PASS
git diff --check: PASS
frontend Vitest: 28 passed
frontend Vite production build: PASS
npm run check: TOOLING_MISSING（仓库未安装 package script 引用的 eslint）
real Provider calls: 0
```

T53 没有前端代码变更。ESLint 缺失是额外诊断结果，不被写成 PASS，也不在 T53
擅自扩 scope 修改前端依赖。

## 真实性与阶段边界

- T53 没有真实 Provider 请求、费用或 token 用量；测试中的 Provider 都是确定性
  double。
- 48 组合和阻塞 E2E 证明工程 identity/persistence/conflict/zero-call 行为，不证明
  真实 Provider 的问题质量。
- T54 的编辑审计与 Knowledge Binding 重校验尚未完成。
- T55–T56 的可编辑计划和配置 UI 尚未完成。
- T57 的真实初始问题 Provider Quality benchmark 尚未执行，不能标记 PASS。
- T53 Engineering PASS 不改变任何 Provider 或人工 Quality Gate 状态。

机器证据：`docs/interview-quality-v1-t53-evidence.json`。

## 回滚

T53 只有测试与证据文件，没有生产代码变更。回滚 T53 提交会移除新增矩阵和审查
记录，不会改变任何 revision、source、session snapshot、启动行为或 Provider 授权。
不得通过回滚重新允许启动时生成计划，也不得删除既有持久化对象。
