# Interview Agent 长期记忆生产 Shadow、用户控制、Consumption 与渐进晋级实施计划

**Plan revision:** v0.1-draft

**Document type:** Master Implementation Plan / How-to + Reference

**Target audience:** Product、Change Owner、Operations、Privacy、Security、Fairness、Legal、Interview Quality、后端工程师、Agent 工程师、前端工程师、SRE、QA 与验收负责人

**Primary goal:** 在不把历史事实用于评分、报告或招聘判断的前提下，把已经完成的 Principal Memory 基础设施依次推进到 Production Write Shadow、Production Read Shadow 零注入、候选人自主管理、C1 Consumption 实现、Staging 验证和最高 1% 的生产 Canary，并为每个阶段建立独立审批、证据、停止和回滚闭环。

**Historical Memory RC:** `f5dce4206751775c1650a4fccbd5060625af523a`

**Historical remote verification:** `REMOTE_MANIFEST=VERIFIED`、`FILES=30`、`1682 passed`、`163 skipped`、`1 warning`

**Status at authoring:** `PRODUCTION_BUDGET_SHADOW=NOT_RUN`、`PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED`、`PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED`、`PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT`、`IMPLEMENTATION=NOT_AUTHORIZED`、`PRODUCTION_CANARY=NOT_AUTHORIZED`

> **授权边界：** 本计划是完整路线和执行契约，不是生产授权。仓库权限、计划批准、Staging PASS、Shadow PASS 或用户对主 agent 的通用授权，都不能代替 Product、Change Owner、Operations、Privacy、Security、Fairness 与必要时 Legal 的独立批准。任何 production task 只有在精确 revision、精确 deployment scope、精确窗口、精确流量和精确配置都被外部记录批准，且 change preflight 返回 `PASS` 后才能执行。

---

## 1. 阶段结论

长期记忆并不是从零开始。仓库已经具有：

- Principal identity、deployment isolation 和显式 Consent 基础；
- canonical JSON taxonomy；
- proposed-only extraction；
- Principal Fact PostgreSQL store；
- confirm、reject、supersede、expire、revoke、delete 生命周期；
- source binding、session purge、principal purge 和 tombstone replay；
- Write Shadow、Proposal Review 和 Read Shadow 零注入 Staging runner；
- 公共 Knowledge Firewall、Prompt isolation 和聚合指标；
- `consume` 配置 fail-closed；
- Consumption Draft 和风险评审。

当前缺口不是“再做一个 memory 表”，而是把长期记忆变成候选人可理解、可控制、可纠正、可关闭、可删除，并且在生产分布中可证明安全的产品能力。

固定晋级顺序为：

```text
Production Budget Shadow PASS
  → Authenticated Principal + Candidate Control Foundation
  → Principal Memory Write Production Shadow
  → Proposal Quality + Consent/Delete/Restore Production Gates
  → Principal Memory Read Production Shadow（零注入）
  → Prompt/Score/Report/Knowledge Isolation PASS
  → Consumption Spec v1 正式批准
  → C1 Consumption Repository Implementation
  → Isolated Staging Consumption PASS
  → 独立 C1 Production Approval
  → 0.1% C1 Warm-up
  → 最大 1% C1 Canary
  → Scheduled Close
  → PASS / BLOCKED / CONTINUE_OBSERVATION
```

本计划不允许跳过任何箭头。特别是：

```text
Write Shadow PASS ≠ Read Shadow 自动授权
Read Shadow PASS ≠ Consumption 实现授权
Consumption Staging PASS ≠ Production Canary 授权
C1 1% PASS ≠ 5% 或 General Availability 自动授权
```

---

## 2. 当前基线与执行时重新冻结

### 2.1 可审计历史基线

长期记忆审批材料绑定的 frozen RC 为：

```text
f5dce4206751775c1650a4fccbd5060625af523a
```

该 revision 的远端复现已经证明：

```text
depth 1 → GATE=SOURCE_REVISION_NOT_ANCESTOR
exact handoff fetch --depth 2 → manifest VERIFIED
manifest source=d857e0a091d55db76f4405669a9e699e3e3f44b6
manifest bundle SHA=b60382064513dbcdf830140bec8b0854ef59bafeb38bef4df6be279d77d96599
archive SHA=0a429559ba12f96d222abb28fb0760f175835752d7b5a032c81e6389bc8bada4
metadata SHA=de0c265a48afa9802560e2a7a86d9e759796c8a388a2ad02138241e14d162c0e
```

计划开始编写时的 Git 快照是：

```text
AUTHORING_START_HEAD=962eab5990e21d6a34821c400483be798ec5a1ab
AUTHORING_REMOTE_MASTER=6969efa119de0da33698f0de74f4fdeee502b375
ahead=1
behind=0
```

当时存在用户所有的未提交前端修改：

```text
frontend/src/pages/ReportDetailPage.jsx
frontend/src/styles/report-detail-app.css
tests/browser/report-detail-ui.spec.js
tests/test_react_frontend.py
```

这些是作者时快照，不是未来执行时必须成立的断言。Task 0 必须重新读取实际 `HEAD`、`origin/master`、ahead/behind、worktree ownership、部署 revision 和测试基线，记录为 `EXECUTION_START_HEAD`。不得 reset、restore、clean、覆盖或错误提交用户变化。

### 2.2 已完成的 Staging 证据

现有仓库证据包括：

