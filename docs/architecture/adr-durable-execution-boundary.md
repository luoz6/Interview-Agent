# ADR: Durable Execution Boundary

- Status: Accepted
- Date: 2026-09-11
- Scope: durable interview/review execution and LangGraph checkpointing
- Related: P0D-T04, P5

## Decision

冻结 Durable Execution 的核心边界：

- Core Port 只表达稳定的执行语义。
- LangGraph Adapter 私有化 checkpoint 与 graph-state 细节。
- Application/Domain 不得知道 LangGraph checkpoint 表示。

## Core Port Can Express

```text
start
resume
suspend
execution identity
execution status
lease
renewal
fencing
idempotency
stale worker rejection
```

这些能力是业务可靠的 durable execution 契约，不属于具体 workflow engine。

## LangGraph Adapter Keeps Private

以下概念必须保持 LangGraph Adapter 私有：

```text
channel values
node states
pending sends
checkpoint metadata
checkpoint serialization
checkpoint codec
```

Application、Domain、Ports 不得直接读取或构造这些表示。

## Boundary Rules

1. Application 不 import `langgraph`。
2. Domain 不 import `langgraph`。
3. Ports 不暴露 checkpoint object、channel value、pending send、codec 或 serialization detail。
4. Adapter 可以将核心执行语义映射为 LangGraph interrupt/checkpoint/state。
5. Runtime 可以构造 Adapter，但 Application 只看到 Port。

## Current Owners

- `app/domain/interview/state.py` owns the engine-neutral interview state contract.
- `app/domain/interview/transitions.py` and `rounds.py` own pure state/event rules.
- `app/graphs/durable_interview_graph.py` and `durable_review_graph.py` own LangGraph node/edge wiring.
- `app/runtime/langgraph_runtime.py` owns checkpointer lifecycle and graph registry composition.
- `app/adapters/workflows/checkpointer_schema.py` owns PostgreSQL schema inspection for the checkpointer.
- `app/adapters/persistence/postgres/*workflow_store.py` owns durable state/effect persistence.

`app/graphs/interview_state.py`, `interview_transitions.py`, and
`interview_rounds.py` are compatibility exports only. Application and Domain do
not import `app.graphs` or `langgraph`.

## Critical Rule

不得为了目录完整提前创建：

```text
ports/execution/checkpoint_store.py
```

除非有实际 application-level use case 证明需要它。

## Consequences

- Core Port 可以在未来支持非 LangGraph durable execution 实现。
- LangGraph checkpoint 升级不会直接破坏 Application/Domain。
- fencing、lease、stale worker rejection 可以在不依赖具体 workflow engine 的情况下测试。

## Non-Goals

本 ADR 不：

- 创建没有 application use case 的 `ports/execution/` 平行层。
- 修改 LangGraph checkpoint wire format。
- 替换当前 Durable Execution 实现。
