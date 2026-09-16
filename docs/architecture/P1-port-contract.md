# P1 Port Contract

- Status: Accepted
- Related: P1B-T02

## Canonical Use Case

```text
LaunchPreparedInterview
```

## Ports

新增 `app/ports/interview_entry.py`，定义：

- `Clock`
- `IdGenerator`
- `PrepPlanRepository`
- `InterviewLaunchRepository`
- `InterviewSessionRepository`
- `DurableExecution`

## Why Not All Candidate Ports

候选中的 `UnitOfWork` 不进入 P1 application-facing port：

- Application 不应看到 transaction/cursor。
- Memory 和 PostgreSQL Adapter 内部自行实现原子性。
- 暴露 `UnitOfWork` 会重新引入 `cursor` 或 `durability` 分支风险。

因此 P1 Port 只包含上述 6 个。

## Forbidden Leakage

Port 不得暴露：

```text
cursor
psycopg connection
SQL row
durability == postgres
LangGraph checkpoint
Redis client
```

Application 中禁止出现：

```python
if repository.durability == "postgres":
```

## Semantics

### Clock

- 提供 `utc_now()`。
- 用于 plan expiry 判断。

### IdGenerator

- 提供 `new_session_id()`。
- 让 application 不直接依赖 uuid4。

### PrepPlanRepository

- `cleanup()`
- `find_editable(plan_id)`
- `consume(...)`

Adapter 内部处理事务与存储差异。

### InterviewLaunchRepository

- `find(plan_id, command_id)`
- `create_pending(...)`
- `mark_ready(...)`
- `mark_failed_recoverable(...)`

负责 launch command idempotency 和 bootstrap status。

### InterviewSessionRepository

- `start(...)`
- `get(session_id)`
- `delete_session(session_id)`

Adapter 决定如何创建/读取 session。

### DurableExecution

- `ensure_bootstrapped(session_id)`

Application 不分支 legacy/durable；Adapter 实现对应行为。

## Error Contract

`app/ports/interview_entry.py` 定义：

```text
PrepPlanNotFound
PrepPlanExpired
PrepPlanVersionConflict
PrepPlanAlreadyConsumed
```

Application 只处理这些稳定错误，不处理 Adapter 内部 SQL/连接异常。

## Acceptance Target

同一 `LaunchPreparedInterview` application use case 可以由：

- Memory Adapter
- PostgreSQL Adapter

实现，application 本身无需分支。

## Non-Goals

- 不实现 Adapter。
- 不创建 `ports/execution/checkpoint_store.py`。
- 不修改现有 `app/services` 实现。
