# Interview Agent 记忆系统运行 Shadow 与晋级门禁实施计划

**Plan revision:** v1.1，基于
`docs/interview-agent-memory-system-optimization-spec.md` v1.1.1-draft、
`docs/memory-validation-long-term-foundation-acceptance.md`、
`docs/memory-validation-operational-evidence.json`，以及
`2026-07-30-memory-validation-and-long-term-memory-foundation.md` 的完成结果。

**v1.1 review amendments:** 明确单实例 PostgreSQL 可以在严格 schema/table-prefix、
fingerprint、数据和清理边界下作为单用户 Staging，但不等价于生产级实例隔离；把建议提交
序列改为依赖自洽、逐提交可编译的参考序列；规定低样本路径只输出
`CONTINUE_OBSERVATION`；把 Read Shadow 运行时零注入检查收敛为 canonical digest，深度
差异分析仅用于 debug/Staging 诊断；要求任何 Shadow 期间的 production-code 修改都重新
执行 focused tests 和受影响的 Staging 阶段；把 Write→Read 晋级显式绑定 Task 6 proposal
quality gate；并把“可实时、无惩罚地禁用长期记忆”提升为 Consumption Spec 的强制 UX
契约。

**文档类型：** Implementation Plan / How-to + Reference。

**目标读者：** 后端工程师、Agent 工程师、数据工程师、SRE、QA、
安全与隐私评审人员、产品负责人和技术负责人。

**用户目标：** 把已经通过仓库与隔离测试验收的记忆系统固化为可复现的
Release Candidate，在隔离 Staging 中依次运行 Budget Shadow、Principal Memory
Write Shadow 和零注入 Read Shadow，完成同意、撤回、删除与旧备份恢复演练，
形成是否可以申请生产 Shadow 的证据；本计划不实现长期记忆 Prompt consumption。

> **执行边界：** 本文是下一阶段的实施与操作计划，不自动授权提交当前 dirty
> worktree、连接生产数据库、处理真实候选人数据、调用真实 LLM/Embedding
> Provider、启用生产 Shadow、扩大生产流量、开启 Budget enforcement、启用
> Question Memory consumption，或把 Principal Memory 注入 Prompt、评分、报告和
> 公共知识库。每一种外部环境变更都必须有单独的目标环境、观察窗口、负责人和
> 回滚批准。

---

## 1. 阶段结论与推荐顺序

当前最重要的工作不是继续增加 Fact Store 功能，也不是直接开启长期记忆消费，
而是把现有成果变成可复现、可观察、可停止、可审计的 Shadow Release Candidate。

推荐执行顺序固定为：

~~~text
变更所有权审计
  → Release Candidate 固化
  → 提交后回归复现
  → 隔离 Staging 部署与迁移验证
  → Budget Shadow
  → Principal Memory Write Shadow
  → Proposal 人工复核
  → Principal Memory Read Shadow（零注入）
  → Consent / Revoke / Delete 演练
  → 旧备份恢复 + Tombstone Replay
  → 聚合指标与隐私审计
  → Shadow 阶段验收
  → 单独编写 Consumption Spec（不实现）
~~~

任何阶段失败，都返回当前已验证的稳定路径。不得为了赶进度跳过 Budget Shadow、
身份/Consent、删除恢复或隐私门禁。

---

## 2. 当前已验收基线

本计划开始时，仓库已经得到以下机器验收状态：

~~~text
READY_FOR_MEMORY_VALIDATION_SHADOW
LONG_TERM_MEMORY_WRITE_SHADOW_READY
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

已记录的隔离验证证据包括：

| 验证项 | 已验收结果 |
|---|---:|
| Focused memory suite | 339 passed，12 skipped，0 failed |
| Full Python | 1441 passed，162 skipped，0 failed |
| Live PostgreSQL runtime | 37 passed，24 deselected |
| Frontend build | 通过，4587 modules transformed |
| Full browser | 53 passed，21 configured skips，0 failed |
| 最新 migration | `principal_memory_v1` |
| 隔离 migration relations | 28，cleanup verified |
| Knowledge corpus | `memory-p1-zh-v3`，31 chunks |
| 长上下文 hard invariant | 1.0 |
| Atomic fact recall | 1.0 |
| Unsupported atomic claim rate | 0.0 |
| 测试监听器残留 | 0 |
| 隔离 PostgreSQL 测试关系残留 | 0 |

当前实现已经具备：

- Budget Shadow validate-only preflight 和自动停止条件；
- PostgreSQL minute/hour 聚合指标；
- Principal Identity、版本化 Consent 和严格 taxonomy；
- In-memory/PostgreSQL Principal Fact Store；
- write-only proposal effect 和默认空 extractor；
- confirm/reject/supersede/revoke/expire/delete 生命周期；
- bounded read-shadow；
- Prompt、问题、评分、报告零注入保护；
- Principal Memory 与公共 Knowledge Corpus 防火墙；
- Session/Principal 删除和旧备份 Tombstone Replay；
- 聚合型验收制品隐私审计。

### 2.1 当前基线的限制

现有证据仍然不能证明：

- 当前未提交改动可以由一个 Git revision 完整重建；
- Staging 中真实进程、队列、连接池和持续运行窗口没有漂移；
- Budget estimator 在运行分布下长期稳定；
- Write Shadow proposal 在足够样本上具有可接受的精度；
- Read Shadow 选择在真实运行分布下相关且不会产生过度个性化；
- 撤回 Consent 后，运行中的 effect、缓存和恢复任务都能及时停止；
- 旧备份恢复后的删除重放在部署环境中可操作；
- 正式 Principal 身份和候选人可见 Consent UX 已经存在；
- 生产 Shadow 或长期记忆消费已经获得授权。

### 2.2 变更可追溯性缺口

验收记录中的 `9132cf3` 是执行开始时的基础 revision。本阶段新增的大量文件仍在
dirty worktree 中，因此不能把该 revision 描述成完整 Release Candidate。

进入任何共享 Staging 前，必须先完成 Task 0 和 Task 1，使验收证据指向实际包含
本阶段代码的提交。

---

## 3. 本阶段目标和明确排除

### 3.1 本阶段包含

- 建立当前 dirty worktree 的文件所有权与提交边界；
- 形成可复现的 Memory Shadow Release Candidate；
- 在提交后重新执行 Python、PostgreSQL、Frontend、Browser 和 acceptance；
- 建立隔离 Staging preflight、配置快照、迁移验证、清理和回滚程序；
- 运行 Budget Shadow，只观察、不执行预算裁剪或压缩；
- 运行 Principal Memory Write Shadow，只生成 proposed facts；
- 建立 proposal 的受控人工复核流程和聚合质量报告；
- 运行 Principal Memory Read Shadow，只产生 would-select 聚合结果；
- 证明 Read Shadow 不改变 Provider Context、Prompt、问题、评分、报告或响应；
- 运行 Grant、Confirm、Reject、Revoke、Expire、Session Purge、Principal Purge；
- 运行旧备份恢复后的 operator tombstone replay；
- 建立聚合指标、停止门禁、告警和 Shadow observation record；
- 执行隐私、跨 Principal 隔离、Prompt Injection 和 Knowledge Firewall 复审；
- 输出 Shadow 阶段 acceptance runner 和验收记录；
- 起草 Principal Memory Consumption Spec，但不实现消费路径。

### 3.2 本阶段明确排除