```text
BUDGET_SHADOW_STAGING=PASS
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PRINCIPAL_PROPOSAL_QUALITY=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
PROMPT_ISOLATION=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

Staging 证据只证明 runner、契约、故障矩阵和隔离设计在受控数据中成立。它不能替代生产分布证据、候选人 UX 验证或外部审批。

### 2.3 当前阻塞项

1. Production Budget Shadow 尚未产生 `PASS` 结果；
2. 正式 authenticated Principal 产品身份尚未落地；
3. 候选人自助查看、确认、纠正、撤回、删除、导出尚未成为正式产品入口；
4. `Ignore memory for this interview` 和 `Disable memory now` 尚未成为生产 UX；
5. Write/Read Production Shadow 缺少独立 production schema、window controller、acceptance 和审批包；
6. Consumption Spec 仍是 Draft；
7. `MEMORY_LONG_TERM_MODE=consume` 仍被配置加载器硬拒绝；
8. C1 selector、renderer、visible marker、Prompt placement 和 runtime barrier 尚未实现；
9. Consumption 生产隐私、安全、公平性、Provider 与 Legal 审批尚不存在；
10. C1 生产 Canary 没有独立批准窗口。

---

## 3. 范围

### 3.1 本计划包含

- 收口现有 Production Budget Shadow 计划并保留不可变证据；
- 建立正式 authenticated Principal identity；
- 建立候选人可见、purpose-specific、default-off Consent；
- 建立候选人 Memory Center；
- 建立查看、确认、纠正、撤回、删除和导出；
- 建立 session sticky ignore 和 real-time disable barrier；
- 建立 Production Write Shadow schema、sanitizer、window、acceptance 和 runbook；
- 建立 Production Write Shadow 独立 RC、审批包、五角色审批和 change preflight；
- 在最大 1% 的明确 opt-in cohort 上运行 Write Shadow；
- 对生产 proposal 做受控人工复核和聚合质量判断；
- 执行 Consent、删除、并发、进程丢失、旧备份恢复和 tombstone replay 演练；
- 建立 Production Read Shadow 零注入 schema、window、acceptance 和 runbook；
- 在最大 1% cohort 上运行 bounded would-select，保持 Prompt 和业务输出完全不变；
- 完成 conflict、freshness、fairness、score/report/Knowledge isolation 复审；
- 把 Consumption Draft 晋升为经批准的 v1 Spec；
- 实现 C1 consumption 配置、contracts、selector、renderer、Prompt placement 和 runtime barrier；
- 实现候选人 visible indicator、explanation 和实时关闭；
- 实现低基数聚合指标、自动停止和 rollback；
- 执行 unit、integration、live PostgreSQL、browser、accessibility、security、privacy 和 fairness 测试；
- 运行 Isolated Staging Consumption；
- 为 C1 创建独立 RC、审批包、外部批准、preflight、0.1% warm-up 和最大 1% canary；
- 生成 pre-approval 与 post-observation 两条不可混淆的证据链；
- 在 C1 结束时形成 PASS、BLOCKED 或 CONTINUE_OBSERVATION；
- 为 5% 以上扩容起草下一计划的输入，但不自动授权扩容。

### 3.2 本计划明确排除

- 在 Production Budget Shadow PASS 前启动任何 Principal production Shadow；
- 使用 trusted-local identity 作为 production consumption identity；
- 通过 email、姓名、电话、IP、User-Agent、设备指纹、简历、embedding 相似度或模型输出合并 Principal；
- 默认开启 Consent；
- 把 memory Consent 与参加面试、获得报告或获得完整功能捆绑；
- 自动确认或自动激活模型 proposal；
- 把 unconfirmed、revoked、expired、deleted、conflicting 或 stale fact 注入 Prompt；
- 把候选人回答、简历、报告、项目名、公司名或自由文本保存为 Principal fact；
- 在 C1 使用 `confirmed_skill`；
- 使用历史事实计算或改变 score、difficulty、evidence、report、rank、recommendation 或 hiring decision；
- 将 Principal facts 写入公共 Knowledge、corpus、embedding 或共享向量检索；
- cross-Principal similarity、nearest-neighbor、collaborative filtering 或自动 identity merge；
- 在 candidate 不可见的情况下进行隐式 personalization；
- 在用户 decline、ignore、disable、correct、revoke、delete 或 export 后惩罚用户；
- 在 `Disable memory now` 后继续新的 proposal、selection 或 injection；
- 在 production window 内热修代码、切换 revision、改变 schema 或复用旧批准；
- 把 external approval record、approver reference、ticket digest、deployment digest、DSN、secret 或 candidate locator 提交进 Git；
- 把 PENDING evidence 改写为 production PASS；
- 从 C1 1% 自动扩到 5%、25%、50% 或 100%；
- 把本计划视为 Production Budget Shadow、Write Shadow、Read Shadow 或 Consumption Canary 的外部批准。

---

## 4. 固定决策

### Decision 1：原始 Session 数据始终是权威来源

Principal Memory 是派生、可撤回、非权威的历史偏好。当前 session 的明确声明、当前 interview plan 和当前 evidence 永远优先。历史事实冲突时排除历史事实，不猜测赢家。

### Decision 2：Production Shadow 严格串行

Budget → Write → Read。每个阶段必须关闭配置、完成 acceptance、发布 post-observation evidence，并取得下一阶段的新批准。不得共用批准窗口。

### Decision 3：正式身份采用 OIDC issuer/subject 绑定

Production Principal 使用经过批准的 OIDC `issuer + subject`。Memory store 不保存 email、姓名或 raw subject；使用 deployment-scoped、versioned HMAC 映射为 opaque Principal ID。账户恢复、subject 迁移和 issuer 变更必须经过显式绑定流程，禁止自动猜测。

### Decision 4：Consent purpose 分离且 default off

`proposal_write`、`fact_storage`、`read_shadow` 和 `consumption_c1` 是不同 purpose。前一 purpose 的同意不隐含后一 purpose。每次操作重新读取当前 Consent、policy version、identity、session ignore、disable state、fact/source status 和 deletion state。

### Decision 5：模型只能创建 proposed facts

模型、worker、重放任务和管理员批处理都不能把 proposal 变为 active。只有 authenticated candidate 的显式 confirm/correct 行为可以创建 user-confirmed active fact。

### Decision 6：候选人拥有完整控制面

正式 consumption 前必须具备 view、confirm、correct、revoke、delete、export、ignore-for-session 和 disable-now。拒绝或关闭 memory 不得减少功能、缩短面试、影响评分或产生负面标签。

### Decision 7：Write Shadow 不读取，Read Shadow 不注入

Write Shadow 只生成 proposed facts；Read Shadow 只计算 would-select。Read Shadow 的 Provider Context、Prompt、messages、question、score、report、evidence 和 API output 必须与 control 完全相等。

### Decision 8：C1 allowlist 是封闭集合

C1 只允许：

```text
interview_language
accessibility_preference
learning_goal
target_role_family
```

`confirmed_skill` 明确排除。任何新增 fact type 都需要新的 fairness analysis、Spec 版本和批准。

### Decision 9：C1 只允许 follow-up context assembly

唯一允许 operation 是 `interview.followup.context_assembly`。Prep、answer evaluation、scoring、report、PDF、evidence selection、Knowledge retrieval 和 agent control 永远不消费 Principal Memory。

### Decision 10：C1 bounds 固定且 fail-closed

最多 3 个 facts、最多 120 tokens。先 deterministic select，再完整 render；禁止截断结构化 fact。身份、Consent、taxonomy、conflict、freshness 或 deletion 不确定时，对 memory fail-closed，对 deterministic interview fail-open。

### Decision 11：Prompt block 必须可见且有固定 marker

Block 标题精确为 `Non-authoritative historical preference`，位于 system policy 和当前 plan/evidence 之后、current candidate message 之前。Block 必须声明当前 session 优先，并禁止评分、报告、招聘判断和能力断言。

### Decision 12：实时 disable 使用 context-assembly barrier

`Disable memory now` 在下一次 context assembly 前且最长 60 秒内生效。已经发送给 Provider 的 in-flight request 不能保证撤回，UI 必须说明该边界；完成中的请求不得调度新的 memory operation。

### Decision 13：评分、报告和公共知识使用结构隔离加相等性测试

不依赖 Prompt 文案自律。Scoring、report 和 Knowledge 路径不注入 Principal Memory dependency，并用 deterministic equality、source audit 和 adversarial test 证明。

### Decision 14：常规观察只保存低基数聚合

生产 observation 不保存 Principal、Session、Fact、Question、Message、Artifact locator，不保存 fact value、Prompt、answer、resume、report、source excerpt、digest 或 external approval binding。低于隐私阈值的 bucket 合并、延迟或抑制。

### Decision 15：删除真相由 online state 与 operator tombstone 共同维持

Online delete 删除 facts、proposals、effects、bindings 和 derived refs；operator tombstone 保证旧备份恢复后再次删除。任何恢复副本在 tombstone replay 完成前不得接收流量。

### Decision 16：每个生产阶段一次只改变一个 memory axis

Write window 只打开 Write Shadow；Read window 只打开 Read Shadow；C1 只打开 consumption C1。Budget enforcement、compression consumption、Question Memory consumption 和其他 Principal modes 保持 disabled。

### Decision 17：Hard stop 不等待统计显著性

Cross-Principal、no-Consent、revoked/deleted fact、Prompt mutation、score/report difference、Knowledge mutation、hidden personalization、disable SLA breach 或 private artifact hit 都立即停止。Error/latency 统计阈值只适用于样本充分后的性能判断。

### Decision 18：窗口结束先关闭再判定

Scheduled end、manual stop 或 hard stop 都先恢复 disabled，验证新 operation 为 0，再生成结果。证据不足输出 CONTINUE_OBSERVATION，并需要新批准窗口。

### Decision 19：Consumption 实现批准与生产 Canary 批准分离

Spec v1 批准只允许实现。Staging PASS 后还需要新的 exact-revision C1 production approval。任何 Shadow approval 都不能复用。

### Decision 20：C1 1% 是本计划生产上限

即使 C1 PASS，本计划也不授权 5% 以上流量。扩容需要新的长期观察、candidate research、fairness review、RC、审批包和独立计划。

---

## 5. 目标架构

### 5.1 身份与授权链

```text
OIDC Provider
  → verified issuer/subject
  → deployment-scoped HMAC Principal ID
  → current account/session binding
  → purpose/version Consent lookup
  → ignore/disable barrier
  → fact/source/deletion eligibility
  → bounded selection
  → visible C1 rendering
  → follow-up context assembly only
```

### 5.2 写入链

```text
authoritative completed session source
  → opaque proposal event
  → operation-time identity + proposal_write Consent
  → version/source/taxonomy verification
  → proposed fact
  → candidate-visible review
  → explicit confirm/correct
  → active user-confirmed fact
```

模型输出永远停在 `proposed`。

### 5.3 读取与消费链

```text
current follow-up request
  → authenticated Principal
  → current consumption Consent
  → sticky session ignore / real-time disable
  → bounded active fact query
  → conflict/freshness/source checks
  → operation allowlist
  → deterministic max-3 / max-120-token selection
  → visible non-authoritative block
  → follow-up context assembly
```

### 5.4 权威顺序

```text
system policy
  > current interview plan
  > current-session explicit candidate statement
  > current-session evidence
  > user-confirmed Principal preference
  > model-proposed Principal fact
```

`model-proposed` 永远不能进入 consumption。

### 5.5 失败路径

```text
memory identity/Consent/store/selector/renderer/metrics failure
  → no memory block
  → deterministic interview continues
  → stable aggregate gate/count
```

---

## 6. 安全配置矩阵

| 配置 | Disabled | Write Production Shadow | Read Production Shadow | C1 Staging | C1 Production Canary |
|---|---|---|---|---|---|
| `MEMORY_BUDGET_MODE` | `disabled` | `disabled` | `disabled` | `disabled` | `disabled` |
| `MEMORY_COMPRESSION_MODE` | `disabled` | `disabled` | `disabled` | `disabled` | `disabled` |
| `MEMORY_LONG_TERM_MODE` | `disabled` | `write_shadow` | `read_shadow` | `consume_c1` | `consume_c1` |
| `MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED` | `false` | `true` | `true` | `false` | `false` |
| `MEMORY_LONG_TERM_READ_SHADOW_ENABLED` | `false` | `false` | `true` | `false` | `false` |
| `MEMORY_LONG_TERM_CONSUMPTION_C1_ENABLED` | `false` | `false` | `false` | `true` | `true` |
| `MEMORY_LONG_TERM_CONSUMPTION_TRAFFIC_PERCENT` | `0` | `0` | `0` | `100` synthetic | `0.1` 至 `1.0` |
| `MEMORY_LONG_TERM_MAX_CONSUMED_FACTS` | `3` | `3` | `3` | `3` | `3` |
| `MEMORY_LONG_TERM_MAX_CONSUMED_TOKENS` | `120` | `120` | `120` | `120` | `120` |
| `MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED` | `false` | `false` | `false` | `false` | `false` |
| `MEMORY_AUTHENTICATED_SELF_SERVICE_ENABLED` | `false` | cohort-only | cohort-only | `true` | `true` |
| `MEMORY_CONSUMPTION_KILL_SWITCH` | `true` | `true` | `true` | `false` during test | `false` only inside approved window |

`consume_c1` 在 Task 27 前仍是非法值，配置加载器必须继续拒绝 `consume` 和 `consume_c1`。Task 27 只能在 Consumption Spec v1 和 implementation authorization 都存在后添加该值，并保持默认 disabled。

禁止同时配置 canonical `MEMORY_*` 和 legacy `CONTEXT_*` 冲突值。任何冲突都是 hard stop。

---

## 7. 任务依赖图

```text
Task 0  基线、所有权和生产边界
  └── Task 1  Production Budget Shadow 外部执行
        └── Task 2  Budget post-observation evidence

Task 0
  └── Task 3  Authenticated Principal contract
        └── Task 4  Principal mapping/recovery
              ├── Task 5  Consent ledger v2
              │     ├── Task 6  Authenticated self-service API
              │     │     ├── Task 7  Candidate Memory Center
              │     │     ├── Task 8  Ignore/Disable barrier
              │     │     └── Task 9  Correct/Revoke/Delete/Export
              │     └── Task 10 Identity/Consent/UX acceptance
              └──────────────────────────────────────────────┐
                                                             │
