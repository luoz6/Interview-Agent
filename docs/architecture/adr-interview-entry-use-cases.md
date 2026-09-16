# ADR: Interview Entry Use Cases

- Status: Accepted
- Date: 2026-09-11
- Related: P1A-T02

## Decision

选择：

```text
A: 两个合法 Use Case
```

不是：

- B: Legacy Facade + Canonical Use Case
- C: Duplicate Implementation

## Canonical Semantic Names

```text
StartInterviewFromInputs
LaunchPreparedInterview
```

## Definitions

### StartInterviewFromInputs

- 输入：`job_description`, `resume_text`
- 行为：生成 plan，然后启动 interview session。
- 生产调用：`POST /interviews` 的无 `plan_id` / 无 `plan_revision_id` legacy path。
- 当前实现：`InterviewStartService`。

### LaunchPreparedInterview

- 输入：已保存的 plan identity、expected version、command identity。
- 行为：消费 prepared plan，创建 session，并在 durable workflow 需要时 bootstrap。
- 生产调用：
  - `POST /interviews` 的 `plan_id` path，当前实现为 `InterviewLaunchCoordinator`
  - `POST /interviews` 的 `plan_revision_id` path，当前实现为 revision-based start

## Why Not Duplicate Implementation

两者不是同一 use case：

- `StartInterviewFromInputs` 生成 plan。
- `LaunchPreparedInterview` 消费已生成 plan。
- 前置条件、幂等性、事务边界、command identity 和 bootstrap 行为不同。

不能仅凭 `start` / `launch` 名称相似判断重复。

## Why Two Legal Use Cases

- `StartInterviewFromInputs` 是合法的输入驱动入口。
- `LaunchPreparedInterview` 是合法的 prepared plan 消费入口。
- revision-based start 与 plan_id launch 共享同一 prepared-plan 语义，应归入 `LaunchPreparedInterview`，而不是第三个顶层 use case。

## Consequence

P1 vertical slice 可以只选择一个 use case 做架构样板。默认候选：

```text
LaunchPreparedInterview
```

该选择在 P1B-T01 中进一步冻结，但本 ADR 不因 legacy `StartInterviewFromInputs` 仍在生产调用中而把它降级为 facade。

## Non-Goals

- 不合并代码。
- 不删除 `InterviewStartService`。
- 不把 revision-based start 单独命名为新 use case。
