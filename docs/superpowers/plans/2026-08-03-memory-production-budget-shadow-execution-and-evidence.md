# Interview Agent 生产 Budget Shadow 执行、观察与证据闭环计划

**Plan revision:** v1.1-draft

**Published baseline at plan authoring:** `b1559dadde4195449d1841322c4fe931984197dc`

**Initial plan commit:** `87f25689d798c6e531dbdc5eea5bcc86ad7c049a`

**Document type:** Implementation Plan / How-to + Reference

**Target audience:** Change Owner、Operations、Privacy、Security、Fairness、后端工程师、Agent 工程师、SRE、QA 与验收负责人

**Primary goal:** 在不启用 Budget enforcement、Compression consumption、Question Memory consumption 或 Principal Memory 的前提下，把 Production Budget Shadow 从外部审批推进到受控观察、强制关闭和可审计结论。

**Status at authoring:** `APPROVAL_STATUS=PENDING`、`EXTERNAL_APPROVAL_RECORD=ABSENT`、`PRODUCTION_OBSERVATION=NOT_RUN`。

**v1.1 review amendments:** 把发布基线、初始 plan commit 和 Task 0 动态执行基线分开；为 17 项明确排除、14 个立即 hard stop、12 个安全配置键、12 项固定决策、回滚矩阵和 25 项 Definition of Done 增加完整契约测试；Task 标题同时接受全角/半角冒号；requirement ID 审计覆盖连字符、下划线和大小写变体；并把 `EXTERNAL_APPROVAL_RECORD=ABSENT` 约束在作者时状态上下文，而不是把它误写为所有未来阶段的永久状态。

> **授权边界：** 本计划本身不授权生产变更。Task 0～Task 6 是审批前仓库工作；Task 7 以后只有在仓库外正式审批记录存在、精确 revision-bound change preflight 返回 `PASS`、目标环境和窗口均匹配时才能执行。通用仓库操作权限不能替代 Change Owner、Operations、Privacy、Security 和 Fairness 的独立批准。

---

## 1. 阶段结论

下一阶段的正确目标不是实现长期记忆消费，也不是直接开启 Principal Memory Write/Read Shadow，而是完成一个单轴、可停止、可回滚、可审计的 Production Budget Shadow 窗口。

固定顺序为：

```text
Production 结果契约与离线验收器
  → 新 RC 与新 PENDING 审批包
  → 五方外部审批
  → revision-bound change preflight
  → 0.1% warm-up
  → 最多 1% approved cap
  → 至少 24 小时且至少 200 follow-up 样本
  → 强制恢复 disabled
  → 聚合结果审计
  → PASS / BLOCKED / CONTINUE_OBSERVATION
```

任何步骤失败都回到最后一个已验证的 deterministic Interview 路径。不得跳过审批、样本门禁、窗口关闭或隐私审计。

---

## 2. 当前基线

### 2.1 作者时发布基线与计划提交状态

本计划开始编写时，已经发布并完成远端复现的产品基线为：

```text
b1559dadde4195449d1841322c4fe931984197dc
```

在初始 plan commit 创建之前，该发布基线满足：

```text
origin/master == HEAD
ahead=0
behind=0
```

初始计划文档提交后，状态变为：

```text
HEAD=87f25689d798c6e531dbdc5eea5bcc86ad7c049a
origin/master=b1559dadde4195449d1841322c4fe931984197dc
ahead=1
behind=0
```

以上是可审计的历史快照，不是 Task 0 执行时必须继续成立的动态断言。本计划的 review amendment、后续 tooling commit 和 evidence commit 都会继续推进 `HEAD`。Task 0 必须读取执行当时的实际 `HEAD`、`origin/master` 和 ahead/behind，记录为 `EXECUTION_START_HEAD`；不得硬编码或期待 `HEAD=b1559da`。

该作者时发布基线的机器验收：