Task 2 + Task 5 + Task 10                                    │
  └── Task 11 Write production contracts/tooling             │
        └── Task 12 Write RC/evidence bundle                  │
              └── Task 13 Write external approval/preflight   │
                    └── Task 14 Write 0.1% warm-up            │
                          └── Task 15 Write observation       │
                                ├── Task 16 Proposal review   │
                                └── Task 17 Lifecycle drills  │
                                      └── Task 18 Write close │
                                                             │
Task 18 + Task 10                                             │
  └── Task 19 Read production contracts/tooling               │
        └── Task 20 Read RC/approval                           │
              └── Task 21 Read 0.1% warm-up                   │
                    └── Task 22 Read observation              │
                          ├── Task 23 Zero-injection audit     │
                          └── Task 24 Conflict/fairness review │
                                └── Task 25 Read close         │
                                                             │
Task 7 + Task 8 + Task 9 + Task 10 + Task 25                  │
  └── Task 26 Consumption Spec v1                             │
        └── Task 27 Config/contracts/migration                │
              └── Task 28 C1 selector                         │
                    └── Task 29 Renderer/marker               │
                          └── Task 30 Follow-up integration    │
                                ├── Task 31 UX integration    │
                                ├── Task 32 Isolation         │
                                └── Task 33 Metrics/kill switch
                                      └── Task 34 Full matrix
                                            └── Task 35 Staging
                                                  └── Task 36 C1 RC/approval
                                                        └── Task 37 0.1% warm-up
                                                              └── Task 38 1% canary
                                                                    └── Task 39 closure
