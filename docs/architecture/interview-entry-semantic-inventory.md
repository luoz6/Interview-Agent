# Interview Entry Semantic Inventory

> 任务：P1A-T01
> 状态：PASS（只读分析，未合并代码）

## Scope

分析三个 interview entry 语义：

1. `InterviewStartService` — legacy generate-and-start。
2. `InterviewLaunchCoordinator` — prepared plan launch。
3. `plan_revision_id` revision-based start — 新 revision 启动路径。

## 1. InterviewStartService

源码：

- `app/application/interview/interview_start.py`

### Semantic Answers

| question | answer |
| --- | --- |
| 输入 | `job_description`, `resume_text` |
| 输出 | `InterviewTurn` |
| 前置条件 | 提供 session store、workflow service factory、execution runner、runtime store、rollout percent；job/resume 非空由路由校验 |
| 是否生成 plan | 是；默认 `prepare_interview`，可注入 `plan_factory` |
| 是否消费 prepared plan | 否 |
| 是否创建 session | 是；通过 `store.start` 或 `workflow_service.start` |
| 是否需要 command_id | 否 |
| 是否幂等 | 不保证；无 command_id / replay key，重复调用会创建新 session |
| 是否事务 | 取决于 store/workflow 实现，服务本身不显式管理事务 |
| 是否 bootstrap durable workflow | 条件性：`runtime_store == postgres` 且 rollout > 0 时走 workflow service；否则 legacy store |
| 调用入口 | `POST /interviews`，无 `plan_id` 且无 `plan_revision_id` |
| 生产调用方 | `app/api/interview/routes.py::start_interview`；`app/api/shared/dependencies.py::get_legacy_interview_start_service` |

## 2. InterviewLaunchCoordinator

源码：

- `app/services/interview_launch.py`

### Semantic Answers

| question | answer |
| --- | --- |
| 输入 | `plan_id`, `expected_plan_version`, `command_id` |
| 输出 | `dict`，包含 `session_id`, `command_id`, `status`, `current_question`, `bootstrap_status`, `replayed` |
| 前置条件 | prep plan 存在、state 为 editable、未过期、expected version 等于 latest、command_id 为 UUIDv4 |
| 是否生成 plan | 否 |
| 是否消费 prepared plan | 是；将 plan 标记为 consumed，并写入 launch command/session mapping |
| 是否创建 session | 是 |
| 是否需要 command_id | 是；必须是 UUIDv4 |
| 是否幂等 | 是；相同 command 会 replay，不同 command 对已消费 plan 返回冲突 |
| 是否事务 | 是；memory path 使用 `prep_plan_store.transaction`，postgres path 使用 unit of work + `FOR UPDATE` |
| 是否 bootstrap durable workflow | 是；当 session workflow_engine 为 `langgraph-*` 时调用 `workflow_service.ensure_interview_bootstrapped`，失败可 recoverable retry |
| 调用入口 | `POST /interviews`，带 `plan_id` + `expected_plan_version` + `command_id` |
| 生产调用方 | `app/api/interview/routes.py::start_interview`；`app/api/shared/dependencies.py::get_request_interview_launch_coordinator`；`app/services/runtime.py::build_interview_launch_coordinator` |

## 3. Revision-Based Start

源码：

- `app/api/interview/routes.py::_start_interview_locked`

### Semantic Answers

| question | answer |
| --- | --- |
| 输入 | `plan_revision_id`, `expected_revision`, `plan_sha256`, `request_id`, `principal_memory_mode` |
| 输出 | legacy `InterviewTurn` 或 V3 start response |
| 前置条件 | revision 存在、expected revision/hash 匹配、source protected payload 可用、V3 时 JIT 与 postgres/langgraph rollout 可用 |
| 是否生成 plan | 否 |
| 是否消费 prepared plan | 是；使用 revision plan |
| 是否创建 session | 是 |
| 是否需要 command_id | 否；使用 deterministic `request_id` / session_id |
| 是否幂等 | 是；重复 start 会 load existing session replay |
| 是否事务 | 使用 revision store source reference recovery lock；session creation 失败会 rollback reference/principal choice |
| 是否 bootstrap durable workflow | 是；V3 或 postgres+rollout 时调用 workflow service |
| 调用入口 | `POST /interviews`，带 `plan_revision_id` |
| 生产调用方 | `app/api/interview/routes.py::start_interview` |

## Production Caller Summary

| entry | route | runtime wiring | tests / consumers |
| --- | --- | --- | --- |
| Legacy start | `POST /interviews` | `get_legacy_interview_start_service` | `tests/acceptance/test_api.py` |
| Prepared plan launch | `POST /interviews` | `get_request_interview_launch_coordinator` | `tests/unit/test_interview_launch.py`, `tests/acceptance/test_interview_launch_api.py` |
| Revision start | `POST /interviews` | revision store + workflow service | `tests/acceptance/test_interview_plan_configured_e2e.py` |

## Key Distinction

- `InterviewStartService` 是 legacy generate-and-start use case。
- `InterviewLaunchCoordinator` 是 prepared-plan consumption use case。
- revision-based start 是新的 plan-revision start path。

三者不因 `start` / `launch` 名称相似就视为同一语义。