```text
1590 passed
163 skipped
MEMORY_SHADOW_RC=REPRODUCIBLE
BUDGET_SHADOW_STAGING=PASS
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
PRODUCTION_SHADOW_APPROVAL_REQUIRED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

该作者时发布基线的 evidence manifest：

```text
schema_version=memory-production-shadow-evidence-manifest-v2
content_normalization=utf8-lf-v1
files=26
source_revision=d4f9810229105ce630f0a9ad4d88bb3bba2bc686
handoff_revision=b1559dadde4195449d1841322c4fe931984197dc
bundle_sha256=dc29fc052fc536aedafd8f02694f53d1d4dd7e00bcd0efdf49b4d7b627cf31d8
```

该作者时发布基线的 PENDING 审查包：

```text
archive=interview-agent-memory-budget-shadow-review-PENDING-b1559da.zip
archive_sha256=64f8b2c20e111e4f05e12cbe69da4874f5726049b514ead24f102ca9a91469ea
metadata_sha256=8b79fbb321e54b5980e9e1595e4b24d5968cdd7bbc984c305b60cb0566215fad
minimum_clone_depth=2
```

### 2.2 已验证的远端复现边界

真实 GitHub clone 已证明：

```text
depth 1 → GATE=SOURCE_REVISION_NOT_ANCESTOR
depth 2 → MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED
```

外部审批人必须使用完整 clone 或至少 depth 2。不得绕过 ancestry check。

### 2.3 当前缺口

仓库已有：

- Staging synthetic Budget Shadow observation；
- Staging Budget Shadow acceptance evaluator；
- production approval packet；
- external approval record contract；
- production change preflight；
- production Budget Shadow runbook；
- portable PENDING evidence bundle。

仓库尚缺：

1. 独立的 Production Budget Shadow 聚合输入 schema；
2. 生产结果的离线 sanitizer；
3. Production 专用 `PASS/BLOCKED/CONTINUE_OBSERVATION` evaluator；
4. warm-up、ramp、scheduled close 和 emergency stop 的机器状态契约；
5. 窗口结束后“不改写历史 PENDING evidence”的 post-observation artifact；
6. 对生产聚合结果的隐私字段审计；
7. 对 expired/revoked approval、traffic overshoot 和 config drift 的关闭证据；
8. 将生产结果与下一阶段申请解耦的正式 acceptance 文档。

`scripts/memory_budget_shadow_observe.py` 是合成 Staging runner，并明确要求 `provider_calls=0`、`data_category=synthetic`、`PRODUCTION_OBSERVATION=NOT_RUN`。它不能被重新命名或直接用于生产结果。

---

## 3. 本阶段范围

### 3.1 包含

- 定义 Production Budget Shadow 聚合结果 schema；
- 实现只读取脱敏聚合 export 的离线 validator/evaluator；
- 实现 deterministic stop-decision helper；
- 更新 production runbook 和 approval packet；
- 重新生成、验证并发布新的 PENDING RC；
- 取得五方外部审批；
- 在精确批准窗口内运行最多 1% Budget Shadow；
- 运行 0.1% warm-up 和受控 ramp；
- 观察至少 24 小时且至少 200 个 follow-up 样本；
- 在 scheduled end 前恢复 disabled；
- 生成不含个人信息、部署 digest 或审批人 binding 的聚合结果；
- 输出 `PASS`、`BLOCKED` 或 `CONTINUE_OBSERVATION`；
- 为下一阶段提供证据，但不自动授权下一阶段。

### 3.2 明确排除

- Production migration；
- Budget enforcement；
- Context Compression consumption；
- Question Memory production consumption；
- Principal Memory Write production Shadow；
- Principal Memory Read production Shadow；
- Principal Memory consumption；
- 把 personal facts 注入 Prompt、问题、追问、评分、报告或推荐；
- 自动创建、确认或激活 Principal facts；
- 使用真实候选人内容作为测试 fixture；
- 在仓库、CI artifact 或常规日志中保存正式 external approval record；
- 在仓库中保存 approver、ticket、deployment 或 record digest；
- 在聚合 evidence 中保存 session/principal/fact/question/message/artifact locator；
- 把 `b1559da` 的 PENDING metadata 当作审批记录；
- Production Budget Shadow PASS 后自动进入 Write Shadow；
- 在已批准窗口内热修 production code、切换 revision 或复用旧审批；
- 本计划内实现 Consumption Spec。

---

## 4. 固定决策

### Decision 1：先补齐生产结果工具，再申请审批

任何 tracked code、runbook、test 或 evidence contract 修改都会改变待部署 revision。Task 0～Task 6 完成后必须生成新的最终 RC 和 PENDING 包；外部审批只能绑定该最终 revision，不能继续绑定 `b1559da`。

### Decision 2：Production evaluator 必须离线工作

仓库 runner 不直接连接生产 PostgreSQL、指标系统、Secret Store 或变更系统。Operations 从受信系统导出 allowlisted 聚合 summary 到仓库外临时路径，离线 runner 只验证、清洗和判定该 summary。

### Decision 3：历史 PENDING evidence 不改写

当前 `memory-production-shadow-evidence-manifest-v2` 是审批前证据。生产窗口结束后不得把其中的 `PENDING/BLOCKED/NOT_RUN` 改成 `APPROVED/PASS/RUN`。生产结果使用新的独立 artifact 和 schema，保留原始审批前证据链。

### Decision 4：一次只改变 Budget Shadow

生产窗口唯一允许的 memory 配置变化是：

```text
MEMORY_BUDGET_MODE=shadow
MEMORY_BUDGET_SHADOW_ENABLED=true
```

所有 enforcement、compression、question memory 和 principal memory 开关保持 disabled/false。

### Decision 5：先 0.1% warm-up，再到批准上限

初始 effective traffic 为 `min(0.1%, approved cap)`。只有 warm-up 满足至少 20 个 follow-up 样本、持续至少 30 分钟且 hard stop 为 0，才可提升到批准上限；批准上限永远不得超过 1%。

如果在审批窗口内无法取得 20 个 warm-up 样本，不得提升流量，最终输出 `CONTINUE_OBSERVATION`。

### Decision 6：窗口结束先关闭，再判定

无论指标健康与否，scheduled end 到达时先恢复：

```text
MEMORY_BUDGET_MODE=disabled
MEMORY_BUDGET_SHADOW_ENABLED=false
```

确认新 Shadow event 停止后，才生成结论。不得为了凑样本在过期窗口内继续运行。

### Decision 7：不足不是 PASS

少于 200 个 follow-up 样本、缺少 baseline、关键 bucket 不完整或 metrics completeness 不满足时，只能输出 `CONTINUE_OBSERVATION`。继续观察需要新的审批窗口；不能延长原记录。

### Decision 8：低样本 bucket 不阻止总体安全结论，但禁止外推

总体样本达到 200 且所有 hard stop 为 0时，可以对整体 Budget Shadow 做结论。某语言或路径 bucket 少于 30 个样本时，必须标为 `INSUFFICIENT`，不得声称该 bucket 已得到生产验证。Staging Profile B 的多语言证据继续保留，但不能伪装成生产分布证据。

### Decision 9：Hard stop 不等待统计显著性

任何输入突变、mandatory loss、known over-limit call、隐私命中、流量超限、approval 失效、其他 memory axis 打开或 metrics 失真都立即停止。启动 emergency stop 不需要扩展审批。

### Decision 10：Production PASS 不授权 Write Shadow

本阶段成功终态只表示 Budget Shadow 观察完成。必须继续输出：

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

### Decision 11：生产窗口内禁止代码修补

窗口开始后发现 production-code 问题时，先关闭 Shadow。任何代码修改、依赖变更、migration 或配置契约修改都会使当前 approval record 失效，必须重新构建 RC、重新回归并重新审批。

### Decision 12：外部 record 和受信 digest 永不进入 Git

正式 record、record SHA、deployment scope SHA、approver refs 和 change-ticket binding 只保存在受信变更系统。仓库 evidence 只保留布尔验证结果、聚合计数、稳定 gate code 和公开 Git revision。

---

## 5. 安全配置矩阵

| 配置 | Disabled/默认 | Production Budget Shadow | 本阶段禁止 |
|---|---|---|---|
| `MEMORY_BUDGET_MODE` | `disabled` | `shadow` | `enforce` |
| `MEMORY_BUDGET_SHADOW_ENABLED` | `false` | `true` | 持续超出批准窗口 |
| `MEMORY_BUDGET_ENFORCEMENT_PREP` | `false` | `false` | `true` |
| `MEMORY_BUDGET_ENFORCEMENT_INTERVIEW` | `false` | `false` | `true` |
| `MEMORY_BUDGET_ENFORCEMENT_REVIEW` | `false` | `false` | `true` |
| `MEMORY_BUDGET_ENFORCEMENT_REPORT` | `false` | `false` | `true` |
| `MEMORY_COMPRESSION_MODE` | `disabled` | `disabled` | `consume` |
| `MEMORY_COMPRESSION_SHADOW_ENABLED` | `false` | `false` | `true` |
| `MEMORY_LONG_TERM_MODE` | `disabled` | `disabled` | `write_shadow`、`read_shadow`、`consume` |
| `MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED` | `false` | `false` | `true` |
| `MEMORY_LONG_TERM_READ_SHADOW_ENABLED` | `false` | `false` | `true` |
| `MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED` | `false` | `false` | `true` |

禁止同时配置 canonical `MEMORY_*` 与 legacy `CONTEXT_*` 键。任何冲突均为 hard stop。

---

## 6. Production 聚合结果契约

### 6.1 输入 artifact

Operations 从受信指标系统导出仓库外临时 JSON：

```text
memory-production-budget-shadow-aggregate-input-v1
```

输入允许包含：

- 公开 Git revision；
- requested phase；
- approved traffic cap；
- 实际最大 traffic percent；
- window duration；
- warm-up 和总 follow-up 样本；
- control/shadow 聚合样本；
- would-select、would-drop、fallback 计数；
- mandatory loss、input mutation、known over-limit call 计数；
- error-rate baseline/observed；
- P95 latency baseline/observed；
- coarse language/path bucket counts；
- metrics completeness；
- approval/current-window/config/rollback 的布尔结果；
- stable hard-stop code counts。

输入禁止包含：

- external approval record 或其路径；
- record、deployment、ticket、approver digest；
- environment、cluster、database、schema 或 table locator；
- DSN、credential、token、secret；
- session/principal/fact/question/message/artifact ID；
- Prompt、answer、resume、report、source excerpt；
- Provider request/response payload；
- 高基数自由文本 label。

### 6.2 输出 artifact

离线 sanitizer 生成：

```text
memory-production-budget-shadow-observation-v1
```

输出只包含 allowlisted 字段，并固定包括：

```text
approval_record_verified=true|false
deployment_scope_verified=true|false
revision_match=true|false
window_match=true|false
traffic_cap_respected=true|false
configuration_single_axis=true|false
data_complete=true|false
rollback_verified=true|false
configuration_restored=true|false
principal_write_shadow_production=NOT_AUTHORIZED
principal_read_shadow_production=NOT_AUTHORIZED
long_term_memory_consumption=BLOCKED
```

### 6.3 三态结论

```text
PASS
BLOCKED
CONTINUE_OBSERVATION
```

`CONTINUE_OBSERVATION` 不表示窗口可以自动延长；它表示当前窗口关闭、配置恢复 disabled，若要继续必须申请新窗口。

---

## 7. Hard stop 与统计门禁

### 7.1 立即 hard stop

以下任一值大于 0 或布尔条件失败，立即关闭 Shadow：

| Gate | 条件 |
|---|---|
| `MANDATORY_CURRENT_CONTENT_LOSS` | mandatory current content loss > 0 |
| `PROVIDER_INPUT_CHANGED` | Provider input mutation > 0 |
| `KNOWN_OVER_BUDGET_PROVIDER_CALL` | known over-limit call > 0 |
| `PRIVACY_AUDIT_HIT` | private/high-cardinality artifact hit > 0 |
| `TRAFFIC_CAP_EXCEEDED` | observed traffic > approved cap 或 > 1% |
| `APPROVAL_NOT_CURRENT` | record revoked、expired 或不在窗口内 |
| `APPROVED_REVISION_MISMATCH` | deployed revision 不等于批准 revision |
| `DEPLOYMENT_SCOPE_MISMATCH` | 当前 scope 验证失败 |
| `BUDGET_CONFIG_CONFLICT` | canonical/legacy 冲突或 enforcement 打开 |
| `OTHER_MEMORY_AXIS_ENABLED` | Compression、Question Memory 或 Principal Memory 打开 |
| `DURABLE_METRICS_INCOMPLETE` | 连续 2 个预期 minute bucket 缺失，或 5 分钟检查仍 `data_complete=false` |
| `SHADOW_EXECUTION_ERROR` | Shadow sidecar execution error > 0 |
| `DETERMINISTIC_INTERVIEW_REGRESSION` | 可归因于 Shadow 的业务错误 |
| `CONFIGURATION_DRIFT` | approved snapshot 与当前配置不一致 |

### 7.2 样本达到 200 后的统计 hard stop

```text
observed_error_rate - baseline_error_rate > 0.005
```

或：

```text
observed_p95_latency_ms > baseline_p95_latency_ms * 1.20
```

### 7.3 Continue Observation 条件

- follow-up 样本 < 200；
- warm-up 样本 < 20；
- baseline 缺失或不可比；
- control cohort 不完整；
- metrics bucket 仍可恢复但当前不足；
- 无 hard stop，但窗口不足以形成统计结论。

---

## 8. 观察节奏

### 8.1 Warm-up

```text
effective traffic=min(0.1%, approved cap)
minimum duration=30 minutes
minimum follow-up samples=20
```

检查点：

- 第一个 follow-up；
- 第 5 个 follow-up；
- 第 20 个 follow-up；
- 每 15 分钟一次，直到 warm-up 结束。

### 8.2 Approved-cap observation

只有 warm-up PASS 后才可提升到批准上限，且永不超过 1%。

检查节奏：

- 第一小时每 15 分钟；
- 第 2～6 小时每小时；
- 之后每 4 小时；
- scheduled end 前 30 分钟；
- emergency stop、rollback 和 close 后立即检查。

### 8.3 最低充分性

```text
window duration >= 24 hours
follow-up sample count >= 200
hard stop count = 0
data_complete=true
rollback_verified=true
configuration_restored=true
```

时间本身不是充分证据。

---

## 9. 任务依赖图

```text
Task 0 基线和所有权审计
  → Task 1 Production schema 表征测试
  → Task 2 离线 sanitizer/evaluator
  → Task 3 stop/ramp/window 状态契约
  → Task 4 runbook、隐私与恢复文档
  → Task 5 仓库 acceptance 与完整回归
  → Task 6 新 RC、manifest、PENDING bundle 与发布
  → Task 7 五方外部审批
  → Task 8 revision-bound change preflight
  → Task 9 0.1% warm-up
  → Task 10 approved-cap observation
  → Task 11 scheduled close / emergency rollback
  → Task 12 聚合结果、隐私审计与三态判定
  → Task 13 证据闭环与下一阶段边界
