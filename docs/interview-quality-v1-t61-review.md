# Interview Quality V1 — T61 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=NOT_REQUIRED_DETERMINISTIC_ENGINEERING
overall_status=PASS
plan_requirements=17/17
acceptance_invariants=4/4
official_acceptance=54 passed / 0 failed / 0 skipped
expanded_adjacent_regression=291 passed / 0 failed
frontend=67 passed / 0 failed
provider_calls=0
screenshots=0
open_findings=0
```

T61 已完成崩溃恢复、幂等、Lease fencing、事务窗口、孤儿恢复和有界终止验收。正式 runner 强制使用可达 PostgreSQL，禁止 skip，并把 17 条 Plan 要求与 4 条验收不变量映射到 44 个唯一 pytest 节点；参数展开后执行 54 项测试。PostgreSQL 版本为 16.14，pgvector 为 0.8.6，验收 canonical SHA-256 为 `e251c592b8836840df83496b635f2e6b7a3fc09c2662d9ba762ba5accafebe52`。

## 17 条要求覆盖

| ID | Plan 要求 | 权威证据摘要 | 状态 |
|---|---|---|---|
| T61-R01 | Plan revision 并发写 | PostgreSQL expected-revision 并发写只有一个 winner，冲突码稳定 | PASS |
| T61-R02 | Session start 重复 request | 服务端稳定身份、并发本地重放、PostgreSQL 重启重放、payload 冲突关闭 | PASS |
| T61-R03 | Decision Lease 丢失与 stale completion | fencing token 拒绝旧 worker，retry 有上限 | PASS |
| T61-R04 | Generation Lease 丢失与 SSE cursor | reset 先于替换 chunk，reconnect cursor 可恢复，旧 worker 全部写入被拒绝 | PASS |
| T61-R05 | Report job Lease 与 Artifact commit | job fencing 与 Artifact/review-run 同事务完成 | PASS |
| T61-R06 | Artifact insert 前失败 | 新增 `before_artifact` 注入点，内存与 PostgreSQL 均无部分写入 | PASS |
| T61-R07 | Artifact 已写、head 未切换 | `artifact` 窗口回滚 Artifact 与后续投影 | PASS |
| T61-R08 | head 已更新、job 未完成 | `head` 窗口整事务回滚 | PASS |
| T61-R09 | job completed、session/review-run/outbox 未提交 | `job`、`review_run`、`session` 与 outbox 窗口全部回滚 | PASS |
| T61-R10 | commit 成功但 response 丢失 | 重建 store 后按 `source_job_id + artifact_sha256` 返回同一 Artifact；payload 漂移冲突 | PASS |
| T61-R11 | rescore failed 保留旧 active | 失败 job 不移动 active head | PASS |
| T61-R12 | active source job 与 latest failed job 同时可见 | API 同时暴露 active Artifact 与失败历史 | PASS |
| T61-R13 | 多次 rescore 与活动 job 部分唯一约束 | revision 1→2→3 单调，重叠活动 job 被拒绝 | PASS |
| T61-R14 | orphan report job 恢复 | orphan projection 有稳定原因并可确定性补队列 | PASS |
| T61-R15 | 服务重启后的 command 重放 | 七个崩溃点重启后无重复业务输出；Session start store 重建后同样重放 | PASS |
| T61-R16 | 最大 retry 后安全终止 | Generation 与 Report retry exhausted 都进入安全终态 | PASS |
| T61-R17 | 无无限循环 | node-step、Provider-call、progress、checkpoint 与 stream-event guard 均有稳定原因码 | PASS |

完整节点映射位于 `tests/golden/interview_quality_v1/t61-recovery-acceptance-v1.json`；生成器会验证 17 个 ID 的完整顺序、每条要求非空证据码、测试节点存在、节点去重投影和 canonical hash。

## 四条验收不变量

1. 同一命令最多一个业务副作用：Session start、Decision 与 command restart replay 均验证单写。
2. 同一 source job 最多一个 Artifact revision：response-loss replay 返回同一 `report_id` 与 hash，多次 rescore 仍单调。
3. stale worker 不能覆盖新结果：Decision、Generation 与 Report 三条 Lease 链都验证 fencing。
4. 所有恢复路径有稳定 reason code 和证据：17 条要求全部绑定至少一个小写、机器稳定的证据码。

## 自动审查发现与修复

### 1. Session start 原先没有幂等身份

旧 `POST /api/interviews` 每次生成随机 session ID，重复请求会创建两个业务会话。修复后 `request_id` 必填，服务端以 `plan_family_id + request_id` 生成稳定 UUIDv5；并发 PostgreSQL 唯一冲突会读取 winner，本地 store 使用 `RLock` 防止重复 first question。服务重建后的相同请求仍返回同一会话且只存在一条初始 interviewer message。

所有生产启动路径都发送已验证的 revision contract 与稳定 request ID。失败或响应丢失时保留 ID；成功响应后清除 ID，使后续用户主动启动新面试获得新身份。ADR 已同步必填字段、重放和冲突语义。

### 2. Artifact insert 前失败与 insert 后失败曾被折叠

旧注入点 `artifact` 已位于 insert/staging 之前，无法区分 Plan 要求的两个事务窗口。新增 `before_artifact`，并保留 `artifact` 表示“Artifact 已插入或 staged、head 尚未切换”。内存与 PostgreSQL 参数化测试分别覆盖 `before_artifact`、`artifact`、`head`、`job`、`review_run`、`session`。

### 3. 缺少 response-loss 与多次 rescore 的 PostgreSQL 证据

新增 store 重建后的 Artifact replay 测试，验证同 source job、同 hash、同 report ID、单一 revision，以及同 source job payload 改变时 fail closed。新增三代 Artifact 历史与 partial unique constraint 测试，验证活动 job 不能重叠、history/source chain 保留且 active head 只在成功 publish 后移动。

### 4. 自动复审发现同 idempotency key 可绕过请求字段校验

第一版修复在检查 `expected_revision` 和 `plan_sha256` 之前返回现有会话，因此同一 `plan_revision_id + request_id` 携带被篡改的 revision/hash 仍会错误成功。最终实现把持久化 Session binding 与本次两个字段一起比较；任一不一致返回稳定的 `session_start_request_conflict`，且不会产生第二个会话。该发现已加入正式 T61 runner。

### 5. 正式 runner 必须对 PostgreSQL 缺失与 skip 失败关闭

runner 在执行前只读查询 PostgreSQL/pgvector 版本。缺少或不可达 DSN 返回 `BLOCKED_POSTGRES_UNAVAILABLE / exit 3`；pytest 返回成功但存在 skip 时 runner 改为 exit 4。正式结果为 54 passed、0 skipped。

以上发现均已关闭，T61 open findings 为 0。

## 验证结果

```text
acceptance builder --check: PASS
collect-only: 54 tests / 44 unique nodes
official PostgreSQL acceptance: 54 passed / 0 failed / 0 skipped / 1 warning
expanded adjacent regression after final fix: 291 passed / 0 failed / 1 warning
full frontend: 67 passed / 0 failed
frontend production build: PASS / 4596 modules
focused frontend after request-id lifecycle change: 19 passed
compileall / py_compile: PASS
git diff --check: PASS
missing PostgreSQL preflight: BLOCKED_POSTGRES_UNAVAILABLE / exit 3
provider_calls=0
first_data_request_sent=false
screenshots=0
```

唯一警告是既有 Starlette TestClient/httpx 弃用警告。前端构建仍有既有的 JavaScript chunk 大于 500 kB 警告，不影响 T61 恢复语义。

临时 PostgreSQL fixture 首次尝试以生产 `schema_mode=validate` 重建 store 时，按设计因 fixture 不包含完整生产 schema contract 而抛出 `PostgresSchemaNotReady`；最终测试使用 fixture 原本的幂等启动模式 `schema_mode=migrate`。该诊断失败没有被计为验收通过。

## 完整基线的真实状态

一次 2910 项 PostgreSQL 全套基线得到：

```text
2898 passed
10 failed
2 errors
3 skipped
1 warning
```

第一次全套命令在旁路 PowerShell 正则解析失败后未同步取消，第二次全套与残留进程重叠，导致 DDL deadlock、测试表清理竞态和时间阈值假失败。确认无残留 pytest 后严格串行重跑：3 个时间/cleanup 节点全部通过，4 个 DDL/重启节点全部通过，数据库时钟与主机 UTC 差约 0.17 秒。仍可独立复现的四个既有基线失败与 T60 记录一致：

1. `test_stream_latency_clock_starts_at_first_next` 的旧 perf-counter mock ticks 不足；
2. historical Local V1 publication allowlist 拒绝长期质量分支的累计改动；
3. session deletion 测试仍断言旧 latest migration `followup_decision_v1`；
4. dependency lock 文件 SHA 与 metadata 不一致。

这四项未触及 T61 修改文件，不用于替代或削弱 T61 的 54/54 no-skip 专用门禁；它们继续保留给后续运行时、T62 迁移和工具链任务处理。

## 边界与后续

- T61 是确定性工程验收，不需要真实 Provider；调用数为 0，未发送任何数据。
- 未截图、未生成 trace、未做图像工作。
- T61 PASS 不改变 Gate 2–5 已记录的 Quality blocker。
- 下一任务是 T62 数据库迁移、备份恢复和 rollback 演练；总体 Goal 保持 `active`。