```

允许并行的只有：

- Tasks 3-10 的 product identity/control track 可以在等待 Task 1 外部窗口时进行；
- Task 16 的 reviewer training 和 synthetic calibration 可以在 Task 15 前准备，但不能查看 production proposal；
- Task 17 的合成故障 fixture 可以提前准备，真实 production drill 必须等待 Task 15；
- Tasks 23-24 的 test fixture 可以并行准备，生产结论必须等待 Task 22；
- Tasks 31-33 可以在 Task 30 contract 固定后并行编辑不重叠文件。

禁止并行编辑：

- Principal migration registry；
- `memory_config.py` 的 mode validation；
- `runtime.py` 的 singleton/dependency graph；
- `routes.py` 的 authenticated/trusted-local boundary；
- follow-up context assembly 的 Prompt order。

---

## 8. 统一证据与验证约定

### 8.1 证据分层

1. **Repository evidence：** public revision、schema version、stable gates、aggregate counts；
2. **External approval evidence：** approver、ticket、record SHA、scope SHA、window、traffic；
3. **Operational raw evidence：** 受信 metrics backend 中的短期聚合源；
4. **Candidate data：** 业务存储中的受控数据，永不复制进计划、Git 或审批 ZIP；
5. **Operator tombstone：** 独立控制面中的删除真相，不与应用备份一起回滚。

### 8.2 生产 observation 共同字段

允许：

- public Git revision；
- phase；
- duration、traffic、sample count；
- low-cardinality taxonomy category counts；
- proposal/selection/outcome counts；
- stable hard-stop count；
- error/latency aggregate；
- boolean revision/scope/config/approval/current-window/rollback results；
- coarse language/path buckets；
- configuration restored boolean。

禁止：

- Principal、Session、Fact、Question、Message、Artifact ID；
- normalized fact value；
- source digest/excerpt；
- Prompt、answer、resume、report、provider payload；
- OIDC issuer/subject、email、name；
- DSN、host、schema、prefix、secret；
- record、ticket、approver 或 deployment digest；
- exact low-volume timestamp sequence；
- free-text reviewer note。

### 8.3 三态结果

所有 production window 只能输出：

```text
PASS
BLOCKED
CONTINUE_OBSERVATION
```

任何失败不得同时输出 READY 或 PASS。CONTINUE 必须先关闭配置，并要求新批准窗口。

### 8.4 测试层次

- pure unit；
- contract/source audit；
- in-memory concurrency；
- isolated live PostgreSQL；
- API/auth/CSRF；
- React/browser/accessibility；
- adversarial Prompt/privacy/security；
- deterministic score/report equality；
- Staging synthetic matrix；
- production aggregate acceptance；
- backup restore/tombstone replay；
- full Python/browser regression；
- remote clone/evidence bundle reproduction。

---

## 9. Hard Stop Gates

### 9.1 Write Production Shadow

```text
WRITE_CROSS_PRINCIPAL
WRITE_WITHOUT_CONSENT
WRITE_AFTER_DISABLE
WRITE_DURING_DELETE
WRITE_SOURCE_MISMATCH
WRITE_FREE_TEXT_FACT
WRITE_INVALID_TAXONOMY
WRITE_INFERRED_ACCESSIBILITY
WRITE_AUTOMATIC_ACTIVE
WRITE_PUBLIC_KNOWLEDGE_MUTATION
WRITE_INTERVIEW_BEHAVIOR_CHANGED
WRITE_PRIVATE_ARTIFACT_HIT
WRITE_TRAFFIC_CAP_EXCEEDED
WRITE_APPROVAL_NOT_CURRENT
WRITE_REVISION_SCOPE_CONFIG_MISMATCH
WRITE_METRICS_INCOMPLETE
```

### 9.2 Read Production Shadow

```text
READ_CROSS_PRINCIPAL
READ_WITHOUT_CONSENT
READ_REVOKED_EXPIRED_DELETED
READ_UNCONFIRMED_FACT
READ_CONFLICT_SELECTED
READ_STALE_SOURCE
READ_FACT_OR_TOKEN_CAP_EXCEEDED
READ_PROMPT_CHANGED
READ_PROVIDER_CONTEXT_CHANGED
READ_QUESTION_SCORE_REPORT_CHANGED
READ_PRIVATE_ARTIFACT_HIT
READ_TRAFFIC_CAP_EXCEEDED
READ_APPROVAL_NOT_CURRENT
READ_REVISION_SCOPE_CONFIG_MISMATCH
READ_METRICS_INCOMPLETE
```

### 9.3 C1 Consumption

```text
CONSUME_CROSS_PRINCIPAL
CONSUME_WITHOUT_CONSENT
CONSUME_AFTER_IGNORE_OR_DISABLE
CONSUME_REVOKED_EXPIRED_DELETED
CONSUME_UNCONFIRMED_OR_CONFLICTING
CONSUME_NON_C1_FACT
CONSUME_OUTSIDE_FOLLOWUP_CONTEXT
CONSUME_FACT_OR_TOKEN_CAP_EXCEEDED
CONSUME_MARKER_MISSING
CONSUME_HIDDEN_PERSONALIZATION
CONSUME_CURRENT_EVIDENCE_OVERRIDDEN
CONSUME_SCORE_OR_REPORT_DIFFERENCE
CONSUME_PUBLIC_KNOWLEDGE_MUTATION
CONSUME_DISABLE_DELETE_SLA_BREACH
CONSUME_BACKUP_REPLAY_RESIDUE
CONSUME_PRIVATE_ARTIFACT_HIT
CONSUME_TRAFFIC_CAP_EXCEEDED
CONSUME_APPROVAL_NOT_CURRENT
CONSUME_REVISION_SCOPE_CONFIG_MISMATCH
CONSUME_METRICS_INCOMPLETE
```

样本达到 200 后，以下也是统计 hard stop：

```text
observed_error_rate - baseline_error_rate > 0.005
observed_p95_latency_ms > baseline_p95_latency_ms * 1.20
```

---

## Task 0：冻结执行基线、所有权和安全边界

**Goal:** 形成不覆盖用户工作、可复现且不会误用历史批准的执行起点。

**Files:**

- Create: `tests/test_long_term_memory_production_plan.py`
- Review: 本计划、Memory Spec、四份前序计划、Consumption Draft/Risk Review
- No production writes

**Steps:**

1. 记录 `EXECUTION_START_HEAD`、`origin/master`、ahead/behind、worktree status；
2. 为每个 dirty path 标记 user-owned、plan-owned 或 shared；
3. 验证 `f5dce42` 只作为历史 evidence，不作为未来 Write/Read/C1 自动批准；
4. 验证所有 memory production modes 默认 disabled；
5. 添加本计划契约测试，固定任务 0-39、Decision 1-20、Hard Stops、C1 allowlist、DoD 和排除项；
6. 运行 plan test 和 `git diff --check`；
7. 只 stage 精确 plan/test 路径。

**Exit gate:** baseline inventory 完整；用户文件未被修改或暂存；production 状态仍全部未授权。

**Suggested commit:** `docs(memory): plan long-term memory production evolution`

---

## Task 1：完成 Production Budget Shadow 外部窗口

**Goal:** 取得长期记忆 production Shadow 的唯一前置运行证据。

**Dependencies:** Task 0；现有 Production Budget Shadow Plan Tasks 7-13。

**Repository writes:** 仅经过 Privacy/Security 审核的 post-observation aggregate evidence。

**Steps:**

1. 上传 `f5dce42` PENDING bundle 到独立 change system，并把 phase 固定为 `BUDGET_SHADOW_ONLY`；
2. 取得 change_owner、operations、privacy、security、fairness 独立批准；
3. 运行 revision/scope/window-bound change preflight；
4. 执行 0.1% warm-up：至少 30 分钟和 20 follow-ups；
5. 提升到批准上限但不超过 1%；
6. 总窗口至少 24 小时和 200 follow-ups；
7. scheduled end 前恢复 Budget Shadow disabled；
8. 运行 production acceptance；
9. 输出 PASS、BLOCKED 或 CONTINUE。

**Exit gate:** 只有 `PRODUCTION_BUDGET_SHADOW=PASS` 才允许 Task 2 形成晋级输入。BLOCKED 或 CONTINUE 停止本计划的 production branches。

---

## Task 2：发布 Budget post-observation evidence 并冻结下一审批输入

**Goal:** 保留 pre-approval 与 post-observation 两条不可混淆的证据链。

**Files:**

- Create only after audit: `docs/memory-production-budget-shadow-observation.json`
- Create only after audit: `docs/memory-production-budget-shadow-acceptance.md`
- Modify: production evidence manifest tooling/tests as required

**Steps:**

1. 从受信指标系统导出 allowlisted aggregate；
2. 运行 sanitizer 和 privacy sentinel scan；
3. 验证不包含 external digest、locator 或 candidate content；
4. 记录 window closed、configuration restored 和 hard-stop count；
5. 从新 evidence commit 创建干净 RC 并做 remote reproduction；
6. 明确继续输出 `PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED`；
7. 仅把 Budget PASS 作为 Write plan 的申请输入。

**Tests:** production Budget observation/acceptance/evidence manifest suites + full regression。

**Exit gate:** `BUDGET_POST_OBSERVATION_EVIDENCE=VERIFIED`，但 Write 仍未授权。

---

## Task 3：定义 Authenticated Principal OIDC Contract

**Goal:** 替换 trusted-local/inferred identity，建立 production-safe Principal 根身份。

**Files:**

- Modify: `app/ports/principal_identity.py`
- Create: `app/services/authenticated_principal.py`
- Create: `docs/principal-memory-authenticated-identity-contract.md`
- Create: `tests/test_authenticated_principal.py`

**Steps:**

1. 定义 verified OIDC issuer/subject input；
2. 使用 deployment-scoped、key-versioned HMAC 派生 opaque Principal ID；
3. 禁止 raw subject、email、name 落入 memory store、metrics 或 logs；
4. 定义 issuer migration、subject migration、account recovery 和 key rotation；
5. 对 ambiguous/missing/unverified identity fail-closed；
6. 添加 cross-deployment、cross-issuer、case/Unicode、collision 和 recovery tests；
7. 保留 trusted-local route 仅用于测试，production app 不挂载。

**Tests:** `tests/test_authenticated_principal.py tests/test_principal_identity.py tests/test_principal_memory_isolation.py`

**Exit gate:** production Principal 只能来自 approved authenticated boundary。

**Suggested commit:** `feat(memory): define authenticated principal identity`

---

## Task 4：实现 Principal Mapping、Key Rotation 与 Account Recovery 防护

**Goal:** 持久化 opaque mapping，并阻止账户恢复导致 memory 错绑。

**Dependencies:** Task 3。

**Files:**

- Modify: PostgreSQL migration registry
- Create: `app/services/postgres_principal_identity.py`
- Create: `tests/test_postgres_principal_identity.py`
- Create: `docs/principal-memory-account-recovery-runbook.md`

**Steps:**

1. 新 migration 使用 deployment、issuer hash、subject HMAC、key version 和 status；
2. unique constraint 防止同一 identity 重复映射；
3. recovery 不自动继承旧 memory；
4. 显式 rebind 需要 authenticated old/new proof 和审计批准；
5. key rotation 使用双读、单写、可回滚窗口；
6. 删除 principal 时删除 mapping 或不可逆 tombstone；
7. live PostgreSQL 测试并发创建、rotation、recovery、delete 和 rollback。

**Exit gate:** identity collision/recovery threat tests 全绿，无 PII 字段。

**Suggested commit:** `feat(memory): persist opaque principal mappings`

---

## Task 5：升级 Purpose-specific Consent Ledger v2

**Goal:** 支持 production Write、Read Shadow 和 C1 的独立、版本化、实时 Consent。

**Dependencies:** Task 4。

**Files:**

- Modify: `app/ports/principal_memory_consent.py`
- Modify: `app/services/principal_memory_consent.py`
- Modify: `app/services/postgres_principal_memory_consent.py`
- Create: `tests/test_principal_memory_consent_v2.py`

**Steps:**

1. purposes 固定为 proposal_write、fact_storage、read_shadow、consumption_c1；
2. record 包含 policy version、decision、effective/revoked time 和 authority；
3. default off；
4. purpose 不相互继承；
5. 操作时读取，不使用 session-start 缓存；
6. revoke 立即阻止新 operation 并调度 purge/eligibility update；
7. policy version 变更需要重新同意；
8. 添加 concurrent revoke/select/enqueue tests。

**Exit gate:** Consent TOCTOU、purpose confusion 和 version downgrade tests 全绿。

**Suggested commit:** `feat(memory): version principal consent purposes`

---

## Task 6：建立 Authenticated Self-service Memory API

**Goal:** 提供候选人本人可用、无内部 locator 泄漏的正式 API。

**Dependencies:** Tasks 4-5。

**Files:**

- Modify: `app/api/routes.py`
- Create: `app/api/principal_memory_routes.py`
- Create: `app/services/principal_memory_self_service.py`
- Create: `tests/test_principal_memory_self_service_api.py`

**Steps:**

1. 所有 route 依赖 authenticated Principal 和 CSRF/re-auth policy；
2. list 只返回 canonical category/value、status、authority、coarse source status、confirmed/expiry time；
3. confirm/correct/revoke/delete/export 使用 idempotency key；
4. fact handle 是 Principal-scoped opaque handle，不暴露内部 fact_id；
5. cross-Principal handle 返回统一 not-found；
6. rate limit、audit event 和 safe error body；
7. trusted-local API 保持单独 disabled gate；
8. 添加 authorization、CSRF、enumeration 和 replay tests。

**Exit gate:** authenticated candidate 只能访问自己的 records；拒绝响应无存在性 side channel。

**Suggested commit:** `feat(memory): add authenticated memory self service`

---

## Task 7：实现 Candidate Memory Center

**Goal:** 让候选人理解系统记住了什么、为什么、如何控制。

**Dependencies:** Task 6。

**Files:**

- Create: `frontend/src/pages/MemoryCenterPage.jsx`
- Create: `frontend/src/styles/memory-center-app.css`
- Modify: frontend router/navigation
- Create: `tests/browser/memory-center-ui.spec.js`
- Modify: `tests/test_react_frontend.py`

**Steps:**

1. default-off Consent 分 purpose 展示；
2. 展示 proposed、active、revoked、expired 状态；
3. 提供 confirm、correct、revoke、delete、export；
4. 显示“不用于评分、报告或招聘判断”；
5. decline 与 delete 不使用羞辱、阻碍或降级文案；
6. keyboard、screen reader、focus、contrast 和 mobile tests；
7. 不展示隐藏模型 reasoning、内部 digest 或 source excerpt；
8. destructive action 使用清楚确认和可验证 completion state。

**Exit gate:** accessibility audit、plain-language review 和 no-dark-pattern review PASS。

**Suggested commit:** `feat(frontend): add candidate memory center`

---

## Task 8：实现 Ignore-for-session 与 Disable-now Barrier

**Goal:** 在面试开始前和进行中提供可预测的实时关闭。

**Dependencies:** Tasks 5-7。

**Files:**

- Create: `app/services/principal_memory_runtime_control.py`
- Modify: interview session state contract/migration
- Modify: `frontend/src/pages/InterviewPage.jsx`
- Create: `tests/test_principal_memory_runtime_control.py`
- Create: `tests/browser/principal-memory-disable-ui.spec.js`

**Steps:**

1. ignore 在首个 context assembly 前记录并对 session sticky；
2. disable 写入 account control state，阻止新 proposal/read/consume；
3. context assembly 每次重新读取 barrier；
4. 最长 60 秒和 next-assembly 双重 SLO；
5. in-flight Provider request 显式不可撤回边界；
6. completion 不得调度下一个 memory effect；
7. 并发 disable/select、disable/enqueue、disable/retry tests；
8. no-penalty score/report equality tests。

**Exit gate:** disable 后 zero new memory operation，SLO 和 UI disclosure PASS。

**Suggested commit:** `feat(memory): enforce real-time memory disable barrier`

---

## Task 9：完善 Correct、Revoke、Delete 与 Export 生命周期

**Goal:** 让用户权利覆盖在线数据、派生引用和恢复副本。

**Dependencies:** Tasks 6-8。

**Files:**

- Modify: `app/services/principal_memory_lifecycle.py`
- Modify: `app/services/principal_memory_deletion.py`
- Create: `app/services/principal_memory_export.py`
- Create: `tests/test_principal_memory_export.py`
- Extend: lifecycle/deletion/restore tests

**Steps:**

1. correct 创建新 confirmed fact 并立即 supersede predecessor；
2. revoke 下一 assembly 起不可选；
3. delete 覆盖 fact、proposal、effect、binding、owner ref、cache 和 derived ref；
4. online delete SLO 24 小时；
5. export SLO 24 小时，machine-readable 且仅含本 Principal；
6. tombstone replay 阻止 backup resurrection；
7. export 与 delete 并发、重复请求、进程丢失和恢复 tests；
8. metrics 只记录低基数完成/失败计数。

**Exit gate:** zero cross-Principal export、zero deletion residue、restore replay PASS。

**Suggested commit:** `feat(memory): complete principal memory rights lifecycle`

---

## Task 10：Identity、Consent、UX、Privacy、Security 与 Fairness Acceptance

**Goal:** 在任何 production Principal Shadow 前验收产品控制面。

**Dependencies:** Tasks 3-9。

**Files:**

- Create: `scripts/principal_memory_control_plane_acceptance.py`
- Create: `docs/principal-memory-control-plane-acceptance.md`
- Create: `tests/test_principal_memory_control_plane_acceptance.py`

**Steps:**

1. 组合 auth、Consent、self-service、disable、delete、export gates；
2. 执行 account takeover/recovery threat review；
3. 执行 Consent comprehension/accessibility review；
4. 执行 no-penalty equality；
5. 检查 protected-class proxy 和 accessibility direct-declaration；
6. artifact scan 拒绝 PII、locator、digest 和 candidate content；
7. 失败输出稳定 gate，禁止 READY；
8. 成功只授权申请 Write Shadow，不授权实际窗口。

**Success output:**

```text
PRINCIPAL_MEMORY_CONTROL_PLANE=PASS
AUTHENTICATED_PRINCIPAL=PASS
CONSENT_USER_RIGHTS=PASS
WRITE_PRODUCTION_APPROVAL_REQUIRED
READ_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