```

Task 7 是外部 hold point。Task 8～Task 13 不得在 Task 7 完成前执行。

---

## Task 0：基线、文件所有权和审批冻结

**Goal:** 确认当前基线、保护用户 dirty worktree，并冻结审批前实现范围。

**Read:**

- `docs/memory-production-budget-shadow-runbook.md`
- `docs/memory-production-shadow-approval-record-contract.md`
- `docs/memory-production-shadow-change-preflight.md`
- `docs/memory-production-shadow-evidence-manifest.md`

**Step 1：记录基线**

```powershell
git rev-parse HEAD
git status --short
git rev-list --left-right --count origin/master...HEAD
```

`PUBLISHED_BASELINE_AT_PLAN_AUTHORING` 固定为 `b1559da`，`INITIAL_PLAN_COMMIT` 固定为 `87f2568`；`EXECUTION_START_HEAD` 必须取本步骤的实际输出。Task 1～Task 5 还会产生新的审批 revision，因此 Task 0 不得断言当前 `HEAD` 仍等于任一历史基线。

**Step 2：文件所有权**

记录用户已有前端、CSS、Hallmark、DESIGN 和测试改动。后续始终使用：

```powershell
git add -- <exact task paths>
```

禁止使用 `git add .`、`git clean`、`git reset --hard` 或覆盖用户文件。

**Step 3：审批冻结规则**

在 Task 6 发布新 RC 前，当前 `b1559da` PENDING 包仅作历史基线，不提交正式五方审批。Task 6 后任何 tracked 变更都使新 approval packet 失效。

**Exit gate:**

```text
BASELINE_OWNERSHIP_AUDIT=PASS
APPROVAL_STATUS=PENDING
PRODUCTION_OBSERVATION=NOT_RUN
```

---

## Task 1：Production 聚合 schema 表征测试

**Goal:** 先证明现有 Staging evaluator 不能表达 production window，再定义新契约。

**Create:**

- `tests/test_memory_production_budget_shadow_observation.py`
- `tests/fixtures/memory_production_budget_shadow/`，仅合成聚合 fixture

**Step 1：写失败表征**

测试现有 Staging artifact 的以下限制：

- 强制 `data_category=synthetic`；
- 强制 `provider_calls=0`；
- 强制 `production_observation=NOT_RUN`；
- 不包含 approval/window/traffic/rollback 生产语义。

**Step 2：定义合法 fixture**

至少包括：

- PASS candidate；
- 少于 200 样本；
- warm-up 少于 20；
- error delta > 0.5 percentage points；
- P95 delta > 20%；
- traffic cap exceeded；
- approval expired/revoked；
- revision/scope mismatch；
- metrics bucket 缺失；
- provider input changed；
- privacy hit；
- other memory axis enabled；
- rollback/config restore 失败；
- forbidden/private key 输入；
- DSN、Prompt、answer 和高基数 locator 输入。

**Step 3：schema version**

固定：

```text
memory-production-budget-shadow-aggregate-input-v1
memory-production-budget-shadow-observation-v1
```

不得创建未经 Spec 定义的新 `MEM-*` requirement ID。

**Test:**

```powershell
F:\python3.11\python.exe -m pytest -q `
  tests/test_memory_production_budget_shadow_observation.py
```

