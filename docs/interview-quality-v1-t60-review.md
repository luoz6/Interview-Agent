# Interview Quality V1 — T60 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=NOT_REQUIRED_DETERMINISTIC_ENGINEERING
overall_status=PASS
scenario_count=30
manual_p0_scenario_count=4
risk_pair_coverage=111/111
matrix_tests=47 passed / 0 failed / 0 skipped
provider_calls=0
open_findings=0
```

T60 全链路组合回归矩阵已完成。矩阵不是完整笛卡尔积，而是由 6 组高风险 pair family、4 个手工 P0 状态机交叉场景和确定性 greedy set-cover 生成的 30 个可审查场景。11 个 Plan 维度的每个值都至少出现一次，声明的 111 个风险对全部覆盖；每个维度值都绑定真实 pytest 节点，执行器去重后得到 44 个节点、实际展开 47 个测试。

正式执行使用 PostgreSQL 16.14 与 pgvector 0.8.6，结果为 47 passed、0 failed、0 skipped、1 个既有 Starlette TestClient/httpx 弃用 warning。Provider 行为维度使用确定性 fake、保存响应和错误注入，没有调用真实 Provider，也没有发送任何 Provider 数据。

## 矩阵维度

| 维度 | 值 |
|---|---|
| 面试图 | legacy / durable_v1 / durable_v2 |
| 追问策略 | fixed_v1 / adaptive_v1 |
| Plan | legacy snapshot / revision v2 / edited / stale / conflict |
| 报告 job | queued / running / completed / failed |
| Report Artifact | scored / partial / unscored / degraded / legacy |
| 报告版本 | single / active+history / rescore success / rescore fail |
| 知识检索 | normal / empty / degraded |
| 回答 | strong / partial / incorrect / off-topic / empty / skipped |
| Provider | normal / timeout / invalid JSON / retry / exhausted |
| 运行异常 | SSE interruption / checkpoint recovery / lease expired / stale worker |
| Memory | disabled |

风险 pair family 为：面试图×追问策略、Plan×报告 job、Artifact×报告版本、回答×Artifact、Provider×运行异常、知识检索×Provider。完整笛卡尔积没有执行，也没有用单一 smoke 代替多轴覆盖。

## 四个手工 P0 状态机交叉场景

### P0-01 durable v2 checkpoint → immutable report commit

- durable_v2 + adaptive_v1 + revision_v2；
- checkpoint recovery 后只按持久化 Decision 路由；
- revision snapshot 不可变；
- scored Artifact 发布幂等、单调；
- Memory disabled，不构造 Principal Memory 依赖。

### P0-02 edited plan → lease reclaim → successful rescore

- 编辑问题使旧知识 binding 失效，audit 只含 hash；
- generation lease 过期后先 reset 再替换 chunks；
- partial 保留 numerator/denominator；
- rescore 成功后才创建 history 并移动 active head。

### P0-03 plan conflict + stale worker + failed rescore

- revision 冲突不覆盖 winner，并返回当前 metadata；
- stale report worker 被 lease token fencing；
- failed rescore 保留旧 active，retry 复用原 job；
- off-topic + Provider exhausted 走有界 safe-next 路径。

### P0-04 stale plan + SSE interruption + skipped/unscored

- historical revision 启动被拒绝；
- SSE timeout 保留 reconnect cursor；
- skipped 不伪造 0 分，覆盖为空时为 unscored；
- report enqueue 只建立一个 queued job 与 processing projection。

## 自动审查发现与修复

### 1. Runner 最初允许 PostgreSQL skip 以 exit 0 返回

首次矩阵运行在未设置 `POSTGRES_DSN` 时得到 41 passed / 6 skipped / 0 failed。Pytest 本身以 0 退出，存在把未运行集成节点冒充矩阵 PASS 的风险。

修复后 runner 在正式执行前连接 PostgreSQL、执行只读版本/pgvector preflight，并对任何 skip fail closed：

```text
missing POSTGRES_DSN -> BLOCKED_POSTGRES_UNAVAILABLE / exit_code=3
reachable PostgreSQL -> preflight PASS
any runtime skip with pytest exit 0 -> runner exit_code=4
official run -> 47 passed / 0 skipped
```

DSN 不进入输出，run result 只记录 PostgreSQL 与 pgvector 版本。

### 2. legacy 最后一题后的旧测试要求 `next_question`

legacy 图已在 plan exhausted 时把有效 `next_question` 决策归一为公开终态 `finish`，状态类型、pending output 和 terminal interviewer message 都使用该语义。旧测试仍要求 `next_question`，造成 T59 全仓基线中的一项历史失败。

测试现在明确验证：status=finished、current index 超出最后问题、decision action=finish、pending output 为结束文案，并且最后只存在一个终态 interviewer message。矩阵的 legacy 图代表节点也改为该终态测试。

### 3. generation chunk cleanup 混用 Python 与 PostgreSQL 时钟

扩展回归发现数据库时钟比 Python UTC 快约 4 秒。旧测试使用 `datetime.now()+1s` 选择 PostgreSQL `completed_at`，会间歇删除 0 条。生产 maintenance 实际使用数据库 `NOW() - interval` 路径。

测试现在先用数据库时钟把 completed generation 固定为两小时前，再调用生产使用的 `cleanup_completed_chunks_older_than(hours=1)`；active generation 的 chunk 仍必须保留。修复后隔离和扩展回归均通过。

以上三项发现均已关闭，T60 open findings 为 0。

## 验证结果

```text
matrix contract: 5 passed / 0 failed
matrix node collect: 47 collected
official PostgreSQL matrix: 47 passed / 0 failed / 0 skipped / 1 warning
expanded adjacent regression: 175 passed / 0 failed / 1 warning
legacy terminal isolated: 1 passed
generation cleanup isolated: 1 passed
compileall: PASS
git diff --check: PASS
provider_calls=0
screenshots=0
```

矩阵 canonical SHA-256：

```text
d9bbafdfa70e49c183d29f0be1d6496ecfbc1aef3c032d598ef49c75f27759a0
```

签入 JSON 文件 SHA-256：

```text
230bab4168d370979ca6683207d240125e95f1e6ce7ad63915f2be7ad726f565
```

## 全仓基线

当前离线全仓 pytest：

```text
2674 passed
4 failed
218 skipped
1 warning
63.51 seconds
T60-related failures=0
```

相比 T59 的 5 个失败，legacy terminal 旧断言已关闭。剩余 4 个既有失败没有被隐藏：

1. `test_stream_latency_clock_starts_at_first_next`：旧 perf-counter mock ticks 数量不足，归入后续运行时/性能验收；
2. historical Local V1 publication allowlist 拒绝质量分支累计改动；
3. PostgreSQL session deletion 测试仍要求旧 latest migration `followup_decision_v1`，归入后续迁移验收；
4. dependency lock 文件字节 SHA 与 metadata 不一致，归入后续工具链/可复现性验收。

一次显式全仓 PostgreSQL 运行因超过 240 秒被终止，没有被报告为 PASS；残留的精确 pytest 子进程已停止，Uvicorn 和其他用户进程未受影响。T60 的阻塞 PostgreSQL 组合证据来自单独的 47/47 no-skip 正式矩阵，而不是该超时运行。

## 边界

- Provider 维度由 deterministic fake、保存响应或错误注入覆盖，真实调用为 0；
- Memory 始终 disabled，证明评分/报告组合不直接依赖 Principal Memory；
- T60 Engineering PASS 不改变 Gate 2–5 的 Quality blocker；
- T61–T72 尚未完成，Goal 保持 `active`。