- 自动或直接提交整个 dirty worktree；
- 丢弃、覆盖或改写用户已有变更；
- 生产 migration；
- 生产流量 Shadow；
- 真实候选人长期记忆试点；
- 未经单独批准的真实 Provider 调用；
- Budget enforcement；
- Context Compression consumption；
- Question Memory production consumption；
- Principal Memory Prompt injection；
- 使用历史个人事实改变提问、追问、评分、报告、招聘判断或推荐；
- Candidate Answer、Resume 或 Principal Fact 写入公共 pgvector；
- Principal Fact embedding、跨 Principal 相似度检索或自动身份合并；
- 自动把 model-proposed fact 晋升为 active；
- 依据历史评分校准当前评分；
- 自动训练、微调、在线强化学习或公共知识库自增长；
- 候选人身份/Consent UI 的完整产品实现；
- 1% production consumption canary。

---

## 4. 固定决策

### Decision 1：先固化 Release Candidate，再运行 Shadow

Shadow 证据必须绑定一个包含全部相关代码、migration、tests、runbook 和验收器的
Git revision。不得从无法复现的 dirty worktree 部署共享 Staging。

### Decision 2：三种 Shadow 严格串行晋级

顺序固定为 Budget Shadow → Write Shadow → Read Shadow。每次只改变一个主要变量，
上一阶段必须形成独立 observation record 并通过退出门禁。

### Decision 3：Staging 通过不等于生产授权

本计划只允许输出“可以申请生产 Shadow 审批”，不得输出
`PASS_FOR_PRODUCTION`、`PRODUCTION_READY` 或“可以开启长期记忆消费”。

### Decision 4：Budget Shadow 只观察

合法配置模式是 `disabled`、`shadow`、`enforce`。本阶段只允许使用 `shadow`，
并保持 enforcement、compression consumption 和 Question Memory consumption 关闭。

### Decision 5：Write Shadow 只创建 proposed facts

Write Shadow 不得自动创建 active facts。模型输出必须保持：

~~~text
authority=model_proposed
status=proposed
user_confirmed=false
~~~

测试中需要 active fact 时，只能由显式 trusted test fixture 或模拟用户确认操作创建。

### Decision 6：Read Shadow 永远零注入

Read Shadow 可以计算 would-select，但返回给 Interview Graph 的 Provider Context 必须与
未启用长期记忆时逐项相同。它不能改变问题、评分、报告、Evidence 或 API 响应。

### Decision 7：身份和 Consent 在操作时重新解析

Proposal、Confirm、Read Shadow、Revoke、Delete 和 Replay 都必须使用当前身份和当前
Consent。不得仅依赖 Session 创建时缓存的 Principal 或 Consent 快照。

### Decision 8：Shadow 数据仅使用合成或明确授权数据

默认 observation profile 使用合成 Principal、合成 Session 和固定 fixture。若要使用
内部测试人员数据，必须有独立数据授权、用途、保留期和删除记录。本计划不授权真实
候选人数据。

### Decision 9：聚合指标是唯一常规观察面

常规指标和 acceptance artifact 不得包含 Session、Principal、Fact、Question、
Artifact、Prompt、Answer、Excerpt、Resume、Source Locator、DSN 或 Provider Payload。
受控人工审核原始材料必须是临时、最小权限、不可进入常规日志的独立流程。

### Decision 10：Hard stop 优先于可用性

Cross-principal、Consent 失效、删除失败、公共知识污染、Prompt 变化和隐私审计命中
均为立即停止条件。停止 Shadow 不得中断原有 deterministic 面试业务路径。

### Decision 11：低流量环境以样本覆盖代替等待时间

单用户或低流量 Staging 不以“运行七天”作为充分证据。必须完成固定语言、taxonomy、
并发、撤回、删除、冲突、恢复和故障样本矩阵。

### Decision 12：Consumption 必须有新 Spec 和新授权

本计划的最后一项只能起草 Consumption Spec。任何把 personal fact 注入 Prompt 的代码、
配置、API 或 canary 都不属于本计划。

---

## 5. 安全默认值与合法模式

提交到仓库的默认值必须继续保持：

~~~dotenv
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false
# MEMORY_BUDGET_MODE=disabled
# MEMORY_COMPRESSION_MODE=disabled
# MEMORY_LONG_TERM_MODE=disabled
# MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
# MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
# MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
~~~

### 5.1 Budget 模式

| 模式 | 本阶段是否允许 | 说明 |
|---|---:|---|
| `disabled` | 是 | 默认和回滚状态 |
| `shadow` | 仅经目标环境批准 | 只计算和记录聚合结果 |
| `enforce` | 否 | 本阶段禁止执行预算裁剪 |

### 5.2 Long-term 模式

| 模式 | 本阶段是否允许 | 必要条件 |
|---|---:|---|
| `disabled` | 是 | 默认和回滚状态 |
| `write_shadow` | 仅隔离 Staging | mode + 显式 write gate |
| `read_shadow` | 仅隔离 Staging | mode + 显式 write/read gates |
| `consume` | 不存在且必须拒绝 | 不得降级或兼容处理 |

`read_shadow` 要求 write/read 两个显式 gate 同时开启；这只是允许 proposal 和 would-select
在 Staging 运行，不意味着允许 Prompt consumption。

### 5.3 Trusted-local API

Trusted-local Principal Memory API 默认隐藏。它只能用于隔离测试和受控生命周期演练，
不能作为候选人 API、生产控制面或 Shadow 启用入口。

---

## 6. 任务依赖图

~~~text
Task 0  变更所有权审计与 Release Candidate 固化
  └── Task 1  提交后回归与证据重绑定
        └── Task 2  隔离 Staging、迁移、配置与回滚 preflight
              └── Task 3  Budget Shadow 运行
                    └── Task 4  Budget Shadow 观察与晋级门禁
                          └── Task 5  Principal Write Shadow
                                └── Task 6  Proposal 人工复核与质量门禁
                                      └── Task 7  Principal Read Shadow 零注入

Task 2 + Task 5
  └── Task 8  Consent、生命周期与删除演练

Task 2 + Task 8
  └── Task 9  旧备份恢复与 Tombstone Replay

Task 3 + Task 5 + Task 7
  └── Task 10 聚合观察面板与自动停止告警

Task 6 + Task 7 + Task 8 + Task 9 + Task 10
  └── Task 11 隐私、安全、公平性与 Knowledge Firewall 复审
        └── Task 12 Shadow 阶段 Acceptance Gate
              └── Task 13 Consumption Spec 草案（不实现）
~~~

允许并行的工作仅限：

- Task 8 的测试夹具准备可与 Task 6 并行，但实际删除演练要等待 Write Shadow store 可用；
- Task 10 的聚合查询和文档可与 Task 6/7 并行，但告警阈值必须使用最终门禁；
- Task 13 的风险目录可以提前准备，但正式 Spec 输入必须等待 Task 12 结果；
- 不允许并行编辑同一个 migration registry、配置加载器或 acceptance runner。

---

## 7. 统一验证与证据约定

### 7.1 测试层次

每个任务至少覆盖：

1. 静态配置和源码契约；
2. 单元测试；
3. 服务集成测试；
4. PostgreSQL `pg_runtime` 测试；
5. 故障注入；
6. 隐私制品审计；
7. Staging observation；
8. 最终 acceptance runner。

### 7.2 Observation artifact

常规 observation record 只允许保存：