**Exit gate:** 失败测试准确证明 production contract 尚未实现。

---

## Task 2：实现离线 sanitizer 和 Production evaluator

**Goal:** 从仓库外聚合输入生成安全 observation，并做三态判定。

**Create:**

- `scripts/memory_production_budget_shadow_observation.py`
- `scripts/memory_production_budget_shadow_acceptance.py`
- `tests/test_memory_production_budget_shadow_acceptance.py`

**Modify:**

- `tests/test_memory_production_budget_shadow_observation.py`

**Step 1：输入 validator**

要求：

- JSON object；
- 精确 schema；
- allowlisted keys；
- 数值有限且非负；
- traffic `0 <= actual <= approved <= 1.0`；approved cap 本身必须大于 0，实际为 0 时只能形成 `CONTINUE_OBSERVATION` 或 `BLOCKED`，不能 PASS；
- revision 为 40 位 lowercase Git SHA；
- 不包含 private/operational keys；
- 不包含连接串、secret、Prompt/answer/resume/report；
- 不允许额外自由文本 label。

**Step 2：sanitizer**

输出只保留布尔、计数、比率、低基数 bucket、stable gate codes 和公开 revision。对未知 key 必须拒绝，而不是静默保留。

**Step 3：evaluator**

优先级固定：

```text
hard stop → BLOCKED
无 hard stop但证据不足 → CONTINUE_OBSERVATION
所有门禁满足 → PASS
```