**Suggested commit:** `test(memory): accept principal control plane`

---

## Task 11：实现 Production Write Shadow Contracts、Sanitizer、Window 与 Acceptance

**Goal:** 为真实生产聚合结果建立离线、可审计、三态工具。

**Dependencies:** Tasks 2、5、10。

**Files:**

- Create: `scripts/principal_memory_production_write_observation.py`
- Create: `scripts/principal_memory_production_write_window.py`
- Create: `scripts/principal_memory_production_write_acceptance.py`
- Create: corresponding tests/fixtures/contracts/runbook

**Steps:**

1. 定义 aggregate input 和 sanitized output schema；
2. exact allowlist，unknown field fail-closed；
3. 状态机包含 PENDING_APPROVAL、PREFLIGHT_VERIFIED、WARM_UP、OBSERVING、STOPPING、CLOSED；
4. hard stop 优先于 CONTINUE/PASS；
5. 输入输出路径必须在仓库外；
6. 工具不连接生产 DB、HTTP、Provider、deployment 或 approval system；
7. rollback/config restored 未证明时输出 NOT_VERIFIED；
8. fixtures 覆盖 pass、hard stop、insufficient evidence 和 privacy rejection。

**Exit gate:** offline tooling focused suite PASS；production 仍未运行。

**Suggested commit:** `feat(memory): add production write shadow evidence gates`

---

## Task 12：生成 Write Shadow RC、Manifest 与 PENDING Bundle

**Goal:** 把 Write tooling、control plane、migration、tests 和 runbook 绑定到不可变 revision。

**Dependencies:** Task 11。

**Steps:**

1. 从 clean detached worktree 跑 focused、live PG、browser 和 full regression；
2. 生成 readiness evidence；
3. 扩展固定 evidence allowlist；
4. 生成 canonical manifest；
5. remote depth-1 fail-closed、exact-revision depth-2 verify；
6. 生成 PENDING ZIP、metadata、sidecar；
7. 解压验证精确文件集合和敏感模式；
8. metadata 固定 phase `PRINCIPAL_WRITE_SHADOW_ONLY`；
9. 明确 Read/Consumption 仍未授权。

**Exit gate:** `PRINCIPAL_WRITE_SHADOW_TOOLING=READY_FOR_REVIEW`。

---

## Task 13：取得 Write Shadow 五角色批准并运行 Change Preflight

**Goal:** 获得 exact revision/scope/window/traffic 的独立 production Write 授权。

**Dependencies:** Task 12。

**Repository writes:** None。

**Steps:**

1. 上传 PENDING bundle；
2. 五角色独立批准；
3. 记录 opt-in cohort、max 1%、metrics、rollback owner 和 incident channel；
4. expected record SHA 与 scope SHA 来自独立系统；
5. 运行 Write-specific preflight；
6. 验证只允许 Write axis；
7. 配置仍未改变。

**Pass output:**

```text
PRINCIPAL_WRITE_SHADOW_CHANGE_PREFLIGHT=PASS
EXTERNAL_APPROVAL_RECORD=VERIFIED
REQUESTED_PHASE=PRINCIPAL_WRITE_SHADOW_ONLY
CONFIGURATION_CHANGED=false
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

---

## Task 14：运行 Write Shadow 0.1% Warm-up

**Goal:** 最小流量验证 identity、Consent、proposal 和 stop path。

**Dependencies:** Task 13。

**Steps:**

1. 部署精确 approved revision；
2. 只设置 mode=write_shadow 和 explicit write gate；
3. sticky assignment `min(0.1%, approved cap)`；
4. 只包含明确 opt-in、authenticated cohort；
5. 至少 30 分钟和 20 eligible sessions；
6. 在第 1、5、20 个 proposal 和每 15 分钟检查；
7. hard invariants、metrics completeness、deterministic interview health 全绿才 ramp；
8. 不足则保持 warm-up 或关闭为 CONTINUE。

**Exit gate:** `WRITE_WARM_UP=PASS`，不等于总 Write PASS。

---

## Task 15：运行 Write Shadow Approved-cap Observation

**Goal:** 在最大 1% cohort 上取得生产 proposal 分布证据。

**Dependencies:** Task 14。

**Steps:**

1. 提升到 approved cap 且不超过 1%；
2. 至少 24 小时和 200 eligible sessions；
3. 所有 facts 保持 proposed；
4. operation-time recheck identity/Consent/source/deletion/taxonomy；
5. 检查 replay、concurrency、worker loss 和 duplicate；
6. 监控 hard stops、P95 latency、error delta 和 data completeness；
7. scheduled end 前恢复 disabled；
8. 导出 sanitized aggregate。

**Exit gate:** window CLOSED；配置恢复；等待 Tasks 16-17 复核。

---

## Task 16：执行 Production Proposal 人工复核和质量门禁

**Goal:** 证明 proposed facts 的语义质量、直接声明边界和 taxonomy 正确性。

**Dependencies:** Task 15。

**Steps:**

1. reviewer 在受控环境访问最小必要 source；
2. review sample 至少 300 或全部样本（取较小的完整集合规则由审批固定）；
3. 标签固定为 correct、unsupported、over_generalized、wrong_taxonomy、stale_source、conflict、privacy_sensitive、not_useful、duplicate、review_unavailable；
4. 只有 correct 可以成为未来候选人 review 输入；
5. privacy_sensitive=0；unsupported<2%；stale_source_accepted=0；
6. reviewer unavailable 不计为 correct；
7. Git artifact 只保存 label counts/rates；
8. 任何 raw candidate text 留在受控系统并按 retention 删除。

**Exit gate:** `PRODUCTION_PROPOSAL_QUALITY=PASS` 或 BLOCKED/CONTINUE。

---

## Task 17：执行 Write Consent、Delete、Restore 与故障演练

**Goal:** 在 production-like 边界证明撤回和删除传播。

**Dependencies:** Task 15。

**Steps:**

1. opt-in → proposal → revoke before worker；
2. enqueue → delete session → worker replay；
3. proposal complete → owner binding 前进程丢失；
4. concurrent workers；
5. principal purge；
6. restore approved old backup；
7. replay operator tombstones before traffic；
8. residue query 必须为 0；
9. drill 只使用批准的 internal/synthetic identities，不故意破坏真实候选人数据；
10. 记录聚合 gates。

**Exit gate:** Consent、session purge、principal purge、backup replay 全部 PASS。

---

## Task 18：关闭 Write Shadow 并发布 Acceptance Evidence

**Goal:** 对 Write 阶段形成不可变三态结论。

**Dependencies:** Tasks 15-17。

**Steps:**

1. 确认 mode disabled、新 proposal operation=0；
2. 运行 production Write sanitizer/acceptance；
3. Privacy/Security artifact review；
4. 合并 quality 和 deletion gates；
5. PASS 仍不授权 Read；
6. 生成独立 post-observation manifest；
7. clean RC full regression 和 remote reproduction；
8. CONTINUE 需要新窗口，BLOCKED 需要新 RC/批准。

**Pass output:**

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=PASS
PROPOSAL_QUALITY_GATE=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

---

## Task 19：实现 Production Read Shadow Contracts、Window 与 Acceptance

**Goal:** 为 bounded would-select 和零注入生产证据建立独立工具。

**Dependencies:** Tasks 10、18。

**Files:** production read observation/window/acceptance scripts、tests、fixtures、contracts、runbook。

**Steps:**

1. schema 只接受 aggregate selection/outcome/invariant counts；
2. 记录 relevant、conflict、stale、revoked、deleted、cap exclusion counts；
3. Prompt/Provider digest 只在内存比较，不持久化 digest；
4. zero-injection mismatch 为 immediate hard stop；
5. state machine 和三态 acceptance；
6. unknown/private field fail-closed；
7. offline only；
8. success 仍输出 Consumption BLOCKED。

**Exit gate:** `PRINCIPAL_READ_SHADOW_TOOLING=READY_FOR_REVIEW`。

---

## Task 20：生成 Read RC、审批包并取得外部批准

**Goal:** 把 Read tooling 与 Write PASS/control plane 精确绑定。

**Dependencies:** Task 19。

**Steps:**

1. clean RC full validation；
2. manifest/bundle/remote drill；
3. phase 固定 `PRINCIPAL_READ_SHADOW_ZERO_INJECTION_ONLY`；
4. 五角色重新批准；
5. preflight 验证 exact revision/scope/window/traffic；
6. 只允许 Read Shadow axis；
7. active facts 只能来自明确 candidate/internal fixture confirmation；
8. Consumption 继续硬拒绝。

**Exit gate:** `PRINCIPAL_READ_SHADOW_CHANGE_PREFLIGHT=PASS`，配置仍未变化。

---

## Task 21：运行 Read Shadow 0.1% Warm-up

**Goal:** 最小流量证明 selection 和 zero-injection barrier。

**Dependencies:** Task 20。

**Steps:**

1. sticky 0.1% eligible cohort；
2. 至少 30 分钟和 20 follow-ups；
3. 每次 operation 重读 identity、Consent、ignore/disable、fact/source status；
4. in-memory canonical Prompt/Provider Context before/after equality；
5. conflict 全排除；
6. max facts/tokens 不超限；
7. question/score/report/API equality；
8. mismatch 立即 disabled。

**Exit gate:** `READ_WARM_UP=PASS`。

---

## Task 22：运行 Read Shadow Approved-cap Observation

**Goal:** 在最大 1% cohort 上取得 would-select 分布和零注入证据。

**Dependencies:** Task 21。

**Steps:**

1. 至少 24 小时和 200 follow-ups；
2. 记录 coarse fact category 和 exclusion reason counts；
3. 记录 relevant-but-not-authorized；
4. 监控 cache/store failure 的 deterministic fallback；
5. 监控 latency/error 和 metrics completeness；
6. 监控 Consent revoke、disable、delete 和 source tombstone；
7. scheduled end 恢复 disabled；
8. 导出 sanitized aggregate。

**Exit gate:** window CLOSED；等待 Tasks 23-24。

---

## Task 23：执行 Prompt、Provider、Question、Score、Report 零注入审计

**Goal:** 证明 Read Shadow 没有任何候选人可见或招聘语义变化。

**Dependencies:** Task 22。

**Steps:**

1. canonical Unicode NFC、sorted key、compact JSON、stable message order；
2. 比较 Provider Context 和 Prompt SHA-256，仅保存 equality count；
3. question text/order equality；
4. evaluator input/output equality；
5. score/evidence/report/PDF equality；
6. API response equality；
7. source audit 禁止 Read Shadow dependency 进入 score/report/Knowledge；
8. adversarial fact 尝试改变评分、泄露、激活或 Knowledge write；
9. 任一差异 hard stop。

**Exit gate:** `PROMPT_BUSINESS_ZERO_INJECTION=PASS`。

---

## Task 24：执行 Conflict、Freshness、Fairness 与 Relevance 复审

**Goal:** 判断 would-select 是否在未来有资格进入 C1 设计。

**Dependencies:** Task 22。

**Steps:**

1. exclusive conflicts 全部排除；
2. current session contradiction 排除历史 fact；
3. source deleted/unavailable/expired 排除；
4. taxonomy version mismatch 排除；
5. accessibility 只接受 direct declaration；
6. language、role、goal 做 protected proxy review；
7. confirmed_skill 在 C1 继续排除；
8. reviewer 标签 useful_but_not_authorized、irrelevant、stale、conflict、unsafe_proxy；
9. 只提交聚合 counts/rates。

**Exit gate:** conflict/freshness invariants=0，fairness review 无 Critical open finding。

---

## Task 25：关闭 Read Shadow 并发布 Acceptance Evidence

**Goal:** 对零注入 Read 阶段形成正式结论。

**Dependencies:** Tasks 22-24。

**Steps:**

1. 确认 Read disabled、新 selection=0；
2. sanitizer/acceptance；
3. artifact privacy review；
4. 合并 zero-injection、conflict/fairness 和 lifecycle gates；
5. 生成独立 post-observation manifest；
6. clean RC regression/remote drill；
7. PASS 只允许 Task 26 完成 Spec；
8. 不输出 implementation 或 canary authorized。

**Pass output:**

```text
PRINCIPAL_READ_SHADOW_PRODUCTION=PASS
PROMPT_BUSINESS_ZERO_INJECTION=PASS
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=READY_FOR_FINAL_REVIEW
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