- schema version；
- Release Candidate revision；
- 环境类别，不记录主机名或连接串；
- 配置 digest；
- 观察窗口；
- 语言/operation/taxonomy 等 allowlisted 聚合维度；
- 样本量；
- 计数、比例、延迟、token 估计、停止 gate；
- `data_complete`；
- `production_observation` 状态。

禁止字段包括：

- DSN、credential、token、cookie；
- Session/Principal/Fact/Message/Question ID；
- Prompt、Answer、Resume、JD、Excerpt、Summary；
- Source Manifest、Source Excerpt digest 或 Artifact Ref；
- Provider 原始请求和响应；
- 可以反向定位到个人的低基数自由文本维度。

### 7.3 两种观察 profile

**Profile A：稳定流量 Staging**

- 连续观察至少 7 天；
- 每条关键路径至少 200 个有效样本；
- Write Shadow 至少 300 个经过受控复核的 proposal；
- 至少一次完整删除和恢复演练。

**Profile B：低流量/单用户 Staging**

- 至少 300 个合成或内部明确授权 Session；
- `zh_hans`、`en`、`mixed` 各至少 100 个；
- 每种允许 Fact taxonomy 都有 positive、negative、conflict、revoke、delete；
- 至少 100 次并发、重放或进程丢失场景；
- 至少 10 次完整 Principal 生命周期演练；
- 至少 3 次旧备份恢复后的 tombstone replay。

仅经过时间而没有足够样本，或仅有样本而没有删除/恢复演练，都不能晋级。

### 7.4 失败输出

任何 runner 或 preflight 失败时必须输出稳定 gate code，并且不得同时输出 READY。
失败日志只报告 gate 类别和聚合计数，不打印触发失败的候选人内容或标识符。

---

## Task 0：变更所有权审计与 Release Candidate 固化

**目的：** 把当前无法由 Git revision 完整复现的 dirty worktree，整理成边界明确的
Memory Shadow Release Candidate，同时保留所有用户已有变更。

**前置条件：** 当前完整回归和 acceptance 仍为绿色；不得执行 reset、checkout、clean
或全仓库无选择 staging。

**建议文件：**

- Create: `docs/memory-shadow-change-ownership.md`
- Create: `docs/memory-shadow-release-candidate.md`
- Create: `scripts/memory_shadow_release_preflight.py`
- Create: `tests/test_memory_shadow_release_preflight.py`
- Modify only if necessary: `.gitignore`

### Step 1：建立文件所有权清单

把工作树文件分为：

1. 上一阶段 Memory Optimization；
2. 本阶段 Validation/Foundation；
3. 与记忆系统共享但不可拆的基础设施；
4. 用户已有、与本阶段无关的改动；
5. 历史 HTML 删除；
6. 前端或设计资产；
7. 生成物和应忽略文件。

清单记录 path、owner phase、是否允许提交、依赖提交和验证命令，不复制文件内容。

### Step 2：生成 scoped diff inventory

Preflight 只输出路径和变更类别，不输出凭据或数据内容。它必须检测：

- 本阶段必需文件是否遗漏；
- 是否意外恢复 `app/test0.html` 至 `app/test4.html` 和 `app/test-help.html`；
- 是否出现未归属的大型二进制或测试数据；
- 是否有 migration、config、privacy blocker、acceptance runner 未纳入提交边界；
- 是否存在与用户文件重叠且无法安全拆分的修改。

### Step 3：按功能拆分提交

以下序列是建议的功能边界，不是必须机械执行的提交顺序：

~~~text
feat(memory): add validation and durable aggregate metrics
feat(knowledge): add memory P1 coverage corpus
feat(principal-memory): add identity consent and fact store
feat(principal-memory): add proposal lifecycle and deletion
feat(principal-memory): add zero-injection read shadow
test(memory): add postgres deletion quality and privacy gates
docs(memory): add threat model runbooks and acceptance evidence
~~~

实际提交前必须逐个确认文件归属，并根据 Task 0 Step 1 的所有权和依赖清单重新验证提交
拓扑。`memory_config.py`、runtime singleton/factory、domain contracts、migration registry
等共享基础设施可以与直接依赖它们的最小功能集合合并提交，避免形成中间不可导入、
不可迁移或无法测试的 revision。

每一个提交都必须自洽，至少满足：

~~~powershell
& 'F:\python3.11\python.exe' -m compileall -q app scripts tests
& 'F:\python3.11\python.exe' -m pytest -q <该提交影响的 focused tests>
~~~

如果建议序列会产生循环提交依赖，允许调整顺序或合并相邻提交；判断标准是每个 revision
都可编译、可导入、migration contract 一致且 focused tests 通过，而不是提交数量。计划中的
建议提交不授权自动提交。

### Step 4：建立 RC record

记录：

- 最终 commit revision；
- 提交列表；
- migration head；
- corpus version/hash；
- Python/Node/PostgreSQL/Browser 版本；
- 测试命令；
- 已知 intentional skips/warnings；
- safe defaults；
- production observation=`NOT_RUN`。

### Step 5：退出门禁

- 所有本阶段文件均有归属；
- 用户无关变更未被提交或丢弃；
- RC revision 包含全部必要实现；
- 历史 HTML 仍保持删除；
- 没有 secret、DSN 或候选人数据进入提交；
- 工作树剩余变更都有明确 owner。

---

## Task 1：提交后回归与证据重绑定

**目的：** 证明 RC revision，而不是提交前的 dirty worktree，能够重现验收结果。

**建议文件：**

- Modify: `docs/memory-validation-operational-evidence.json`
- Modify: `docs/memory-validation-long-term-foundation-acceptance.md`
- Create: `docs/memory-shadow-rc-acceptance.md`
- Modify/Create: `scripts/memory_shadow_release_preflight.py`
- Modify: `tests/test_memory_validation_foundation_acceptance.py`

### Step 1：从 RC revision 运行完整验证

至少运行：

~~~powershell
& 'F:\python3.11\python.exe' -m pytest -q
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_runtime_migrations.py tests/test_postgres_session_store.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py tests/test_postgres_session_deletion.py tests/test_postgres_memory_metrics.py tests/test_postgres_principal_memory.py tests/test_postgres_principal_memory_consent.py tests/test_dual_langgraph_canary_postgres.py -q -m pg_runtime
npm.cmd run build:frontend
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
& 'F:\python3.11\python.exe' -m compileall -q app scripts tests
git diff --check
& 'F:\python3.11\python.exe' -m scripts.memory_validation_foundation_acceptance
~~~

Live PostgreSQL selected tests 不得全部 skip。

### Step 2：重绑定 evidence revision

更新 evidence 中的 revision 和执行时间，不得继续使用基础 revision `9132cf3` 代表
新实现。证据只能保存聚合结果。

### Step 3：验证可重建性

在可控环境中从 RC revision 构建一次全新虚拟环境或等价隔离环境，验证：

- 依赖安装可复现；
- migration registry head 正确；
- Knowledge manifest 可重建；
- frontend build 可重建；
- acceptance runner 精确输出预期状态。

### Step 4：退出门禁

~~~text
MEMORY_SHADOW_RC=REPRODUCIBLE
FOUNDATION_ACCEPTANCE=PASS
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

---

## Task 2：隔离 Staging、迁移、配置与回滚 Preflight

**目的：** 建立一个不会误连接生产、不会误启用消费、能够清理和回滚的目标环境。

**建议文件：**