失败输出中不得同时出现 `PASS`。

**Step 4：CLI**

建议接口：

```powershell
F:\python3.11\python.exe `
  -m scripts.memory_production_budget_shadow_observation `
  --aggregate-input '<outside-repository-path>' `
  --output '<outside-repository-sanitized-path>'

F:\python3.11\python.exe `
  -m scripts.memory_production_budget_shadow_acceptance `
  --observation '<outside-repository-sanitized-path>'
```

工具不得写生产配置或连接生产服务。

**Test:**

```powershell
F:\python3.11\python.exe -m pytest -q `
  tests/test_memory_production_budget_shadow_observation.py `
  tests/test_memory_production_budget_shadow_acceptance.py
```

**Suggested commit:**

```text
feat(memory): add production budget shadow evidence evaluator
```

---

## Task 3：实现 warm-up、ramp、window 和 stop 状态契约

**Goal:** 让 operator 在每个检查点得到 deterministic 行动，不依赖自由文本判断。

**Create:**

- `scripts/memory_production_budget_shadow_window.py`
- `tests/test_memory_production_budget_shadow_window.py`

**Step 1：状态机**

```text
PENDING_APPROVAL
  → PREFLIGHT_VERIFIED
  → WARM_UP
  → OBSERVING
  → STOPPING
  → CLOSED
```

任何 hard stop 从 `WARM_UP/OBSERVING` 进入 `STOPPING`。`CLOSED` 不得返回 `OBSERVING`。

**Step 2：deterministic actions**

输出动作只能是：

```text
HOLD
START_WARM_UP
KEEP_WARM_UP
RAMP_TO_APPROVED_CAP
STOP_NOW
CLOSE_SCHEDULED
```

**Step 3：窗口检查**

每次决策重新检查：

- approval still current；
- current time inside window；
- revision/scope/config match；
- actual traffic 不超过 cap；
- metrics complete；
- hard stop count。

不缓存 preflight 时的 approval 状态。

**Step 4：状态 artifact**

只输出状态、动作、聚合计数和 gate code；不输出外部 record、digest、部署 locator 或时间系统凭据。

**Test:**

```powershell
F:\python3.11\python.exe -m pytest -q `
  tests/test_memory_production_budget_shadow_window.py
```

**Suggested commit:**

```text
feat(memory): add production shadow window gates
```

---

## Task 4：更新 Runbook、隐私审计和回滚说明

**Goal:** 把机器契约映射为可执行操作步骤。

**Modify:**

- `docs/memory-production-budget-shadow-runbook.md`
- `docs/memory-production-shadow-approval-request.md`
- `docs/memory-production-shadow-approval-record-contract.md`，仅在字段契约确需澄清时
- `docs/memory-shadow-observability-runbook.md`

**Create:**

- `docs/memory-production-budget-shadow-observation-contract.md`
- `docs/memory-production-budget-shadow-acceptance-contract.md`
- `tests/test_memory_production_budget_shadow_docs.py`

**Step 1：Runbook 加入固定节奏**

- 0.1% warm-up；
- 20 样本和 30 分钟；
- approved cap ≤1%；
- 检查 cadence；
- depth ≥2 remote verification；
- scheduled close 先恢复 disabled；
- `CONTINUE_OBSERVATION` 需要新 approval。

**Step 2：隐私清单**

文档和测试必须禁止：

```text
prompts
answers
resumes
reports
session/principal/fact/question/message/artifact IDs
approval/deployment/ticket/approver digests
credentials
DSNs
provider payloads
```

**Step 3：回滚 owner**

明确 primary operator、independent rollback owner、change owner、privacy/security incident contact 均必须在窗口内可达，但仓库不记录其身份 locator。

**Step 4：production-code 修改重验规则**

任何 Shadow 期间 production-code 修改都必须：

```text
STOP_NOW
→ disabled
→ focused tests
→ full regression
→ new RC
→ new five-role approval
```

**Test:**

```powershell
F:\python3.11\python.exe -m pytest -q `
  tests/test_memory_production_budget_shadow_docs.py
```

**Suggested commit:**

```text
docs(memory): define production budget shadow operations
```

---

## Task 5：审批前 Acceptance Gate

**Goal:** 在提交五方审批前证明所有生产结果工具只观察、不改变配置。

**Create:**

- `scripts/memory_production_budget_shadow_readiness.py`
- `tests/test_memory_production_budget_shadow_readiness.py`
- `docs/memory-production-budget-shadow-readiness-evidence.json`

**Step 1：readiness 输入**

验证：

- production observation schema/evaluator/window state tests 通过；
- default config disabled；
- `consume` 仍被 preflight 拒绝；
- Write/Read production unauthorized；
- current production observation NOT_RUN；
- runbook/contract 内容一致；
- repository Pending example 仍只产生两个预期 gate；
- no production connections or external record in Git；
- current tree can be rebuilt from revision。

**Step 2：成功输出**

```text
PRODUCTION_BUDGET_SHADOW_TOOLING=READY_FOR_REVIEW
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
CONFIGURATION_CHANGED=false
PRODUCTION_OBSERVATION=NOT_RUN
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

**Step 3：失败输出**

任何失败必须输出稳定 `GATE=*`，不得同时输出 READY。

**Test:**

```powershell
F:\python3.11\python.exe -m pytest -q `
  tests/test_memory_production_budget_shadow_readiness.py