---

## Task 26：把 Consumption Draft 晋升为经批准的 Spec v1

**Goal:** 解决 Draft open decisions，并取得 implementation authorization。

**Dependencies:** Tasks 7-10、25。

**Files:**

- Modify: `docs/principal-memory-consumption-spec.md`
- Modify: `docs/principal-memory-consumption-risk-review.md`
- Create: decision records for auth、Consent copy、jurisdiction、freshness、export、provider、canary population

**Steps:**

1. 固定 authenticated account model；
2. 批准 Consent 文案和 jurisdiction 行为；
3. 固定 correction history visibility 和 export format；
4. 固定每类 freshness window；
5. 固定 Provider retention/cancellation policy；
6. 固定 fairness metrics 和 canary population；
7. 固定 emergency disable owner；
8. 将 PMC-001 至 PMC-010 映射到 acceptance tests；
9. Product、Privacy、Security、Fairness、Operations、Legal 评审；
10. 只有正式 decision record 才把 `IMPLEMENTATION=AUTHORIZED` 写入外部系统，不把批准记录写进 Git。

**Exit gate:** Spec v1 approved for implementation；production canary 仍未授权。

---

## Task 27：实现 Consumption Config、Contracts 与 Migration Foundation

**Goal:** 添加默认关闭的 C1 模式和必要持久状态。

**Dependencies:** Task 26。

**Files:**

- Modify: `app/services/memory_config.py`
- Create: `app/ports/principal_memory_consumption.py`
- Create: `app/services/principal_memory_consumption_contracts.py`
- Modify: migration registry/session state
- Create: contract/config/migration tests

**Steps:**

1. 添加 `consume_c1`，继续拒绝泛化 `consume`；
2. explicit C1 gate、traffic、max facts=3、max tokens=120；
3. session ignore、visible disclosure 和 assignment version 持久化；
4. config conflict fail-closed；
5. legacy env 不得暗中启用 C1；
6. migration nullable/backfill/constraint 顺序；
7. old sessions 保持 deterministic/no-memory；
8. rollback 不删除 migration/data/tombstone。

**Exit gate:** 默认配置仍 disabled；无授权环境无法加载 C1 enabled config。

**Suggested commit:** `feat(memory): define bounded c1 consumption contracts`

---

## Task 28：实现 C1 Deterministic Selector

**Goal:** 在 operation time 选择最多 3 个、最多 120 tokens 的安全 facts。

**Dependencies:** Task 27。

**Files:**

- Create: `app/services/principal_memory_consumption.py`
- Extend: retrieval/eligibility modules
- Create: selector tests

**Steps:**

1. exact deployment/principal/Consent/policy/session checks；
2. only active user-confirmed facts；
3. C1 allowlist 和 follow-up operation；
4. current session contradiction 优先；
5. exclusive conflict 全排除；
6. deterministic dedupe/order；
7. provider tokenizer + conservative fallback；
8. 超预算按完整 fact 排除，不截断；
9. store failure 返回 empty selection；
10. 输出不含 raw source。

**Exit gate:** selector property/fuzz/conflict/token tests PASS。

**Suggested commit:** `feat(memory): select bounded c1 principal facts`

---

## Task 29：实现 Visible Renderer 与固定 Prompt Marker

**Goal:** 把 selected facts 渲染为候选人可见、非权威、不可用于评分的结构块。

**Dependencies:** Task 28。

**Files:**

- Create: `app/services/principal_memory_context_renderer.py`
- Create: renderer/snapshot/adversarial tests

**Steps:**

1. 标题精确为 `Non-authoritative historical preference`；
2. 固定 disclaimer；
3. 只渲染 canonical category/value、authority、confirmation、coarse source status；
4. 不渲染 source excerpt、prior answer、score、report、locator、digest；
5. Unicode/escaping 防 Prompt injection；
6. current session priority 文案不可被 fact 覆盖；
7. max facts/tokens 再验证；
8. exact snapshots 覆盖四类 facts 和多语言。

**Exit gate:** marker/placement/content privacy contract PASS。

**Suggested commit:** `feat(memory): render visible non-authoritative context`

---

## Task 30：仅在 Follow-up Context Assembly 集成 C1

**Goal:** 将 C1 限制在唯一允许的 runtime operation。

**Dependencies:** Task 29。

**Files:**

- Modify: interview v2 follow-up context assembly
- Modify: runtime dependency provider
- Create: integration/source isolation tests

**Steps:**

1. 在 system policy 和 current plan/evidence 后、current candidate message 前插入；
2. operation-time barrier 在 selector 前和 Provider call 前各检查一次；
3. current request 明确选择覆盖 memory；
4. selector/renderer failure 返回 deterministic context；
5. Prep/evaluation/report/review routes 无 dependency；
6. legacy/v1 sessions 不消费；
7. sticky assignment 保持 session 一致；
8. duplicate/retry 不改变 fact order。

**Exit gate:** follow-up integration PASS；所有非允许路径 source audit PASS。

**Suggested commit:** `feat(memory): integrate c1 follow-up context only`

---

## Task 31：集成 Candidate Indicator、Explanation 与实时控制

**Goal:** 每次消费对用户可见并可立即停止。

**Dependencies:** Tasks 8、30。

**Files:** Interview UI、Memory Center、API、browser/accessibility tests。

**Steps:**

1. 面试开始提供 Ignore；
2. 每次使用显示 visible indicator；
3. explanation 说明使用 category 和 bounded effect，不显示内部 reasoning；
4. Disable now 始终可达；
5. current request 已发出的边界清楚说明；
6. no-penalty 文案和行为；
7. screen reader live region 不泄漏隐藏内容；
8. desktop/mobile/keyboard/slow-network tests。

**Exit gate:** candidate comprehension、accessibility 和 disable UX PASS。

**Suggested commit:** `feat(frontend): expose principal memory controls`

---

## Task 32：强化 Score、Report 与 Knowledge Firewall

**Goal:** 用结构隔离阻止长期记忆进入招聘语义路径。

**Dependencies:** Task 30。

**Files:** source audits、dependency tests、score/report equality tests、Knowledge firewall tests。

**Steps:**

1. scoring/evaluator constructor 无 Principal consumption dependency；
2. report generator/repair 无 dependency；
3. evidence selection/PDF 无 dependency；
4. Knowledge ingestion/query 拒绝 Principal types；
5. embeddings 不接受 Principal data；
6. C1 on/off exact score/report/evidence equality；
7. adversarial block 请求打分、激活、泄露、Knowledge write；
8. static/source tests 防未来回归。

**Exit gate:** `SCORING_REPORT_KNOWLEDGE_ISOLATION=PASS`。

**Suggested commit:** `test(memory): enforce consumption isolation firewalls`

---

## Task 33：实现 Consumption Metrics、Kill Switch 与 Window Tooling

**Goal:** 建立低基数观察、自动停止和可验证关闭。

**Dependencies:** Tasks 30-32。

**Files:** metrics port/store、production C1 observation/window/acceptance scripts、runbook/tests。

**Steps:**

1. 聚合 assignment、eligible、selected、excluded、fallback、disable counts；
2. coarse fact/language/path buckets；
3. low-volume suppress/merge；
4. durable minute/hour rollup；
5. central kill switch 在下一 assembly 前阻止 injection；
6. stop new leasing；
7. verify zero post-stop injection；
8. offline sanitizer/three-state acceptance；
9. hard stops 和 error/latency thresholds；
10. no raw/digest/locator fields。