- Create: `docs/memory-shadow-staging-runbook.md`
- Create: `scripts/memory_shadow_staging_preflight.py`
- Create: `tests/test_memory_shadow_staging_preflight.py`
- Create: `docs/memory-shadow-staging-acceptance.md`

### Step 1：目标环境声明

操作人必须提供：

- 环境类别：isolated staging；
- RC revision；
- 观察 profile A 或 B；
- 观察窗口；
- 数据类别；
- operator；
- stop/rollback owner；
- 数据保留期；
- 备份和恢复位置类别；
- 是否允许真实 Provider；默认 false。

制品不得保存主机名、DSN 或 credential。

### Step 2：安全连接 preflight

优先级从高到低为：独立 PostgreSQL 实例、同一实例中的独立 database、同一 database 中
严格隔离的 schema/table prefix。对于当前单用户、本地部署，如果只有一个 PostgreSQL
实例，Staging 可以与本地业务实例共存，但必须满足以下全部条件：

- 使用独立 database，或使用经过严格 validator 验证的 schema/table prefix；
- Staging 只处理合成或内部明确授权数据，不读取现有业务表；
- database fingerprint、deployment scope 和隔离 prefix 在 preflight 中匹配批准记录；
- migration、backup、restore、cleanup 和 tombstone replay 只能命中隔离范围；
- cleanup 前后都验证 relation inventory 和 residue；
- 不复用生产连接池、worker queue、outbox owner 或 artifact owner scope；
- 发生无法证明边界的情况时 fail closed。

在本计划的单用户 Staging 范围内，上述严格 schema/prefix 隔离可以作为“实例隔离”的受限
替代，但它不等价于生产级物理或数据库级隔离，也不能据此授权生产 Shadow。

脚本必须验证：

- 目标不是未声明或未批准的生产数据库；如果与本地业务实例共存，必须明确记录
  `co_resident_isolated_staging=true` 并通过上述受限替代条件；
- 目标 schema/prefix 符合隔离命名规则；
- migration 以 validate/apply 的批准模式运行；
- 数据库支持 pgvector 和当前 PostgreSQL contract；
- durable metrics store 可写、可 rollup、可清理；
- backup/restore drill 使用独立副本；
- cleanup 只操作经过严格验证的隔离范围。

可以输出不可逆 database fingerprint，但不得输出 DSN。

### Step 3：配置冲突检查

初始部署必须验证：

~~~text
budget mode = disabled
compression mode = disabled
long-term mode = disabled
write shadow gate = false
read shadow gate = false
trusted-local API = false
question memory consumption = disabled
budget enforcement = disabled
~~~

出现 `consume`、同时开启不兼容 gate 或缺少 durable metrics 时，readiness 返回不可用，
但应用进程可以保留用于诊断；不得静默改写配置。

### Step 4：回滚演练

验证：

- 配置恢复 disabled；
- worker 停止 leasing；
- 原有 deterministic Interview 路径继续；
- 不删除 migration、graph definition 或 immutable artifact；
- 不把 terminal fact 恢复成 active；
- 清理 Staging 测试关系后 residue=0。

### Step 5：退出门禁

~~~text
STAGING_PREFLIGHT=PASS
MIGRATION_SCOPE=ISOLATED
ROLLBACK_DRILL=PASS
ALL_MEMORY_SHADOWS=DISABLED
~~~

---

## Task 3：运行 Budget Shadow

**目的：** 在不改变 Provider 输入和业务行为的条件下，收集预算估算、选择和延迟的
运行分布。

**前置条件：** Tasks 0-2 通过；durable metrics `data_complete=true`；操作窗口已批准。

**建议文件：**

- Modify: `scripts/memory_budget_shadow.py`
- Create: `scripts/memory_budget_shadow_observe.py`
- Create: `tests/test_memory_budget_shadow_observe.py`
- Modify: `docs/memory-budget-shadow-runbook.md`
- Create: `docs/memory-budget-shadow-observation.json`

### Step 1：Validate-only preflight

在启用前运行现有 validate-only 工具，证明：

- 当前 Shadow 仍关闭；
- RC、Staging、Knowledge、quality、PostgreSQL、browser 和 metrics 证据存在；
- Question Memory consumption 关闭；
- Principal Memory mode 为 disabled；
- 观察窗口和 stop owner 已提供。

### Step 2：单轴启用

仅在批准的 Staging 配置中启用 Budget `shadow`。必须保持：

~~~text
budget enforcement = disabled
compression consumption = disabled
question memory consumption = disabled
principal memory = disabled
~~~

Shadow 可以计算 would-select、would-drop、estimator error 和预算利用率，不得把结果用于
实际裁剪、压缩或 Provider 请求构建。

### Step 3：收集 allowlisted 聚合指标

至少包括：

- operation；
- language bucket；
- estimator path；
- estimator fallback；
- estimated/provider token 差异方向；
- would-select/would-drop 数量；
- mandatory current-content loss；
- known-over-budget provider call；
- fallback count；
- follow-up error rate；
- P50/P95 latency；
- observation bucket availability；
- metrics `data_complete`。

### Step 4：运行 Profile A 或 B

不得用低样本得出通过结论。Profile B 必须覆盖中文、英文、mixed、长代码标识符、数字、
纠正、否定、fallback 和长历史问题。

### Step 5：关闭并生成 observation record

观察窗口结束后先恢复 disabled，再生成只含聚合指标的 observation record。记录必须通过
artifact audit。

---

## Task 4：Budget Shadow 观察与晋级门禁

**目的：** 对 Budget Shadow 运行结果作出可重复的 PASS/BLOCKED 判断。

**建议文件：**

- Create: `scripts/memory_budget_shadow_acceptance.py`
- Create: `tests/test_memory_budget_shadow_acceptance.py`
- Create: `docs/memory-budget-shadow-acceptance.md`

### Step 1：Hard stop

以下任意一项出现即 BLOCKED：

| Hard stop | 阈值 |
|---|---:|
| Known-over-budget provider call | > 0 |
| Mandatory current-content loss | > 0 |
| Privacy audit hit | > 0 |
| Budget/config conflict | > 0 |
| Durable metrics incomplete | true |
| Unavailable observation bucket | > 1 |
| Follow-up error-rate regression，样本 ≥ 200 | > 0.5 个百分点 |
| P95 follow-up latency regression，样本 ≥ 200 | > 20% |

Error-rate 和 P95 regression 必须按关键 path/bucket 分别判断。某条路径少于 200 个有效
样本时，偶发失败不触发统计型 BLOCKED，也不能判定 PASS；该路径输出
`CONTINUE_OBSERVATION`，继续收集样本或使用批准的 Profile B 合成矩阵补齐覆盖。隐私、
mandatory-content-loss、known-over-budget 和配置冲突等非统计 hard stop 不受样本量豁免。

### Step 2：样本门禁

- 样本不足可以输出 `CONTINUE_OBSERVATION`；
- 样本不足不能输出 PASS；
- 任何 language/operation 关键 bucket 缺失都不能扩大；
- Profile B 必须完成固定覆盖矩阵。

### Step 3：输出

成功时输出：

~~~text
BUDGET_SHADOW_STAGING=PASS
BUDGET_ENFORCEMENT=BLOCKED
PRINCIPAL_MEMORY_SHADOW=NOT_RUN
PRODUCTION_OBSERVATION=NOT_RUN
~~~

---

## Task 5：运行 Principal Memory Write Shadow