```

**Suggested commit:**

```text
feat(memory): gate production budget shadow readiness
```

---

## Task 6：新 RC、完整回归和 PENDING 审批包

**Goal:** 形成真正可审批、包含全部 production-result 工具的 immutable revision。

**Step 1：完整回归**

从新 commit 创建 clean detached worktree，运行：

```powershell
F:\python3.11\python.exe -m pytest -q
F:\python3.11\python.exe -m compileall -q app scripts tests
F:\python3.11\python.exe -m scripts.memory_operational_shadow_acceptance
F:\python3.11\python.exe -m scripts.memory_production_shadow_approval_packet
F:\python3.11\python.exe -m scripts.memory_production_budget_shadow_readiness
git diff --check
git status --short
```

**Step 2：重新生成 pre-approval manifest**

将新增的 observation contract、acceptance contract 和 readiness evidence 加入固定 allowlist。保持：

```text
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

**Step 3：远端复现**

- push 新 RC；
- 从 GitHub 新 clone；
- depth 1 必须 fail-closed；
- depth 2 或 full clone 必须 manifest VERIFIED；
- remote clone negative preflight 必须只输出 repository-template 两个 gate。

**Step 4：生成新 PENDING bundle**

文件名必须使用新 revision，不得复用 `b1559da`。生成 ZIP、metadata 和 `.sha256`，metadata 包含 `minimum_clone_depth=2`。

**Step 5：旧包处理**

新包验证成功后，将旧 `b1559da` 包标为 superseded 并从待上传目录移除。不得删除 Git 历史或已提交 evidence。

**Exit gate:**

```text
NEW_RC=REPRODUCIBLE
REMOTE_MANIFEST=VERIFIED
APPROVAL_PACKET=READY_FOR_REVIEW
APPROVAL_STATUS=PENDING
PRODUCTION_OBSERVATION=NOT_RUN
```

---

## Task 7：五方外部审批

**Goal:** 在独立变更系统中取得 exact-revision、exact-window、exact-scope 的批准。

**Repository writes:** None。

**Step 1：上传**

上传 Task 6 的 ZIP、metadata、checksum；登记 repository、revision、manifest bundle SHA、traffic cap、window、metrics destination、rollback owner 和 incident channel。

**Step 2：五个独立角色**

```text
change_owner
operations
privacy
security
fairness
```

每个角色必须有独立 `APPROVED`、approver reference hash 和 timezone-aware decision time。

**Step 3：独立 digest**

- expected record SHA 来自 change-management system；
- expected deployment scope SHA 来自 deployment inventory；
- 不得从同一本地 JSON 自算 expected digest。

**Step 4：冻结**

批准后代码、依赖、runbook、schema 和默认配置全部冻结。任何变更使审批失效。

**Exit gate:** 外部正式 record 存在，五方批准完整，窗口尚未开始或正处于批准窗口；record 仍不进入 Git。

---

## Task 8：Production Change Preflight

**Goal:** 在不改变配置的前提下验证外部批准和仓库门禁。

**Step 1：运行**

```powershell
F:\python3.11\python.exe `
  -m scripts.memory_production_shadow_change_preflight `
  --approval-record '<outside-repository-record>' `
  --expected-record-sha256 '<trusted-change-system-value>' `
  --expected-deployment-scope-sha256 '<trusted-inventory-value>' `
  --current-revision '<exact-deployed-revision>'
```

**Step 2：唯一通过输出**

```text
PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS
EXTERNAL_APPROVAL_RECORD=VERIFIED
REQUESTED_PHASE=BUDGET_SHADOW_ONLY
CONFIGURATION_CHANGED=false
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

**Step 3：配置前快照**

在受信部署系统记录：

- exact revision；
- scope match boolean；
- 当前配置安全矩阵；
- deterministic Interview health；
- metric destination reachable；
- rollback owner reachable；
- automatic stop available。

仓库 evidence 只保存 boolean/count，不保存 locator/digest。

**Exit gate:** preflight PASS，配置仍未改变。

---

## Task 9：0.1% Warm-up

**Goal:** 在最小流量上验证运行时不变量。

**Step 1：应用单轴 delta**

通过批准的 deployment system 设置：

```text
MEMORY_BUDGET_MODE=shadow
MEMORY_BUDGET_SHADOW_ENABLED=true
```

并显式保持其他安全值 disabled/false。

**Step 2：sticky canary**

effective traffic：

```text
min(0.1%, approved cap)
```

selector 必须 sticky，不能按候选人内容、语言、分数、角色或敏感属性选择。

**Step 3：检查点**

在第 1、5、20 个 follow-up 和每 15 分钟运行 window decision。

**Step 4：晋级条件**

```text
duration >= 30 minutes
followup samples >= 20
all immediate hard stops = 0
data_complete=true
deterministic Interview health=green
```

否则保持 warm-up、STOP_NOW 或关闭为 `CONTINUE_OBSERVATION`。

---

## Task 10：Approved-cap Observation

**Goal:** 在不超过 1% 的批准流量内取得充分生产样本。

**Step 1：提升流量**

只在 Task 9 PASS 后提升到 approved cap。实际流量不得超过 record 中的 cap，也不得超过 1%。

**Step 2：持续检查**

按 §8 节奏检查：

- approval current；
- revision/scope/config match；
- traffic cap；
- hard stop；
- bucket completeness；
- error/latency；
- deterministic path health。

**Step 3：只观察**

Budget Shadow 可以记录 hypothetical would-select/would-drop/fallback，不得 crop、truncate、compress、replace 或 reorder Provider input。

**Step 4：低样本 bucket**

每个 coarse language/path bucket 单独记录 sample count。少于 30 标为 `INSUFFICIENT`，禁止外推。

---

## Task 11：Scheduled Close 或 Emergency Rollback

**Goal:** 先恢复安全配置，再生成结论。

**Step 1：关闭条件**

- scheduled end；
- hard stop；
- approval revoked/expired；
- config drift；
- operator manual stop；
- metrics unavailable；
- production-code defect。

**Step 2：恢复配置**

