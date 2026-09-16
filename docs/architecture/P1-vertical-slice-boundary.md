# P1 Vertical Slice Boundary

- Status: Accepted
- Related: P1B-T01

## Selected Canonical Use Case

```text
LaunchPreparedInterview
```

选择依据：

- P1A-T02 已确认两个合法 use case。
- `LaunchPreparedInterview` 的输入更稳定、更容易建立 Port 和 Adapter。
- 该 use case 包含明确的幂等、版本冲突、事务和 durable bootstrap 语义，适合做架构样板。

## IN SCOPE

以下内容进入 P1 vertical slice：

- `LaunchPreparedInterview` application use case。
- 消费 prepared plan record。
- 校验：
  - plan exists
  - plan not expired
  - state is editable
  - expected_plan_version matches latest
  - command_id 是 UUIDv4
- 创建 interview session。
- 保证同一 command replay 和不同 command conflict。
- 决定是否 bootstrap durable workflow。
- Memory 与 PostgreSQL 实现路径都由同一 application use case 支持。

## OUT OF SCOPE

以下内容不进入 P1 vertical slice：

- `StartInterviewFromInputs` legacy use case。
- 生成 plan / `prepare_interview`。
- revision-based start 的全部实现。
- 迁移所有 prep plan、knowledge、materials、report、context、memory workflow。
- 删除 `app/services/interview_launch.py`。
- 创建完整 ports/execution 目录。

## Existing Dependencies

当前实现依赖：

- `app/services/interview_launch.py`
- `app/services/prep_plans.py`
- `app/services/in_memory_prep_plan_store.py`
- `app/services/postgres_prep_plan_store.py`
- `app/services/in_memory_interview_launch_repository.py`
- `app/services/postgres_interview_launch_repository.py`
- `app/services/session.py`
- `app/services/postgres_session.py`
- `app/services/interview_workflow.py`

这些依赖在 P1C 中逐步被 ports/adapters/runtime wiring 替换。

## Temporary Compatibility

- 当前 `InterviewLaunchCoordinator` 继续作为旧入口使用。
- P1C-T05 可将其改为 legacy facade，只做 argument/result mapping，不保留业务逻辑。
- 不新增 `app/services` 普通实现文件。

## Ports Needed

只建立本 slice 实际需要的 Port：

- `PrepPlanRepository`
- `InterviewLaunchRepository`
- `InterviewSessionRepository`
- `UnitOfWork`
- `DurableExecution`
- `Clock`
- `IdGenerator`

不得机械创建全部；最终以 P1B-T02 为准。

## Behavior That Must Remain Unchanged

- 正常 launch 成功。
- invalid plan / expired plan / version mismatch 失败语义。
- same command replay 返回相同 session。
- same plan different command 返回冲突。
- bootstrap success 返回 ready。
- bootstrap recoverable failure 返回 retryable pending。
- 响应字段保持兼容：
  - `session_id`
  - `command_id`
  - `status`
  - `current_question`
  - `bootstrap_status`
  - `replayed`

## Non-Goals

- 不在本任务实现代码。
- 不修改 API 契约。
- 不修改 Prompt / LLM Provider。