**目的：** 验证显式身份、Consent、权威 source、taxonomy、异步 effect、去重和 proposed
存储在运行环境中的行为，不产生 active fact 或 Prompt 影响。

**前置条件：** Budget Shadow PASS；使用合成 Principal 或内部明确授权数据；删除路径可用。

**建议文件：**

- Create: `scripts/principal_memory_write_shadow.py`
- Create: `tests/test_principal_memory_write_shadow_runtime.py`
- Create: `docs/principal-memory-write-shadow-runbook.md`
- Create: `docs/principal-memory-write-shadow-observation.json`
- Modify only if required: `app/services/principal_memory_tasks.py`
- Modify only if required: `app/services/memory_metrics.py`

“Only if required”不降低验证要求。Shadow 运行中如果发现 worker lease、idle renewal、
metrics aggregation 或其他仓库测试未覆盖的问题，任何 production-code 修改都必须：

1. 新增或更新对应的 characterisation/focused tests；
2. 重新运行 compileall 和受影响的完整 focused suite；
3. 使修改前的 observation record 失效或明确标记为旧 revision；
4. 从当前 Task 的 Staging preflight 重新开始观察；
5. 不把修改前后的样本合并为同一个晋级窗口。

### Step 1：启用前检查

必须验证：

- Identity resolver 是显式测试/内部授权 resolver；
- 当前 write Consent 存在；
- long-term mode 和 write gate 一致；
- read gate 仍关闭；
- trusted-local API 仅在生命周期演练窗口临时开启；
- extractor 是批准的固定/测试 extractor，或另行批准的 Provider；
- public knowledge ingestion 不引用 Principal Memory；
- proposal worker 有独立 lease/fencing。

### Step 2：只生成 proposed facts

每次 effect 重新检查：

- 当前 Principal Identity；
- 当前 Consent；
- Session 未 deleting/deleted；
- Source state version；
- Exact excerpt 仍存在于权威消息；
- Taxonomy version；
- 每 Session proposal 上限；
- accessibility preference 是否为用户直接声明。

### Step 3：运行故障和重放场景

覆盖：

- Identity unavailable/changed；
- Consent 在 enqueue 后撤回；
- Session 在 enqueue 后删除；
- Source version 改变；
- Worker 在 create 前/后丢失；
- 同一 event 重放；
- 两个 worker 并发 proposal；
- 不合法 taxonomy；
- Prompt Injection 字符串；
- extractor timeout/failure；
- PostgreSQL 短暂不可用。

### Step 4：Write Shadow hard invariants

以下必须为 0：

- 无 Consent proposal；
- Identity unavailable proposal；
- Cross-principal write；
- Source mismatch write；
- 非 allowlist taxonomy write；
- 自由文本 normalized fact；
- 自动 active；
- 自动 `user_confirmed=true`；
- 模型推断 accessibility preference；
- 删除中 Session 新 proposal；
- Public Knowledge write；
- Prompt/Question/Score/Report change；
- 隐私 artifact hit。

### Step 5：关闭并记录

观察结束后恢复 long-term disabled。Observation record 只记录 proposal outcome、拒绝原因类别、
taxonomy 类别、去重、延迟、故障和聚合计数。

---

## Task 6：Proposal 人工复核与质量门禁

**目的：** 证明 Write Shadow 不仅满足 schema，还在业务语义上足够准确、保守和有用。

**建议文件：**

- Create: `docs/principal-memory-proposal-review-protocol.md`
- Create: `scripts/principal_memory_proposal_review.py`
- Create: `tests/test_principal_memory_proposal_review.py`
- Create: `docs/principal-memory-proposal-quality.json`

### Step 1：受控审核环境

原始 Session、Excerpt 和 proposal 内容不得进入普通 observation artifact。审核只能在：

- 合成固定 fixture；或
- 明确授权的内部测试数据；
- 最小权限临时视图；
- 有自动过期和删除的审核环境中进行。

常规质量报告只输出分类计数和比例。

### Step 2：审核标签

每个 proposal 只能标记为：

~~~text
correct
unsupported
over_generalized
wrong_taxonomy
stale_source
conflict
privacy_sensitive
not_useful
duplicate
review_unavailable
~~~

### Step 3：最低样本

- Profile A：至少复核 300 个 proposal；
- Profile B：每个允许 taxonomy 至少 50 个 proposal，合计不少于 300；
- 中文、英文、mixed 都必须覆盖；
- accessibility preference 必须单独报告 direct-statement 命中率。

### Step 4：晋级阈值

| 指标 | 阈值 |
|---|---:|
| `privacy_sensitive` | 0 |
| Cross-principal | 0 |
| Free-text contract violation | 0 |
| Unsupported | < 2% |
| Wrong taxonomy | < 2% |
| Stale source accepted | 0 |
| 自动 active/confirmed | 0 |
| Review coverage | 100% 或明确标记 unavailable |

`not_useful` 和 `over_generalized` 不作为隐私 hard stop，但必须形成趋势和改进项；高比例时
不得进入 Read Shadow。

### Step 5：输出

~~~text
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PROPOSAL_QUALITY_GATE=PASS
PRINCIPAL_READ_SHADOW=NOT_RUN
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
~~~

---

## Task 7：运行 Principal Memory Read Shadow，保持零注入

**目的：** 在运行分布下验证 active、confirmed、current-consent facts 的有界选择、冲突排除、
相关性和延迟，同时证明业务输出完全不变。

**前置条件：** Tasks 5-6 PASS；active fact 只由显式测试确认产生；删除和撤回路径可用。

**建议文件：**

- Create: `scripts/principal_memory_read_shadow.py`
- Create: `tests/test_principal_memory_read_shadow_runtime.py`
- Create: `docs/principal-memory-read-shadow-runbook.md`
- Create: `docs/principal-memory-read-shadow-observation.json`
- Modify only if required: `app/services/principal_memory_shadow.py`
- Modify only if required: `app/services/principal_memory_retrieval.py`

### Step 1：准备显式确认 facts

只允许使用：

- 合成 fixture 明确确认的 facts；
- 或内部测试人员直接确认的 facts；
- 当前 Consent policy 和 taxonomy version；
- 未过期、未撤回、未删除 source。

模型 proposal 不得通过脚本批量自动确认。

### Step 2：运行 bounded would-select

选择仍使用确定性 taxonomy matching，不使用 vector search。必须遵守：

- `max_shadow_facts`；
- `max_shadow_tokens`；
- deployment/principal exact match；
- active + user-confirmed；
- current read-shadow consent；
- source session 可用；
- authority/confidence 下限；
- exclusive conflict 全部排除。

### Step 3：逐调用验证零注入

运行时主验证使用 Provider Context/Prompt 的 canonical SHA-256 digest，而不是对每次调用都
保留完整深拷贝。Canonicalization 至少固定 Unicode NFC、键排序、紧凑 JSON 分隔符和消息
顺序；指标只记录 digest 是否相等和 violation count，不持久化 digest 值或 Prompt 内容。

每次 observe 前后比较：

- Provider Context canonical digest；
- Prompt canonical digest；
- messages；
- selected question；
- scoring input/output；
- report input/output；
- evidence；
- API response。

完整深拷贝和逐字段 diff 仅允许在 debug/Staging 诊断模式下使用，可以按固定比例采样，或在
digest 不一致时临时启用；它不得进入常规 production-style 热路径，也不得把差异内容写入
日志或 observation artifact。