```text
MEMORY_BUDGET_MODE=disabled
MEMORY_BUDGET_SHADOW_ENABLED=false
```

确认所有 enforcement、Compression、Question Memory 和 Principal Memory 仍 disabled。

**Step 3：关闭验证**

- 新 Shadow event 停止；
- deterministic Interview health green；
- worker/listener residue = 0；
- temporary relation residue = 0；
- committed defaults unchanged；
- immutable evidence retained。

**Step 4：禁止操作**

不得删除 migration、tombstone、历史 artifact 或 approval evidence 来“回滚”。

---

## Task 12：聚合结果、隐私审计与三态判定

**Goal:** 从受信系统导出聚合 summary，生成安全结论。

**Step 1：导出到仓库外路径**

Operations 导出 `memory-production-budget-shadow-aggregate-input-v1`。禁止直接把指标 backend dump 放进仓库。

**Step 2：运行 sanitizer**

未知字段、private key、connection string、free-text label 或高基数值均 fail-closed。

**Step 3：运行 acceptance**

```powershell
F:\python3.11\python.exe `
  -m scripts.memory_production_budget_shadow_acceptance `
  --observation '<sanitized-outside-repository-path>'
```

**Step 4：隐私/安全复审**

Privacy 和 Security 复核 sanitized artifact，确认：

- private artifact hit = 0；
- external bindings 未泄漏；
- no Prompt/answer/resume/report；
- no locator；
- no connection/secret；
- no other memory axis；
- configuration restored。

**Step 5：三态输出**

PASS：

```text
PRODUCTION_BUDGET_SHADOW=PASS
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

BLOCKED：

```text
PRODUCTION_BUDGET_SHADOW=BLOCKED
GATE=<stable-code>
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

证据不足：

