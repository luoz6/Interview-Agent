# MA0-T09 — Orchestration Lifecycle / Drain Inventory

状态：**PASS**（2026-09-18）  
范围：只盘点当前 execution/session 生命周期与 Scheduler cutover 约束；不实现 Scheduler，也不隐式迁移旧 execution。

## 1. 结论先行

当前 interview session 在创建时选择并持久化 `workflow_engine` 与
`graph_schema_version`。创建完成后，路由依据 session 中已保存的值，而不是再次读取
当前 rollout 配置。因此切换 Scheduler 不会改变已经存在的 session。

切换政策确定为：

```text
NEW EXECUTIONS
→ scheduler-v1（仅在显式 cutover gate 通过后）

EXISTING EXECUTIONS
→ 继续绑定的 legacy/langgraph orchestration version，自然 drain
```

默认不支持隐式 migration。旧 execution 只能采用以下三种已明确的产品政策之一：

```text
A. natural drain（本 T09 采用）
B. explicitly supported migration（未来若实现，必须单独设计、测试、审计）
C. explicit termination according to product policy
```

## 2. 当前 engine matrix

| 创建条件 | 新 session 的 engine | 持久化绑定 | 后续路由 |
| --- | --- | --- | --- |
| 非 PostgreSQL、runtime 未启用、或 `rollout_percent = 0` | `legacy` | `workflow_engine=legacy`, `graph_schema_version=NULL` | legacy store |
| PostgreSQL + runtime enabled + hash bucket 命中 rollout，`default_graph_version=langgraph-v1` | `langgraph-v1` | 两字段均为 `langgraph-v1` | v1 graph + v1 checkpoint |
| 同上，`default_graph_version=langgraph-v2` | `langgraph-v2` | 两字段均为 `langgraph-v2` | v2 graph + v2 checkpoint |
| `interview-plan-v3` 且 PostgreSQL/runtime/v3 条件全部满足 | `langgraph-v3` | 两字段均为 `langgraph-v3` | v3 graph + bootstrap outbox/checkpoint |
| 未来 Scheduler cutover 后的新 execution | `scheduler-v1`（目标绑定） | 应持久化独立 `orchestration_version`（当前代码尚未实现） | Scheduler execution state |

依据：`app/domain/interview/state.py::choose_workflow_engine`、
`app/runtime/interview_workflow.py::start`、
`app/runtime/interview_launch.py`。`langgraph-v3` 只接受
`interview-plan-v3`，不能被 rollout 随意降级到其它图。

## 3. Session creation binding

1. `InterviewWorkflowService.start()` 为新 session 生成/接受 `session_id`，读取当前
   runtime 配置，并在创建前选择 engine。
2. legacy session 直接由 legacy store 创建；durable session 先写 durable shell。
3. v3 在同一个业务 UoW 中写 session shell 与 bootstrap outbox，然后 commit，再执行
   bootstrap；v1/v2 写入对应 durable shell 并使用 `default_graph_version`。
4. 持久化内容还包括 `state_version`、`checkpoint_version`、projection hash、plan
   binding/revision/hash、memory policy 与 `deletion_status`。
5. 已有 session 的 engine 不会因新的 rollout、默认 graph version 或配置变化而重新选择。
   `graph_for_session()` 读取持久化 `graph_schema_version`，交给
   `VersionedGraphRegistry` exact lookup。

因此，未来应增加并持久化类似 `orchestration_version=legacy-v3|scheduler-v1` 的
顶层绑定；在该字段落地前，现有的 `workflow_engine + graph_schema_version` 是旧
execution 的切换边界，不能由 Scheduler 通过“当前默认值”推断或覆盖。

## 4. Active lifecycle、WAIT_USER 与 restart/resume

当前 durable graph 的正常路径是：

```text
create session
→ graph invoke
→ wait_for_answer (WAIT_USER)
→ resume(answer command)
→ validate/follow-up/next question
→ wait_for_answer 或 complete
```

- `wait_for_answer` 通过 checkpoint 保存 graph cursor/state；测试验证
  `snapshot.next == ("wait_for_answer",)`。
- `resume_command()` 使用与该 session 绑定 graph 的 LangGraph `Command(resume=...)`。
  retry timer 使用同一机制，并在恢复前校验 generation/attempt，过期 retry 会丢弃。
- `snapshot()` 将 graph `.next` 映射为 public pending action；业务 session projection
  与 checkpoint 目前是混合运行形态，但两者都按同一 session/version 读取。
- WAIT_USER 没有“等待一段时间后自动改用 Scheduler”的语义。等待时长不会改变
  `workflow_engine`、`graph_schema_version` 或 checkpoint owner。
- 进程重启后，必须保留旧 graph registry 与 checkpointer，并以 session 已持久化的
  version 恢复；不得按新 rollout 重建到另一版本。v3 bootstrap 对已有 snapshot 有
  resume/已提交 snapshot 的幂等分支。

## 5. Old workflow checkpoint 处理

`VersionedGraphRegistry.get()` 只允许 exact version lookup，未知版本直接报错，不做
跨版本 fallback。旧 checkpoint 因此有三种可审计结果：

1. version 仍注册且 checkpoint 完整：继续旧 graph，自然 drain；
2. version/checkpoint 缺失但有未来明确的迁移方案：进入显式 migration 流程，不能静默
   转为 Scheduler；