任何 digest 或业务结果差异都立即关闭 read-shadow，并输出
`prompt_isolation_violation`。如果为降低开销而修改现有 `PrincipalMemoryShadowService`，
必须保留异常时恢复原 context 的 fail-open 行为，并重新运行 Prompt isolation、runtime、
latency 和完整 Staging Read Shadow 验证。

### Step 4：相关性复核

聚合或受控审核标签：

~~~text
relevant
irrelevant
stale
over_personalized
potentially_sensitive
conflicting
useful_but_not_authorized
excluded_correctly
~~~

### Step 5：Hard invariants

以下必须为 0：

- unconfirmed selected；
- revoked/expired/deleted selected；
- Consent revoked selected；
- Cross-principal selected；
- conflicting exclusive values selected；
- Prompt/Provider Context mutation；
- Question/Score/Report mutation；
- Token/fact limit violation；
- Privacy artifact hit。

P95 latency regression 在样本 ≥ 200 时不得超过 20%。

### Step 6：退出状态

~~~text
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
PROMPT_ISOLATION=PASS
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
~~~

---

## Task 8：Consent、生命周期与删除演练

**目的：** 证明长期记忆在授权变化、来源删除和 Principal 删除时能够立即停止并最终清除。

**建议文件：**

- Create: `scripts/principal_memory_lifecycle_drill.py`
- Create: `tests/test_principal_memory_lifecycle_drill.py`
- Create: `docs/principal-memory-lifecycle-drill.md`
- Create: `docs/principal-memory-lifecycle-drill-evidence.json`

### Step 1：执行完整序列

~~~text
创建合成 Principal
  → Grant proposal_write Consent
  → 创建并完成合成 Session
  → 生成 proposed facts
  → Confirm 一个 Fact
  → Reject 一个 Fact
  → 创建 supersede predecessor
  → Grant read_shadow Consent
  → 运行 would-select
  → Revoke Consent
  → 验证停止新 proposal
  → 验证停止 read-shadow
  → Session purge
  → Principal purge
  → 验证 terminal/purged state
~~~

### Step 2：撤回竞态

覆盖：

- event enqueue 后、worker consume 前撤回；
- worker 读取 source 后、store create 前撤回；
- read-shadow select 前撤回；
- read-shadow select 后、observe record 前撤回；
- revoke 与 confirm 并发；
- purge 与 replay 并发。

安全语义是拒绝或删除，不是继续处理。

### Step 3：删除结果

证明：

- 新 proposal 停止；
- queued effect 被取消或安全拒绝；
- revoked fact 不再 selected；
- Session source facts 被删除；
- Principal facts/effects/consent 被删除；
- aggregate metrics 不提供个人 drill-down；
- public knowledge 不改变；
- tombstone 保留用于恢复后重放。

### Step 4：退出状态

~~~text
CONSENT_REVOCATION_DRILL=PASS
SESSION_MEMORY_PURGE_DRILL=PASS
PRINCIPAL_MEMORY_PURGE_DRILL=PASS
~~~

---

## Task 9：旧备份恢复与 Tombstone Replay

**目的：** 证明恢复旧备份不会永久复活已删除的 Session 或 Principal Memory。

**建议文件：**

- Modify: `scripts/replay_session_deletion_tombstones.py`
- Create: `scripts/memory_shadow_restore_drill.py`
- Create: `tests/test_memory_shadow_restore_drill.py`
- Create: `docs/memory-shadow-restore-drill.md`
- Create: `docs/memory-shadow-restore-drill-evidence.json`

### Step 1：创建旧备份快照

在隔离 Staging 副本中创建包含合成数据的备份，然后执行 Session/Principal 删除并保存
operator tombstone ledger。不得在生产数据库执行此演练。

### Step 2：恢复旧备份

恢复到新的隔离数据库或 schema，验证已删除数据在恢复副本中暂时重新出现；该步骤只用于
证明 replay 的必要性，不允许该副本接收业务流量。

### Step 3：导入并重放 tombstone

验证再次删除：

- business session；
- workflow checkpoint；
- messages；
- reports；
- Question Memory；
- Context Artifact owner refs；
- Principal facts/effects；
- Session-bound consent/bindings；
- 其他由 deletion contract 列出的派生数据。

### Step 4：故障注入

在六个现有 purge 边界和 tombstone complete 周边模拟进程丢失，证明任务可以 reclaim、重试、
幂等完成，且不会跳过 Principal Memory。

### Step 5：退出状态

~~~text
BACKUP_RESTORE_TOMBSTONE_REPLAY=PASS
RESTORED_PRIVATE_DATA_RESIDUE=0
PUBLIC_KNOWLEDGE_UNCHANGED=true
~~~

证据只能记录聚合 residue count，不记录被删除对象的 ID。

---

## Task 10：聚合观察面板与自动停止告警

**目的：** 把现有 durable metrics 变成 operator 可以判断、停止和审计的聚合视图。

**建议文件：**

- Create: `app/services/memory_shadow_observability.py`
- Create: `app/ports/memory_shadow_observability.py`
- Create: `tests/test_memory_shadow_observability.py`
- Create: `docs/memory-shadow-observability-runbook.md`
- Create: `scripts/memory_shadow_status.py`
- Modify only if approved: trusted-local status routes

### Step 1：Budget 面板

至少展示：

- estimator error；
- over-budget count；
- mandatory-content-loss count；
- would-select/would-drop；
- fallback；
- error-rate delta；
- P95 latency delta；
- sample sufficiency；
- `data_complete`。

### Step 2：Write Shadow 面板

至少展示：

- identity available/unavailable；
- Consent granted/revoked；
- event requested/completed/cancelled；
- proposal created/rejected/deduplicated；
- taxonomy rejection；
- source mismatch；
- lifecycle status transitions；
- review labels。

### Step 3：Read Shadow 面板

至少展示：

- eligible/selected/dropped；
- conflict exclusion；
- consent/expiry/revoke/source exclusion；
- token/fact cap；
- latency；
- fail-open；
- Prompt isolation violation。

### Step 4：低基数隐私控制

不得提供 per-principal、per-session、per-fact drill-down。小样本 bucket 应合并、延迟展示或
不展示，防止通过维度组合反向识别个人。

### Step 5：自动停止

Hard stop 触发时：

1. 输出稳定 gate code；
2. 禁止扩大；
3. 恢复对应 mode/gate 为 disabled；
4. 停止新 worker leasing；
5. 保留最小聚合证据；
6. 不影响 deterministic Interview 路径；
7. 通知 operator 和隐私负责人。

状态端点保持 status-only，不允许通过 GET/POST 请求开启 Shadow。

---

## Task 11：隐私、安全、公平性与 Knowledge Firewall 复审

**目的：** 在有运行观察数据后重新确认长期记忆没有跨边界、暗中影响面试或形成不公平使用。

**建议文件：**

- Modify: `docs/principal-memory-threat-model.md`
- Create: `docs/memory-shadow-security-review.md`
- Create: `tests/test_memory_shadow_privacy.py`
- Create: `tests/test_memory_shadow_fairness.py`
- Modify: `tests/test_principal_memory_knowledge_firewall.py`
- Modify: `tests/test_principal_memory_prompt_isolation.py`

### Step 1：身份与跨 Principal

验证：

- 不从简历、姓名、Email、电话、IP、User-Agent、浏览器 ID 或 embedding 推断 Principal；
- deployment/principal exact match；
- 账号/Principal 不可用时 fail closed for memory；
- 相同 taxonomy facts 在不同 Principal 间绝不共享。