```text
PRODUCTION_BUDGET_SHADOW=CONTINUE_OBSERVATION
GATE=<insufficient-evidence-code>
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
NEW_APPROVAL_WINDOW_REQUIRED=true
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

---

## Task 13：证据闭环与下一阶段边界

**Goal:** 保留审批前和观察后两条不可混淆的证据链。

**Step 1：保持 pre-approval evidence immutable**

不修改旧 PENDING manifest 的语义，不把它改成 production PASS。

**Step 2：生成 post-observation evidence**

建议新增：

- `docs/memory-production-budget-shadow-observation.json`，仅 sanitized aggregate；
- `docs/memory-production-budget-shadow-acceptance.md`，记录结果和限制；
- 独立 post-observation manifest，避免覆盖 pre-approval manifest。

只有通过 Privacy/Security artifact audit 的聚合结果才能进入 Git。外部 approval record 和 digests 继续留在变更系统。

**Step 3：完整回归**

生产窗口结束后的 repository-only evidence commit 必须从 clean worktree 通过：

```powershell
F:\python3.11\python.exe -m pytest -q
F:\python3.11\python.exe -m compileall -q app scripts tests
git diff --check
```

**Step 4：下一阶段**

即使 Budget Shadow PASS，也只允许起草新的 Principal Write Shadow production plan 和新的审批包。不得复用本阶段 approval record。

最终继续输出：

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

---

## 10. 晋级门禁总表

| 从 | 到 | 必须门禁 |
|---|---|---|
| `b1559da` baseline | Production tooling RC | Tasks 1～5、full regression、new manifest |
| Tooling RC | 外部审批 | remote depth-2 verify、PENDING bundle、no secrets |
| PENDING | APPROVED record | 五方独立批准、revision/scope/window/traffic binding |
| APPROVED | Preflight PASS | independent record SHA、independent scope SHA、safe defaults |
| Preflight PASS | Warm-up | 单轴配置、sticky ≤0.1%、automatic stop ready |
| Warm-up | Approved cap | ≥30 分钟、≥20 samples、hard stop=0 |
| Approved cap | Scheduled close | ≤1%、窗口内、持续 metrics/config/approval checks |
| Closed | PASS | ≥24 小时、≥200 samples、hard stop=0、rollback/config restored |
| Closed | CONTINUE | 无 hard stop但证据不足；新窗口审批必需 |
| 任意运行态 | BLOCKED | 任一 hard stop；立即 disabled |
| Budget PASS | Write Shadow planning | 新 Plan、新 evidence、新五方审批；不自动执行 |

---

## 11. 回滚矩阵

| 故障 | 立即行动 | 业务路径 | 后续证据 |
|---|---|---|---|
| Approval absent/pending | HOLD | deterministic 继续 | 两个稳定 approval gate |
| Approval expired/revoked | STOP_NOW | deterministic 继续 | `APPROVAL_NOT_CURRENT` |
| Revision/scope mismatch | STOP_NOW | deterministic 继续 | mismatch boolean/code |
| Traffic > approved cap | STOP_NOW | deterministic 继续 | max traffic + gate |
| Mandatory content loss | STOP_NOW | deterministic 继续 | aggregate count；隐私审查 |
| Provider input mutation | STOP_NOW | deterministic 继续 | aggregate count；Security review |
| Known over-limit call | STOP_NOW | deterministic 继续 | aggregate count |
| Privacy artifact hit | STOP_NOW；启动隐私事件流程 | deterministic 继续 | 最小化 incident evidence |
| Metrics incomplete | STOP_NOW 或 CONTINUE，按连续缺失门禁 | deterministic 继续 | missing bucket count |
| Error/latency regression | STOP_NOW | deterministic 继续 | aggregate deltas |
| Other memory axis enabled | STOP_NOW；全部 memory disabled | deterministic 继续 | config matrix boolean |
| Production-code defect | STOP_NOW；新 RC/新审批 | deterministic 继续 | regression evidence |
| Warm-up 样本不足 | 关闭或继续 warm-up，不 ramp | deterministic 继续 | CONTINUE |
| Final 样本不足 | scheduled close；新审批 | deterministic 继续 | CONTINUE |
| Rollback verification failure | 保持 incident open；禁止下一阶段 | deterministic 继续 | `ROLLBACK_NOT_VERIFIED` |

---

## 12. 风险登记

| 风险 | 缓解 | 必须证据 |
|---|---|---|
| 审批绑定旧 revision | Task 6 后才提交审批；exact SHA | approval revision match |
| depth-1 clone 误报 tamper | 文档固定 depth ≥2 | remote clone drill |
| Staging runner 被误用于生产 | 独立 production schema/evaluator | characterization tests |
| Runner 直接连接生产 | offline aggregate input only | source/config audit |
| 多轴同时改变 | config matrix + repeated window checks | single-axis boolean |
| 0.1% warm-up 被跳过 | deterministic state machine | warm-up samples/duration |
| 样本不足误判 PASS | three-state evaluator | CONTINUE gate |
| 窗口过期仍运行 | operation-time approval recheck | approval-current boolean |
| 流量 selector 不公平 | sticky、不按内容/属性选择 | fairness review |
| 指标泄露个人数据 | allowlist sanitizer + artifact audit | privacy hit=0 |
| 外部 digest 进入 Git | repository schema 禁止 | source/artifact scan |
| PENDING evidence 被改写 | separate post-observation artifact | immutable pre-manifest |
| 生产失败影响主业务 | sidecar observation + fail-open deterministic path | health comparison |
| 代码热修复绕过审批 | STOP、新 RC、新审批 | revision-bound record |
| Budget PASS 被扩展解释 | fixed terminal output | Write/Read/Consumption blocked |

---

## 13. Definition of Done

本计划完成必须同时满足：

1. Production aggregate input/output schema 已实现并测试；
2. 离线 sanitizer 不连接生产系统；
3. Production evaluator 支持 PASS/BLOCKED/CONTINUE；
4. hard stop 优先级稳定且失败不输出 PASS；
5. warm-up/ramp/window/stop 状态机已实现；
6. runbook 包含 0.1%、20 samples、30 minutes、≤1%、24 hours、200 samples；
7. private/operational fields 被 source 和 artifact tests 阻断；
8. 新 RC 完整回归通过；
9. 新 pre-approval manifest 和 PENDING bundle 可跨 LF/CRLF 验证；
10. full/depth-2 remote clone 验证通过；
11. depth-1 clone 继续 fail-closed；
12. 五方外部审批绑定 exact final revision；
13. production change preflight PASS 前配置未改变；
14. Production Budget Shadow 只改变一个配置轴；
15. effective traffic 从 ≤0.1% warm-up 开始且永不超过 1%；
16. approval、revision、scope、window、traffic 和 config 在运行中重复检查；
17. hard stop 能立即恢复 disabled；
18. scheduled end 无条件恢复 disabled；
19. sanitized aggregate artifact 通过 Privacy/Security review；
20. observation 输出三态之一且无矛盾状态；
21. pre-approval PENDING evidence 未被改写；
22. external approval record 和 digests 未进入 Git；
23. 用户 dirty worktree 未丢失或被错误提交；
24. Production Budget Shadow 结束后完整回归通过；
25. 最终仍明确阻断 Write Shadow、Read Shadow 和 Consumption。

---

## 14. Traceability

本计划不创建新的 `MEM-*` requirement ID。它把以下既有契约落实为生产操作义务：

| 来源 | 本计划覆盖 |
|---|---|
| Memory Optimization Spec 的 Budget/Observability/Security/Testing 要求 | Tasks 1～5、12～13 |
| `memory-production-shadow-approval-record-contract.md` | Tasks 7～8 |
| `memory-production-shadow-change-preflight.md` | Task 8 |
| `memory-production-budget-shadow-runbook.md` | Tasks 4、9～11 |
| pre-approval evidence manifest v2 | Tasks 5～6 |
| Operational Shadow acceptance | Tasks 5～6 |
| Principal Memory Consumption Spec draft boundary | Decisions 10、12，Task 13 |

Production-specific schema names是实现契约，不是新的产品 requirement ID。若未来要把 Production Budget Shadow 或 Principal Write Shadow 变成规范性产品要求，必须更新 Spec 版本并分配正式 requirement ID。

---

## 15. 最终稳定输出

### 15.1 工具准备完成、仍待审批

```text
PRODUCTION_BUDGET_SHADOW_TOOLING=READY_FOR_REVIEW
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
CONFIGURATION_CHANGED=false
PRODUCTION_OBSERVATION=NOT_RUN
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

### 15.2 生产观察 PASS

```text
PRODUCTION_BUDGET_SHADOW=PASS
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

### 15.3 生产观察证据不足

```text
PRODUCTION_BUDGET_SHADOW=CONTINUE_OBSERVATION
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
NEW_APPROVAL_WINDOW_REQUIRED=true
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

### 15.4 生产观察阻断

```text
PRODUCTION_BUDGET_SHADOW=BLOCKED
GATE=<stable-code>
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

本阶段任何成功输出都不得包含：

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=ENABLED
```

---

## 16. 最终边界

本计划完成后最多可以证明：

```text
Production Budget Shadow 在精确批准 revision、scope、traffic 和 window 内运行
+ 未改变 Provider input 或 deterministic Interview 业务语义
+ 聚合指标完整且不泄露个人信息
+ hard stop、scheduled close 和 rollback 可执行
+ 已形成 PASS/BLOCKED/CONTINUE 的审计证据
```

它仍然不能证明或授权：

```text
Principal Memory Write Shadow 可以进入生产
+ Principal Memory Read Shadow 可以进入生产
+ personal facts 可以注入 Prompt
+ 历史事实可以参与评分、报告或招聘判断
+ Long-term Memory consumption 可以开启
```

Budget Shadow PASS 之后的下一步只能是起草一个新的 Principal Write Shadow production plan，并重新执行独立隐私、安全、公平性、运维和 Change Owner 审批。