**Exit gate:** timed rollback drill 和 privacy artifact tests PASS。

**Suggested commit:** `feat(memory): observe and stop c1 consumption`

---

## Task 34：执行完整 Consumption 测试矩阵

**Goal:** 在进入 Staging 前证明功能、安全、隐私、公平性和恢复边界。

**Dependencies:** Tasks 27-33。

**Required suites:**

1. authenticated identity/account recovery；
2. Consent purpose/version/TOCTOU；
3. view/confirm/correct/revoke/delete/export；
4. ignore/disable/in-flight race；
5. cross-Principal/cache collision；
6. C1 allowlist/confirmed_skill exclusion；
7. conflict/stale/source delete；
8. max 3/max 120 tokenizer fallback；
9. exact Prompt placement/marker；
10. current-session override；
11. score/report/evidence/PDF equality；
12. Knowledge/embedding firewall；
13. live PostgreSQL migration/concurrency/cleanup；
14. backup restore/tombstone replay；
15. React/browser/accessibility；
16. seven-intent adversarial Prompt suite；
17. protected proxy/disparate impact fixtures；
18. metrics privacy/low-cardinality；
19. kill-switch timed rollback；
20. full regression and remote reproduction。

**Exit gate:** 所有 hard invariants=0；无跳过的 mandatory suite。

**Suggested commit:** `test(memory): cover c1 consumption safety matrix`

---

## Task 35：运行 Isolated Staging Consumption

**Goal:** 在 synthetic/internal explicitly authorized cohort 上验证真实 context injection。

**Dependencies:** Task 34。

**Steps:**

1. isolated PostgreSQL/deployment scope；
2. explicit authenticated test principals；
3. 300 sessions，中文/英文/mixed 各至少 100；
4. 四类 C1 facts；
5. ignore/disable/correct/delete/conflict/restore matrix；
6. Provider 可使用 approved non-production account；
7. 验证 visible indicator 和 Prompt block；
8. scoring/report equality；
9. latency/error/quality review；
10. cleanup residue=0，config restored disabled。

**Exit gate:**

```text
PRINCIPAL_MEMORY_C1_STAGING=PASS
SCORING_REPORT_KNOWLEDGE_ISOLATION=PASS
DISABLE_DELETE_RESTORE=PASS
PRODUCTION_CANARY=NOT_AUTHORIZED
```

---

## Task 36：生成 C1 RC、Evidence Bundle 并取得独立批准

**Goal:** 为最高 1% production C1 创建全新 exact-revision 授权。

**Dependencies:** Task 35。

**Steps:**

1. clean RC full regression/live PG/browser；
2. C1 readiness evidence；
3. manifest/bundle/sidecar/remote drill；
4. Privacy、Security、Fairness、Operations、Product、Change Owner、必要时 Legal 批准；
5. phase 固定 `PRINCIPAL_MEMORY_CONSUMPTION_C1_ONLY`；
6. cohort 仅 explicitly opted-in eligible sessions；
7. max traffic=1%；
8. independent rollback owner/incident channel；
9. external record 不进 Git；
10. C1 preflight PASS 前配置不变。

**Exit gate:** `PRINCIPAL_MEMORY_C1_CHANGE_PREFLIGHT=PASS`。

---

## Task 37：运行 C1 0.1% Warm-up

**Goal:** 在最小真实流量上验证 disclosure、control、injection 和 rollback。

**Dependencies:** Task 36。

**Steps:**

1. sticky 0.1% opted-in eligible sessions；
2. 至少 30 分钟和 20 consumption calls；
3. 每次调用检查 identity/Consent/ignore/disable/fact/source；
4. marker、placement、max facts/tokens；
5. current session priority；
6. score/report/Knowledge isolation；
7. disable SLO 和 kill switch；
8. candidate-visible indicator；
9. hard stop=0 才 ramp；
10. 不足输出 CONTINUE 并关闭。

**Exit gate:** `C1_WARM_UP=PASS`，不是 C1 最终 PASS。

---

## Task 38：运行最大 1% C1 Canary 与 Acceptance

**Goal:** 取得第一版用户可用长期记忆的生产安全与质量证据。

**Dependencies:** Task 37。

**Steps:**

1. traffic 不超过 approved cap 和 1%；
2. 至少 24 小时和 200 consumption calls；
3. 监控所有 C1 hard stops；
4. error delta≤0.5 percentage points；
5. P95 latency≤baseline×1.20；
6. candidate disable/correct/delete outcomes；
7. coarse relevance/usefulness 和 no-penalty metrics；
8. fairness bucket insufficient 时禁止外推；
9. scheduled end 恢复 disabled；
10. sanitizer/Privacy/Security/Fairness review；
11. 运行 three-state acceptance；
12. 不因 PASS 自动扩容。

**Pass output:**

```text
PRINCIPAL_MEMORY_CONSUMPTION_C1=PASS
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
MAX_VERIFIED_TRAFFIC_PERCENT=1
SCORING_REPORT_KNOWLEDGE_ISOLATION=PASS
EXPANSION_ABOVE_1_PERCENT=NOT_AUTHORIZED
```

---

## Task 39：关闭 C1、发布 Post-observation Evidence 并定义后续扩容边界

**Goal:** 形成完整证据闭环并停止在 1% 上限。

**Dependencies:** Task 38。

**Steps:**

1. 确认 C1 disabled、new injection=0、worker leasing stopped；
2. 保留 facts/lifecycle/tombstones，不通过删除 migration 回滚；
3. 发布 sanitized C1 observation 和 acceptance；
4. pre-approval evidence 保持 immutable；
5. clean RC full regression/remote reproduction；
6. 汇总 candidate research、privacy、fairness 和 operational residual risks；
7. 若 PASS，起草独立 5% plan；
8. 若 CONTINUE，申请新 1% 窗口；
9. 若 BLOCKED，修复后新 RC/新审批；
10. 明确本计划不授权 5%、25%、50% 或 100%。

**Terminal output:**

```text
LONG_TERM_MEMORY_C1_EVIDENCE=CLOSED
EXPANSION_ABOVE_1_PERCENT=NOT_AUTHORIZED
GENERAL_AVAILABILITY=NOT_AUTHORIZED
```

---

## 10. 晋级门禁总表

| From | To | Required gate |
|---|---|---|
| Budget tooling ready | Budget production window | 五角色批准、exact preflight PASS |
| Budget window | Budget PASS | closed、restored、≥24h、≥200、hard stop=0 |
| Budget PASS | Product control plane | authenticated identity、Consent、user rights |
| Control plane | Write approval | Task 10 PASS、new RC/bundle |
| Write warm-up | Write observation | ≥30m、≥20、hard stop=0 |
| Write observation | Write PASS | ≥24h、≥200、quality/delete/restore PASS |
| Write PASS | Read approval | new RC/bundle/five-role approval |
| Read warm-up | Read observation | zero injection、≥30m、≥20 |
| Read observation | Read PASS | ≥24h、≥200、Prompt/business equality、fairness review |
| Read PASS | Spec v1 | PMC-001 至 PMC-010 全部映射、open decisions closed |
| Spec v1 | Implementation | external implementation authorization |
| Implementation | C1 Staging | complete test matrix |
| C1 Staging | C1 production approval | Staging PASS、new RC/bundle、independent approvals |
| C1 0.1% | C1 1% | warm-up PASS、hard stop=0 |
| C1 closed | C1 PASS | ≥24h、≥200、restored、privacy/security/fairness PASS |
| C1 PASS | 5% planning | new plan only；no automatic authorization |

---

## 11. 回滚矩阵

| Failure | Immediate action | Data/lifecycle action | Next authorization |
|---|---|---|---|
| Approval absent/pending | HOLD | no data change | obtain valid record |
| Approval expired/revoked | STOP_NOW | preserve aggregate evidence | new window |
| Revision/scope mismatch | STOP_NOW | no migration rollback | new RC/approval |
| Identity ambiguous | memory fail-closed | no read/write | fix identity contract |
| Account takeover/recovery risk | disable all Principal Memory | freeze affected mappings | security incident + reapproval |
| Cross-Principal access | disable all Principal Memory | preserve minimal incident/tombstone evidence | privacy/security incident |
| Consent missing/revoked | stop operation | queue required purge | new explicit Consent only |
| Disable SLA breach | kill switch | stop leasing, verify zero new operations | fix + new RC |
| Automatic active fact | stop Write | revoke/terminalize unsafe fact | privacy/security review |
| Free-text/private fact | stop Write | reject/purge proposal | taxonomy/extractor fix |
| Proposal quality below gate | close Write | proposals remain unconfirmed | new observation |
| Delete residue | disable Write/Read/C1 | continue purge/tombstone | no advancement |
| Backup resurrection | restored copy gets no traffic | replay tombstones and re-query | operations/privacy approval |
| Read Prompt mutation | stop Read | deterministic context restored | new RC |
| Read score/report difference | stop Read | preserve equality evidence | fairness/security review |
| Conflict selected | stop Read/C1 | exclude all conflict values | selector fix |
| Stale/deleted source selected | stop Read/C1 | mark ineligible | lifecycle fix |
| Non-C1 fact consumed | kill C1 | revoke assignment, audit facts | new Spec/RC |
| Marker/disclosure missing | kill C1 | deterministic fallback | UX/privacy fix |
| Current evidence overridden | kill C1 | deterministic fallback | interview quality/fairness review |
| Knowledge/embedding mutation | disable all Principal Memory | purge contamination and verify corpus | security incident |
| Private metrics artifact | stop window | quarantine/delete artifact | privacy review |
| Metrics incomplete | STOP or CONTINUE | keep deterministic path | new window if needed |
| Traffic cap exceeded | STOP_NOW | no new assignment | incident + new approval |
| Error/latency regression | STOP_NOW | deterministic fallback | performance fix/new RC |
| Production code defect | STOP_NOW | do not hotfix in window | new RC/full regression |
| Scheduled end | disable first | retain legitimate facts/tombstones | acceptance only |
| Sample insufficient | close as CONTINUE | preserve aggregates | new approved window |
| Rollback not verified | incident remains open | no next phase | operations/security clearance |