### Step 2：Consent 与目的限制

验证 proposal_write 和 read_shadow purpose 分离；撤回其中一个不会被另一个隐式覆盖；旧
policy version 不继续授权新操作。

### Step 3：Prompt Injection

使用合成攻击字符串覆盖：

- 要求模型把答案写为长期事实；
- 要求绕过 Consent；
- 要求把 Fact 标为 active；
- 要求修改评分；
- 要求写入公共知识；
- 要求暴露其他 Principal；
- 要求忽略删除或撤回。

所有模型输出仍只能进入 proposed + taxonomy contract。

### Step 4：公平性边界

证明当前 Shadow 不改变提问、评分和报告。审核 proposed/selected taxonomy 是否包含或代理：

- 人格；
- 诚信；
- 情绪或心理健康；
- 身体健康；
- 政治、宗教、族裔；
- 婚育、年龄等受保护信息；
- 招聘倾向和历史评分。

出现此类内容必须 hard stop，不得仅标记为低质量。

### Step 5：Knowledge Firewall

验证：

- vector store/corpus builder 不导入 Principal Fact Store；
- Principal fact 无 embedding；
- corpus loader 拒绝 Principal Memory schema；
- Principal 删除不修改公共知识；
- public knowledge retrieval 不接受 Principal scope；
- observation/report 不把 personal fact 包装成 knowledge hit。

### Step 6：Artifact audit

审计所有 `.json`、`.jsonl`、`.log`、`.md`、`.txt` observation artifact；发现敏感 key、
sentinel、DSN 或标识符即失败。

---

## Task 12：Shadow 阶段 Acceptance Gate

**目的：** 用一个机器 runner 汇总 RC、Staging、Budget、Write、Read、Deletion、Restore、
Metrics、Quality、Privacy 和安全默认值。

**建议文件：**

- Create: `scripts/memory_operational_shadow_acceptance.py`
- Create: `tests/test_memory_operational_shadow_acceptance.py`
- Create: `tests/test_memory_operational_shadow_plan.py`
- Create: `docs/memory-operational-shadow-acceptance.md`
- Create: `docs/memory-operational-shadow-evidence.json`
- Modify only if required: `README.md`

### Step 1：强制 gates

Runner 至少检查：

- RC revision 可复现；
- full Python/pg_runtime/frontend/browser green；
- Staging isolated；
- migrations validated and cleanup verified；
- Budget Shadow PASS；
- Write Shadow PASS；
- proposal quality PASS；
- Read Shadow zero-injection PASS；
- Consent/revoke/delete drills PASS；
- backup restore/tombstone replay PASS；
- durable metrics complete；
- sample profile complete；
- privacy/security/fairness/firewall PASS；
- committed safe defaults remain disabled；
- `consume` rejected；
- production observation仍为 NOT_RUN。

### Step 2：失败输出

失败时：

~~~text
MEMORY_OPERATIONAL_SHADOW=BLOCKED
GATE=<stable_gate_code>
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

不得同时输出任何 READY。

### Step 3：成功输出

成功时精确输出：

~~~text
MEMORY_SHADOW_RC=REPRODUCIBLE
BUDGET_SHADOW_STAGING=PASS
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
PRODUCTION_SHADOW_APPROVAL_REQUIRED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

`PRODUCTION_SHADOW_APPROVAL_REQUIRED` 表示可以提交审批材料，不表示已经获得批准。

### Step 4：完整回归

最终至少运行：

~~~powershell
& 'F:\python3.11\python.exe' -m pytest -q
& 'F:\python3.11\python.exe' -m pytest -q -m pg_runtime <approved postgres test list>
npm.cmd run build:frontend
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
& 'F:\python3.11\python.exe' -m compileall -q app scripts tests
git diff --check
& 'F:\python3.11\python.exe' -m scripts.memory_operational_shadow_acceptance
~~~

验收记录必须包含 exact counts、环境类别、RC revision、观察 profile、窗口、聚合门禁、清理
结果和安全默认值，不得包含数据内容或定位符。

---

## Task 13：Principal Memory Consumption Spec 草案，不实现

**目的：** 在 Shadow 证据完成后，定义未来消费阶段需要解决的产品、安全、公平性和回滚问题。

**建议文件：**

- Create: `docs/principal-memory-consumption-spec.md`
- Create: `docs/principal-memory-consumption-risk-review.md`
- Create: `tests/test_principal_memory_consumption_spec_contract.py`

### Step 1：消费前置条件

Spec 必须要求：

- 正式 authenticated Principal；
- 候选人可见、可理解、非默认勾选的 Consent；
- 查看、确认、更正、撤回、删除和导出能力；
- 每轮面试忽略长期记忆，以及面试进行中实时禁用长期记忆的能力；
- 使用记忆时的明确提示；
- 删除和撤回传播 SLA；
- 生产隐私、安全和公平性批准；
- 独立 canary、rollback 和 observation plan。

### Step 2：第一版允许的事实

建议第一版只讨论：

- `interview_language`；
- 用户直接确认的 `accessibility_preference`；
- `learning_goal`；
- `target_role_family`。

`confirmed_skill` 不得直接影响评分；是否用于追问也必须独立论证。

### Step 3：禁止用途

Spec 必须明确禁止：

- 历史评分参与当前评分；
- 招聘结论；
- 人格、诚信、情绪、健康、政治、宗教等敏感推断；
- 公共知识自增长；
- 跨 Principal 相似度；
- 未告知用户的隐式个性化；
- 未提供实时禁用长期记忆且不惩罚用户的能力；
- 通过长期记忆掩盖当前 Session 的直接证据。

实时禁用必须在下一次 follow-up/context assembly 前生效：停止新增 personal fact、停止新的
read selection，并且不改变评分、报告或用户可用功能。已经发送给 Provider 的在途请求无法
事后撤回时，系统必须明确该技术边界，但不得在后续调用继续加入长期记忆。禁用操作不得
降低面试评分、减少功能、触发负面标签或形成招聘信号。

### Step 4：消费契约

必须定义：

- 允许消费的 operation；
- Prompt 中的位置和可见标记；
- 最大 Fact 数和 token；
- source/authority 表示；
- conflict 和 stale 行为；
- current-session evidence 优先级；
- fail-open/fail-closed；
- 用户禁用；
- 1% canary；
- 自动停止；
- 评分与报告隔离测试。

### Step 5：本任务终态

本任务只允许输出：

~~~text
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
~~~

---

## 8. 晋级门禁总表

| 从 | 到 | 必须满足 |
|---|---|---|
| Dirty worktree | RC | 文件所有权、scoped commits、secret audit、完整回归 |
| RC | Staging | revision 可复现、隔离 DB、migration/cleanup、rollback |
| Disabled | Budget Shadow | metrics complete、quality/knowledge/deletion green、批准窗口 |
| Budget Shadow | Write Shadow | hard stop=0、样本充分、error/latency 达标 |
| Write Shadow | Read Shadow | Task 6 proposal quality gate PASS、proposal precision、隐私=0、自动 active=0、删除可用 |
| Read Shadow | Shadow acceptance | 零 Prompt 变化、冲突/撤回/删除正确、延迟达标 |
| Shadow acceptance | 生产 Shadow 审批申请 | restore replay、privacy/fairness/firewall、完整证据 |
| 生产 Shadow | Consumption Spec | 不由本计划授权 |
| Consumption Spec | Consumption implementation | 新 Plan、新隐私批准、新产品身份/Consent UX |