3. 无法恢复：进入 operator-visible recovery/termination，保留错误、审计与 tombstone，
   按产品政策显式终止。

删除 session 时 `checkpointer.delete_thread(session_id)` 必须成功；数据库 FK 不会自动
删除 LangGraph checkpoint。旧 checkpoint 是删除完成与 old-runtime removal gate 的必需
盘点项。

## 6. Session deletion lifecycle

当前删除是两阶段、可重放且带 fencing 的流程：

```text
active
→ mark_deleting (仅 active → deleting)
→ deletion job claim/lease
→ delete workflow control + LangGraph thread/checkpoint
→ delete generation rows
→ delete question memory / context-artifact owner refs
→ delete report jobs/history/artifacts
→ delete failure state / principal memory
→ delete business session
→ record tombstone + complete job
```

实现依据：`SessionDeletionService`、`SessionDeletionWorker`、
`InterviewWorkflowService.purge_session()`、Postgres session repository。删除 Scheduler
execution 不能只删 ExecutionState；必须调用 session-bound workflow cleanup，并覆盖旧
graph checkpoint、outbox/commands/receipts、generation、report 与 owner references。
删除中的 session 不能继续接受普通业务写入；失败由 job retry/lease 与 tombstone replay
继续处理。

## 7. Cutover / drain policy

### 7.1 今天切到 Scheduler 时的规则

问题：**如果今天切到 Scheduler，昨天正在 WAIT_USER 的 session 怎么办？**

答案：昨天的 session 继续使用它已经持久化的 `workflow_engine + graph_schema_version`。
保留该版本的 graph registry 与 checkpoint；用户今天提交的 answer 仍由旧 runtime 的
`resume_command()` 处理。Scheduler 只接收 cutover 之后创建的新 execution，不接管旧
WAIT_USER execution。旧 execution 完成后自然 drain；若无法恢复，则只能走显式
migration 或显式 termination，绝不能静默迁移。

### 7.2 Cutover gate

Scheduler-v1 只允许用于新 execution，且必须有可审计的 cutover 开关/版本绑定。切换前
至少应能按 orchestration version 统计：active、WAIT_USER、pending command、checkpoint
与 deletion 中的数量，并能对旧版本继续提供 resume 与删除能力。T09 不宣称这些
Scheduler 字段已经实现；这里定义的是 MA0 必须满足的约束。

## 8. 删除旧 runtime 的硬门槛

只有满足下列全部条件，或所有旧 execution 已经逐个完成明确 migration/termination，才
允许删除旧 runtime、旧 graph registry、旧 checkpointer schema 或临时 orchestration switch：

```text
active old executions = 0
resumable old WAIT_USER executions = 0
pending old commands = 0
required old checkpoints = 0
```

`deletion_status=deleting` 的旧 session、未完成 deletion job、旧 bootstrap outbox、旧
generation retry 与 report/review job 也必须计入 pending/required 资源，不能以“主表已删”
代替清零证明。

## 9. 验证证据与边界

已执行：

- 92 项非 PostgreSQL focused lifecycle/rollout/WAIT_USER/recovery/deletion/contract/
  legacy-removal 测试：**92 passed**。
- `tests/unit/test_durable_interview_graph.py`：57 项通过；5 项在 PostgreSQL fixture
  setup 阶段因缺少仓库要求的 `approval_id`、`approval_receipt_sha256`、
  `approved_target_fingerprint`、`database_allowlist`、`expires_at` 外部 scope approval
  被阻断，不是断言失败。
- 两个 PostgreSQL integration 文件：1 passed、1 skipped、10 个同样因外部 scope
  approval 在 setup 阶段阻断。
- 计划列出的 `tests/unit/test_interview_workflow.py` 在当前工作树不存在；相关行为由
  `test_dual_langgraph_rollout.py`、durable graph、bootstrap outbox 与 runtime lifecycle
  测试覆盖。

这些外部批准限制不改变本盘点对代码路径和 cutover 规则的结论，但意味着本机未完成真实
PostgreSQL restart/delete 的运行时证明；该限制已明确记录，不得伪称为全绿集成验证。

## 10. T09 自判定

| 验收项 | 结果 | 依据 |
| --- | --- | --- |
| workflow engine 与 session creation binding | PASS | engine matrix、持久化字段、exact graph lookup 已记录 |
| active lifecycle 与 WAIT_USER lifespan | PASS | `wait_for_answer` cursor、resume/retry、无 TTL 换引擎规则 |
| restart/resume 与 old checkpoint | PASS | 版本绑定恢复、无 fallback、缺失时显式 recovery/termination |
| session deletion | PASS | deleting → worker → checkpoint/control/artifact/tombstone 全链路 |
| workflow version compatibility | PASS | v1/v2/v3 exact registry、v3 plan compatibility、旧版本保持 |
| Scheduler cutover/drain policy | PASS | new-only scheduler、existing bound version、三种旧 execution 政策 |
| old runtime removal gates | PASS | 四个清零门槛及 deletion/pending 资源扩展已明确 |
| PostgreSQL full runtime proof | LIMITED | 外部 scope approval 缺失，setup 阶段阻断 |

综合判定：**PASS（带已记录的 PostgreSQL 外部批准验证限制）**。MA0-T09 交付物已完成；
按严格串行要求，完成本 Task 后停止，不自动进入 MA1。