---

## 12. 风险登记

| Risk | Severity | Mitigation | Required evidence |
|---|---|---|---|
| Identity collision | Critical | issuer/subject HMAC + exact deployment keys | collision/cross-issuer tests |
| Account takeover | Critical | approved auth/recovery + no auto inherit | threat model/penetration tests |
| Consent dark pattern | Critical | default off、purpose split、no penalty | UX/comprehension audit |
| Consent TOCTOU | Critical | operation-time re-read + barrier | concurrent revoke tests |
| Disable race | Critical | next-assembly + 60s SLO | timed race tests |
| Cross-Principal cache leakage | Critical | full cache identity key | concurrency/collision tests |
| Historical anchoring | Critical | current session wins、C1 allowlist | conflict/equality review |
| Protected-class proxy | Critical | direct declaration、fairness review | proxy/disparate-impact fixtures |
| Prompt injection | High | canonical renderer + fixed marker | adversarial suite |
| Hidden personalization | High | visible indicator/explanation | browser/accessibility tests |
| Stale/conflicting fact | High | freshness/source/conflict exclusion | lifecycle fixtures |
| Automatic confirmation | Critical | proposed-only model authority | state transition tests |
| Public Knowledge contamination | Critical | separate dependencies/store | source/firewall audit |
| Scoring/report influence | Critical | structural isolation | exact equality tests |
| Incomplete deletion | Critical | purge + tombstone replay | residue/restore drills |
| Backup resurrection | Critical | replay before traffic | repeated restore tests |
| Observation re-identification | High | low-cardinality suppression | artifact privacy audit |
| Provider retention | Critical | approved provider policy + minimal block | Legal/Security review |
| Canary drift | High | sticky assignment/version | deterministic assignment tests |
| Rollback failure | Critical | central kill switch + zero post-stop | timed rollback drill |
| Config conflict | High | canonical-only fail-closed | config tests/preflight |
| Moving master vs approved revision | High | deploy exact immutable SHA | remote reproduction |
| External approval leakage | High | record remains outside Git | source/artifact scans |
| Low sample false PASS | High | CONTINUE state | sample gate tests |
| Fairness underpowered | High | mark insufficient, no extrapolation | bucket counts/review |
| User correction misuse | High | terminal predecessor exclusion | correction/export tests |
| Excessive retention | High | approved retention and delete SLO | lifecycle metrics |
| UX accessibility failure | High | WCAG/browser review | accessibility suite |
| Migration rollback misuse | High | forward-compatible disable | migration/restore tests |
| Scope expansion by configuration | Critical | phase-specific mode/gate | preflight/config matrix |

---

## 13. Traceability

本计划不创建新的 `MEM-*` requirement ID。它引用：

- Memory Optimization Spec §15、§16、§17、§18、§19、§21、§22；
- 既有 Principal Memory foundation contracts；
- `principal-memory-consumption-spec.md` 的 PMC-001 至 PMC-010；
- `principal-memory-consumption-risk-review.md`；
- production approval/change-preflight/evidence manifest contracts；
- Write/Read Shadow Staging runbooks 和 proposal review protocol。

PMC requirements 映射：

| Requirement | Tasks |
|---|---|
| PMC-001 Authenticated Principal | 3、4、10、34 |
| PMC-002 Default-off purpose Consent | 5、7、10、31、34 |
| PMC-003 View/correct/revoke/delete/export | 6、7、9、34 |
| PMC-004 Ignore/Disable | 8、31、34、37 |
| PMC-005 Visible indicator/explanation | 29、31、35、37 |
| PMC-006 Rights/restore SLO | 8、9、17、33、34 |
| PMC-007 Production approvals | 13、20、26、36 |
| PMC-008 Independent canary/rollback | 33、36、37、38 |
| PMC-009 Score/report isolation | 23、32、34、38 |
| PMC-010 Knowledge/deletion/metrics/isolation | 17、23、32、33、34 |

Production-specific schema names和 gate codes 是实现契约，不是新产品 requirement ID。

---

## 14. Definition of Done

本计划只有在以下 50 项全部满足后，才能称为完成：

1. Task 0 执行时基线和 worktree ownership 已记录；
2. 用户已有修改未被覆盖、清理或错误提交；
3. Production Budget Shadow 已关闭并输出 PASS；
4. Budget post-observation evidence 通过 Privacy/Security audit；
5. authenticated Principal 不使用 PII 或 inferred identity；
6. account recovery 不自动继承 memory；
7. Consent 四个 purpose 独立、versioned、default off；
8. Consent 在每次 operation 时重新读取；
9. authenticated self-service API 通过 authorization/CSRF/enumeration tests；
10. Candidate Memory Center 通过 accessibility/no-dark-pattern review；
11. view/confirm/correct/revoke/delete/export 全部可用；
12. ignore-for-session 是 sticky；
13. disable-now 在下一 assembly 且 60 秒内生效；
14. decline/ignore/disable/correct/delete/export 均无评分或功能惩罚；
15. online delete 在批准 SLO 内完成；
16. backup restore tombstone replay residue=0；
17. Write production tooling 是 offline allowlisted sanitizer/evaluator；
18. Write RC、manifest、bundle 和 remote reproduction 通过；
19. Write external approval/preflight 绑定 exact revision/scope/window/traffic；
20. Write warm-up 达到 30 分钟和 20 样本；
21. Write observation 达到 24 小时和 200 样本；
22. Write hard invariants 全为 0；
23. 所有 model outputs 保持 proposed；
24. Production proposal quality gate PASS；
25. Write Consent/delete/restore drills PASS；
26. Write window 已关闭且配置恢复 disabled；
27. Read production tooling/RC/approval 独立于 Write；
28. Read warm-up 达到 30 分钟和 20 样本；
29. Read observation 达到 24 小时和 200 样本；
30. Read Prompt/Provider Context mutation=0；
31. Read question/score/report/evidence/API difference=0；
32. conflict/stale/revoked/deleted/unconfirmed selection=0；
33. Read window 已关闭且配置恢复 disabled；
34. Consumption Spec v1 已解决全部 open decisions；
35. PMC-001 至 PMC-010 均有命名测试和批准 owner；
36. C1 allowlist 仅包含四个批准 categories；
37. confirmed_skill 在 C1 被拒绝；
38. C1 selector 固定 max 3 facts/max 120 tokens；
39. Prompt marker 和 placement 精确；
40. C1 只集成 follow-up context assembly；
41. Scoring、report、Knowledge 和 embeddings 结构隔离；
42. Consumption metrics 低基数且无 private fields；
43. kill switch timed rollback 和 zero post-stop injection PASS；
44. 完整 unit/PG/browser/security/privacy/fairness matrix PASS；
45. Isolated Staging Consumption PASS；
46. C1 使用独立 RC、bundle 和外部批准；
47. C1 0.1% warm-up PASS；
48. C1 1% window 达到 24 小时和 200 calls，hard stop=0；
49. C1 scheduled close 后配置恢复 disabled，post-observation evidence verified；
50. 最终仍明确 `EXPANSION_ABOVE_1_PERCENT=NOT_AUTHORIZED` 和 `GENERAL_AVAILABILITY=NOT_AUTHORIZED`。

---

## 15. 稳定状态输出

### 15.1 当前计划阶段

```text
PRODUCTION_BUDGET_SHADOW=NOT_RUN
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

### 15.2 Write PASS 后

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=PASS
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

### 15.3 Read PASS 后

```text
PRINCIPAL_READ_SHADOW_PRODUCTION=PASS
PROMPT_BUSINESS_ZERO_INJECTION=PASS
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=READY_FOR_FINAL_REVIEW
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

### 15.4 C1 Staging PASS 后

```text
PRINCIPAL_MEMORY_C1_STAGING=PASS
PRODUCTION_CANARY=NOT_AUTHORIZED
EXTERNAL_C1_APPROVAL_REQUIRED
```

### 15.5 C1 Production PASS 后

```text
PRINCIPAL_MEMORY_CONSUMPTION_C1=PASS
MAX_VERIFIED_TRAFFIC_PERCENT=1
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
EXPANSION_ABOVE_1_PERCENT=NOT_AUTHORIZED
GENERAL_AVAILABILITY=NOT_AUTHORIZED
```

---

## 16. 5% 以上后续路线（不属于本计划授权）

只有 Task 39 完成且 C1 PASS 后，下一计划才可以评估：

```text
1% → 5% → 25% → 50% → 100%
```

每一级都需要：

- 新 candidate research；
- 新 fairness distribution review；
- 新 privacy/security/operations approval；
- 新 exact revision；
- 新 traffic/window record；
- 新 warm-up 和 rollback drill；
- 新 post-observation acceptance；
- 前一级配置先关闭并形成 evidence。

任何一级出现 Critical/High safety issue 都回到 disabled，不降级为“继续带风险扩容”。General Availability 还需要长期 SLO、on-call、季度删除恢复演练、年度 Consent/公平性复审和 deprecation/exit plan。

---

## 17. 最终边界

本计划完整执行后最多可以证明：

```text
经过 authenticated identity、candidate control、Write/Read production Shadow、
Consumption Spec v1、完整实现与 Staging、独立生产批准和最大 1% C1 Canary，
四类 user-confirmed、bounded、visible、non-authoritative preference
可以仅在 follow-up context assembly 中安全使用，
同时不改变 scoring、report、Knowledge 或招聘判断，
并且 ignore、disable、correct、delete、restore 和 rollback 可执行。
```

它仍然不能证明或授权：

```text
confirmed_skill consumption
自由文本长期记忆
历史评分或回答复用
隐式 personalization
cross-Principal retrieval
5% 以上流量
General Availability
长期记忆参与评分、报告或招聘判断
```