---

## 9. 回滚矩阵

| 故障 | 立即行动 | 已有数据 | 新业务 |
|---|---|---|---|
| RC 无法复现 | 停止部署，修复提交边界 | 保留验收证据但标记失效 | 不进入 Staging |
| Staging 指向错误 DB | 立即停止，禁止 migration | 保全最小审计证据 | 全部拒绝 |
| Migration mismatch | 停止 runtime validate/apply | 不删除历史 schema | 不启用 store |
| Metrics incomplete | 关闭对应 Shadow | 业务继续，观察无效 | 不晋级 |
| Budget hard stop | Budget mode=disabled | Provider 原路径继续 | 不启用 Write Shadow |
| Proposal privacy violation | Long-term disabled，停止 worker leasing | proposed facts 进入隔离审查或 purge | 不创建新 proposal |
| Consent 失效 | 停止 write/read shadow | revoke/purge | 全部拒绝 |
| Cross-principal 泄漏 | 关闭全部 Principal Memory，启动隐私事件流程 | 最小取证后 purge | 全部拒绝 |
| 自动 active/confirmed | 关闭 Write Shadow | 错误 facts revoke/delete | 不进入 Read Shadow |
| Read Shadow 修改 Prompt | 立即 disabled | deterministic 路径继续 | 不执行 resolver |
| Delete/replay 失败 | 停止新长期写入 | 保留 tombstone，重试/人工修复 | 不创建新 facts |
| Public corpus contamination | 停止 corpus publish/load | 回退 approved manifest | 禁止新 corpus |
| Artifact 泄密 | 停止导出和面板 | 限制访问、删除、轮换凭据 | 禁止新 artifact |
| Latency/error 回归 | 关闭当前 Shadow | 原业务路径继续 | 不扩大 |
| 样本不足 | 继续观察或补合成矩阵 | 不判定 PASS | 不晋级 |

回滚不得删除仍被引用的 migration、graph definition 或 immutable artifact；不得通过把
terminal fact 恢复为 active 来“修复”问题。

---

## 10. 风险登记

| 风险 | 缓解措施 | 必须证据 |
|---|---|---|
| Dirty worktree 无法复现 | ownership + scoped commits + RC rerun | RC acceptance |
| Shadow 同时改变多个变量 | 严格串行、每阶段独立 record | config snapshots |
| 低样本误判 | Profile A/B 最低样本 | bucket completeness |
| 运行指标泄露个人 | aggregate only + low-cardinality control | artifact audit |
| Proposal schema 正确但语义错误 | 人工复核与质量分类 | proposal quality report |
| 自动确认模型事实 | proposed-only contract | lifecycle metrics/tests |
| Consent TOCTOU | operation-time re-read | revoke race drill |
| Identity 错误合并 | explicit resolver only | identity audit |
| 历史事实影响评分 | zero-injection + source audit | prompt/score equality |
| 过度个性化 | read-shadow relevance review | over-personalized rate |
| 删除后备份复活 | operator tombstone replay | restore drill |
| 公共知识污染 | store/import/migration firewall | knowledge firewall |
| Prompt Injection 激活事实 | taxonomy + proposed-only | adversarial fixtures |
| Staging 被误称为生产验证 | 精确状态输出 | acceptance wording test |
| Trusted-local API 外露 | default hidden + route audit | API tests |
| Shadow 失败影响面试 | sidecar/fail-open | business path equality |
| Consumption 被提前实现 | Spec-only contract test | source/config audit |

---

## 11. Definition of Done

本计划完成必须同时满足：

1. 当前记忆系统变更已按所有权边界固化为可复现 RC。
2. 用户已有无关变更未丢失、未被错误提交。
3. RC revision 重新通过 full Python、live PostgreSQL、frontend、browser 和 acceptance。
4. 运行证据中的 revision 指向包含实际实现的提交。
5. Staging 被证明不是生产数据库，migration 和 cleanup 范围可验证。
6. 初始部署所有 memory shadow/enforcement/consumption 开关为 disabled。
7. Budget Shadow 只观察且通过 hard stop、样本、错误率和 P95 latency 门禁。
8. Write Shadow 只创建 proposed、unconfirmed facts。
9. 无 Consent、Identity unavailable、source mismatch 和删除中 Session 不产生 proposal。
10. 非 allowlist taxonomy、自由文本、评分和招聘结论不能进入 Fact Store。
11. 至少 300 个 proposal 完成受控质量复核或满足等价 Profile B 覆盖。
12. Privacy-sensitive、cross-principal 和自动 active/confirmed 均为 0。
13. Read Shadow 只选择 active、confirmed、current-consent、current-taxonomy facts。
14. Conflicting exclusive facts 全部排除。
15. Read Shadow 前后 Provider Context、Prompt、问题、评分、报告、Evidence 和响应完全相同。
16. Consent 撤回竞态、Session purge 和 Principal purge 演练通过。
17. 旧备份恢复后的 tombstone replay 通过，私有数据 residue=0。
18. Public Knowledge Corpus 在 Principal Memory 生命周期和删除中保持不变。
19. Durable metrics `data_complete=true`，无 per-principal/session/fact drill-down。
20. Shadow observation artifacts 通过隐私审计。
21. Prompt Injection、身份、Consent、跨 Principal、公平性和 Knowledge Firewall 复审通过。
22. 所有 Shadow 结束后恢复 committed/default disabled 状态。
23. 生产 observation 仍明确为 NOT_RUN，除非未来有独立生产 Shadow 授权和记录。
24. Consumption Spec 仅为 draft，没有消费代码、配置或 canary。
25. 最终 runner 精确输出：

~~~text
MEMORY_SHADOW_RC=REPRODUCIBLE
BUDGET_SHADOW_STAGING=PASS
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
PRODUCTION_SHADOW_APPROVAL_REQUIRED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

---

## 12. Traceability 说明

本计划不创建新的 `MEM-*` requirement ID，也不把运行阈值伪装成已经存在于 Spec 的产品
需求。它由三类输入构成：

1. Spec v1.1.1-draft 中现有的 Budget、Lifecycle、Observability、Security、Long-term
   Memory 和 Testing 要求；
2. 上一阶段已经实现并通过的仓库契约；
3. 本计划明确定义的 Staging operational obligations、样本阈值和晋级门禁。

未来 Consumption Spec 如需增加规范性需求，必须更新 Spec 版本并分配正式 requirement
ID；不得只在实施计划里使用临时 `MEM-*` 编号。

---

## 13. 最终边界

本计划完成后，系统可以证明：

~~~text
代码可由明确 revision 重建
+ Budget Shadow 在隔离 Staging 可观察、可停止
+ Principal Write Shadow 只产生 proposed facts
+ Principal Read Shadow 不改变 Prompt 或业务结果
+ Consent、删除和恢复重放可执行
+ 聚合指标和隐私门禁可操作
~~~

它仍然不能证明或授权：

~~~text
真实候选人长期记忆试点
+ 生产 Shadow 已批准
+ personal facts 可以进入 Prompt
+ 历史 facts 可以参与评分
+ 公共知识库可以从候选人数据自增长
+ 长期记忆 consumption 可以开启
~~~

因此，本阶段的正确终点不是“上线长期记忆”，而是“形成一套足以申请生产 Shadow、同时
继续阻断 Consumption 的可复现证据”。
