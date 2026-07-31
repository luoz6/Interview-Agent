# Interview Agent 记忆验证与长期记忆基础实施计划

**Plan revision:** v1.1，基于
docs/interview-agent-memory-system-optimization-spec.md v1.1.1-draft、
docs/memory-system-optimization-acceptance.md 的仓库验收结果，以及上一阶段
Interview Agent Memory System Optimization Implementation Plan v1.1。

**v1.1 review amendments:** 确认 MEM-UX-001 至 MEM-UX-008 已存在于
Spec v1.1.1-draft；明确 rejected/superseded 是生命周期实现细化；固定
canonical JSON 与 Unicode NFC 规则；补充指标保留期的审批延长路径；收紧
supersede 的 fact key 语义；把 postgresql 补回 P1 required tags；定义
trusted-local 测试 API gate；并禁止 Task 3 与 Task 11 并行编辑 migration
registry。

**文档类型：** Implementation Plan / How-to + Reference。

**目标读者：** 后端工程师、Agent 工程师、数据工程师、SRE、QA、安全与隐私评审人员、技术负责人。

> **执行说明：** 本计划描述下一阶段的仓库实现、隔离验证和 Shadow
> 准备工作。只有用户明确要求执行时，才授权修改代码。本文不授权连接生产
> 数据库、调用真实模型、处理真实候选人长期记忆、启用预算 enforcement、
> 启用 Question Memory consumption、启用长期记忆消费或扩大线上流量。
> 所有现有 rollout、enforcement、compression consumption 和长期记忆开关
> 必须保持关闭，直到相应操作步骤获得单独批准。

**阶段目标：** 用真实 PostgreSQL、完整回归、长上下文质量评测和持久化聚合
指标，验证上一阶段已经实现的单会话记忆；同时构建 principal-scoped 长期记忆
的身份、授权、事实提案、生命周期、删除和 Shadow 检索基础。长期记忆在本阶段
只能进行 write-only 或 read-shadow，不能进入面试提问、追问、评分、报告或
公共 Knowledge Corpus。

**架构概览：**

~~~text
轨道 A：单会话记忆验证

旧测试基线清零
  → 浏览器基线稳定
  → 隔离 PostgreSQL 验证
  → 删除/备份恢复演练
  → Knowledge P1 覆盖
  → 持久化聚合指标
  → 长上下文质量评测
  → Budget Shadow 运行手册

轨道 B：长期记忆基础

长期记忆配置与契约
  → principal identity + consent
  → fact proposal store + migration
  → write-only proposal extraction
  → confirm/revoke/expire/delete
  → bounded read-shadow

两轨汇合
  → 隐私与隔离审计
  → 阶段验收
  → 完整回归与发布记录
~~~

**核心安全链：**

~~~text
原始 session message（权威）
  → Question Memory（non-authoritative）
  → Principal fact proposal（proposed）
  → 用户确认或明确规则
  → active personal fact
  → bounded read-shadow
  ✕ 不进入 Prompt
  ✕ 不进入评分 Evidence
  ✕ 不进入公共 pgvector corpus
~~~

**技术栈：** Python 3.11、FastAPI、Pydantic v2、LangGraph、PostgreSQL、
psycopg2、pgvector、React/Vite、Playwright、pytest、现有 runtime outbox、
lease/fencing、Context Artifact、Question Memory、Session Deletion 和
EffectiveMemoryConfig。

---

## 1. 当前仓库基线

计划编写时，上一阶段已经完成仓库级实现，并得到以下结果：

- Memory Optimization focused suite：102 passed。
- 非 live PostgreSQL 删除与 migration contract：56 passed，8 deselected。
- 配置收敛：89 passed。
- 指标与隐私测试：52 passed。
- 前端降级/API/source contract：52 passed。
- Acceptance contract：8 passed。
- 排除旧静态 HTML 基线后的完整 Python：1339 passed，158 skipped。
- React/Vite production build：通过。
- Memory assistance browser：desktop/mobile 共 2 passed。
- compileall、git diff --check、repository acceptance runner：通过。
- 验收状态：

~~~text
READY_FOR_MEMORY_SYSTEM_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
~~~

仍未完成：

- 真实 PostgreSQL pg_runtime 验证。
- 真实删除故障注入和备份 tombstone replay。
- 持久化聚合记忆指标。
- 固定的中英文/混合语言长上下文质量评测。
- Budget Shadow 操作观察。
- 长期记忆身份、授权、fact store 和 Shadow 路径。

当前存在两个已知、与上一阶段记忆实现无关的测试基线问题：

1. app/test0.html 至 app/test4.html 和 app/test-help.html 已被删除，但
   tests/test_static_report_ui.py 与 tests/test_utf8_text_contract.py 仍引用它们。
2. tests/browser/reference-ui.spec.js 的大范围多 viewport 测试在
   report detail 阶段等待 .report-actions 超时；memory-specific browser
   测试已经通过。

本计划把这两项作为正式前置工作处理，不恢复已经删除的历史 HTML 页面。

---

## 2. 执行前置条件

1. 保护当前重度 dirty worktree。不得运行 reset、checkout、clean、宽泛删除、
   force push 或无选择地 stage 全仓库。
2. 开始执行前，必须通过提交、补丁清单或变更所有权清单，明确上一阶段改动与
   用户原有改动的边界。不得为了获得 clean tree 丢弃用户文件。
3. 保留 app/test0.html 至 app/test4.html 和 app/test-help.html 的现有删除
   状态。Task 1 的目标是迁移测试契约，不是重建双前端。
4. 仓库实现测试使用 fake provider。未经单独授权，不调用真实 LLM、embedding
   provider 或外部网络服务。
5. PostgreSQL 测试只允许使用批准的本地或测试实例，并使用随机、隔离、可验证
   的 table prefix。运行前必须打印目标 database fingerprint，但不得打印 DSN
   或 credential。
6. runtime store 继续使用 schema_mode="validate"；DDL 只能由
   app/services/postgres_runtime_migrations.py 和明确 migration entry point
   所有。
7. 不改写历史 LangGraph checkpoint，不删除历史 artifact，不把 migration
   rollback 理解为 DROP 已被现有 session 引用的 schema。
8. 所有 acceptance、日志、metric、trace 和 canary artifact 禁止包含：
   prompt、回答、JD、简历、摘要、excerpt、session ID、principal ID、artifact
   ref、Evidence、credential、DSN 或外部 provider 原始响应。
9. 以下默认值必须保持关闭：

~~~dotenv
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false
# MEMORY_BUDGET_MODE=disabled
# MEMORY_COMPRESSION_MODE=disabled
# MEMORY_TRUSTED_LOCAL_DELETION_ENABLED=false
# MEMORY_TRUSTED_LOCAL_METRICS_ENABLED=false
# MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
# MEMORY_LONG_TERM_MODE=disabled
# MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
# MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
~~~

10. 真实候选人数据不得用于长期记忆开发测试。仓库测试和隔离验收使用合成
    principal、合成 session 和固定 fixture。
11. 即使本计划所有任务完成，长期记忆 consumption 仍保持 BLOCKED；任何把
    personal fact 注入面试 Prompt 的变更都需要新的 Spec、Plan、隐私审批和
    生产批准。

---

## 3. 范围

### 3.1 本阶段包含

- 清理已删除历史 HTML 对应的过期测试基线。
- 拆分并稳定 Reference UI 的大型 browser test。
- 对 memory_session_policy_v1、question_memory_index_v1、
  session_deletion_v1 进行隔离 live PostgreSQL 验证。
- PostgreSQL 删除故障注入、租约恢复和备份 tombstone replay。
- Knowledge Memory 的 positive、hard-negative、boundary P1 覆盖。
- 分钟/小时级、无内容、无标识符的持久化聚合记忆指标。
- 20 至 50 轮中文、英文、混合语言长上下文固定评测。
- Budget Shadow 操作脚本、指标门槛和自动停止条件。
- Principal Memory 的配置、身份端口、授权记录和 consent version。
- Principal fact proposal 的 immutable contract、in-memory/PostgreSQL store
  和 migration。
- 基于权威 source 的异步、受限、write-only fact proposal。
- proposed、active、rejected、superseded、expired、revoked、deleted
  生命周期。
- principal/session 删除传播。
- 有界 read-shadow 和离线比较；不进入任何模型 Prompt。
- 跨 principal 隔离、Prompt Injection、隐私和日志审计。
- 阶段 acceptance runner、acceptance record 和完整回归。

### 3.2 明确排除

- 自动生产 rollout。
- 真实用户长期记忆试点。
- 长期记忆进入追问、计划、评分、报告或推荐。
- 依据历史评分调整当前评分。
- 将候选人回答或 personal fact 写入公共 pgvector。
- 自动晋升 proposed fact 为 active，除非值由用户直接提交且规则明确允许。
- 人格、诚信、情绪、健康、政治、宗教、就业倾向等敏感推断。
- 多租户 tenant boundary；v1 继续使用 deployment_id + principal_id。
- 跨候选人相似度检索。
- 个人原文向量化。
- 自动训练、微调或在线强化学习。
- 大规模 Knowledge Corpus 扩展到所有岗位。
- Principal Memory consumption canary；它属于后续独立阶段。

---

## 4. 固定实现决策

### Decision 1：双轨并行，但消费门禁单向依赖验证轨道

长期记忆基础可以与单会话 Shadow 准备并行实现，但长期记忆读取不得影响业务
行为。轨道 B 可以生成 proposed fact 和 would-select 结果，不能绕过轨道 A 的
PostgreSQL、删除、质量、指标和回归门禁。

### Decision 2：第一阶段长期记忆是 write-only / read-shadow

write-only 表示 proposal 可以在明确授权后写入独立 store；read-shadow 表示
系统可以计算哪些 active fact 会被选择，并记录聚合结果，但返回给 Interview
Graph 的上下文必须与未启用长期记忆时完全一致。

### Decision 3：原始消息永远是权威来源

Question Memory 和 LLM 产生的 principal fact proposal 都是
non-authoritative。Fact store 保存 source session、source question、source
manifest 和 source excerpt digest，但不复制候选人原文。需要展示证据时，在
授权边界内从尚未删除的权威 session source 解析。

### Decision 4：个人事实与公共知识彻底隔离

Principal Memory 使用独立 port、store、migration、表前缀和 owner scope。
app/services/vector_store.py、knowledge ingestion 和 corpus build 工具不得导入
Principal Memory store，也不得接受 principal fact。

### Decision 5：显式身份优先于自动关联

不得根据简历哈希、邮箱文本、浏览器指纹、IP 或模型推断自动合并 principal。
没有可信 PrincipalIdentityResolver 时，长期记忆保持 disabled。测试环境使用
显式 fake resolver；trusted-local 模式也必须由配置显式提供 principal。

### Decision 6：授权是版本化数据，不是一个布尔开关

Consent 至少包含 deployment、principal、policy version、granted_at、
revoked_at 和 allowed purposes。撤回授权立即阻止新 proposal 和 read-shadow，
并进入可重试删除流程。

### Decision 7：normalized_fact 只接受 allowlisted 结构

Spec 中的 normalized_fact 不作为任意自由文本字段。它编码为稳定 canonical
JSON，键和值来自版本化 taxonomy。第一版只支持：

- declared_preference；
- confirmed_skill；
- learning_goal；
- accessibility_preference。

候选人自由文本、项目名、公司名和回答摘要不得进入索引列或 metric。

### Decision 8：指标只持久化聚合桶

不建立逐事件长期 telemetry 表。Runtime 直接对分钟桶做原子 upsert，再由
rollup job 形成小时桶。维度只能来自严格 allowlist，禁止 session_id、
principal_id、fact_id 和 source digest。

### Decision 9：事实提案使用异步 effect，不增加面试完成延迟

Session finish 只发布不含内容的 opaque event。Worker 在独立 lease/fencing
所有权下读取权威数据、生成有限 proposal 并幂等落库。提案失败不得让已完成
面试回到未完成状态。

### Decision 10：本阶段不提供消费开关

EffectiveMemoryConfig 可以表达 disabled、write_shadow 和 read_shadow。
consume 值在 v1 preflight 中必须被拒绝。这样即使运维误配，也不能把 personal
fact 注入 Prompt。

---

## 5. 任务依赖图

~~~text
Task 0  基线、变更所有权与安全默认值
  ├── Task 1  退休历史静态 HTML 测试基线
  │     └── Task 2  拆分并稳定 Reference UI 浏览器测试
  ├── Task 3  隔离 live PostgreSQL migration/runtime 验证
  │     ├── Task 4  删除故障注入与 tombstone replay
  │     ├── Task 6  持久化聚合记忆指标
  │     └── Task 11 Principal Fact PostgreSQL store
  ├── Task 5  Knowledge P1 覆盖
  ├── Task 7  长上下文质量评测
  └── Task 9  长期记忆配置与领域契约
        └── Task 10 Principal identity 与 consent
              └── Task 11 Principal Fact store/migration
                    └── Task 12 Write-only proposal extraction

Task 4 + Task 11 + Task 12
  └── Task 13 Fact 生命周期与删除传播

Task 6 + Task 7 + Task 13
  └── Task 14 Bounded read-shadow

Task 2 + Task 3 + Task 5 + Task 6 + Task 7
  └── Task 8 Budget Shadow 操作准备

Task 4 + Task 6 + Task 8 + Task 14
  └── Task 15 隐私、安全与隔离审计
        └── Task 16 阶段 acceptance gate
              └── Task 17 完整回归与发布记录
~~~

允许并行的主要分支：

- Task 1、Task 3、Task 5、Task 7、Task 9 可在 Task 0 后并行。
- Task 4 和 Task 6 可在 Task 3 后并行。
- Task 8 与 Task 12/13 可在不编辑重叠文件时并行。
- Task 14 必须等待持久指标和 fact 生命周期稳定。
- Task 11 的 contract 和 in-memory store 设计可在 Task 9/10 完成后准备，
  但它对 app/services/postgres_runtime_migrations.py 的修改、PostgreSQL store
  实现和 pg_runtime 测试必须等待 Task 3 完成。Task 3 与 Task 11 不得并行编辑
  migration registry；执行者必须先合并/重读 Task 3 的最终 migration 验证框架。

---

## 6. 验证约定

Python focused/full suite：

~~~powershell
& 'F:\python3.11\python.exe' -m pytest -q
~~~

浏览器测试必须显式提供 Python：

~~~powershell
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
~~~

前端 build：

~~~powershell
npm.cmd run build:frontend
~~~

静态检查：

~~~powershell
& 'F:\python3.11\python.exe' -m compileall app scripts tests
git diff --check
~~~

PostgreSQL 测试必须满足：

- 使用 pytest pg_runtime marker。
- 使用 tests/postgres_support.py 的隔离配置。
- table prefix 包含阶段、时间或随机后缀。
- 运行前验证 database fingerprint。
- 运行后验证 acceptance-specific table、连接、worker 和 listener 已清理。
- database 不可用时记录 NOT_RUN，不得把 skip 解释为通过。

每个任务应从 characterization/failing test 开始，完成 focused test 后再进入
下一任务。提交是执行建议，不授权宽泛 stage；只允许添加当前任务拥有的文件。

---

## Task 0：冻结基线、变更所有权和安全默认值

**目的：** 在下一阶段修改前，记录真实基线，防止把用户已有改动、上一阶段实现
和本阶段改动混在一起。

**文件：**

- Create: docs/memory-validation-long-term-foundation-acceptance.md
- Create: tests/test_memory_validation_foundation_plan.py
- Modify: tests/test_local_v1_docs.py
- Reference: docs/memory-system-optimization-acceptance.md
- Reference: docs/interview-agent-memory-system-optimization-spec.md
- Reference: 本计划

### Step 1：记录 worktree ownership

在 acceptance record 的初始模板中记录：

- 当前 revision；
- dirty tracked/untracked/deleted 文件分类；
- 上一阶段 memory implementation 文件集合；
- 用户预先删除的历史 HTML；
- 当前已知测试失败；
- 本阶段禁止触碰的无关文件。

不要把 git diff 内容原样写入 artifact，避免记录业务文本或 secret。

### Step 2：增加计划契约测试

验证：

- 本计划 pin 到 Spec v1.1.1-draft；
- 所有引用的 MEM requirement ID 都存在于 Spec；
- 本计划没有创造新的 MEM-* ID；
- 长期记忆 consumption 明确为 BLOCKED；
- safe defaults 均为 disabled；
- app/test*.html 不在本计划的 Create/Modify 列表中。

### Step 3：建立初始红灯基线

分别运行：

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_utf8_text_contract.py -q
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
~~~

预期：准确复现历史 HTML 缺失和 report-actions timeout。记录失败测试名，不记录
页面或候选内容。

### Step 4：冻结配置默认值

扩展 docs/config contract，证明长期记忆相关配置不存在时等价于 disabled，
write-shadow/read-shadow 不会因旧环境变量隐式启用。

### Step 5：运行 focused contract

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_validation_foundation_plan.py tests/test_local_v1_docs.py -q
~~~

### Step 6：建议提交

~~~powershell
git add docs/memory-validation-long-term-foundation-acceptance.md tests/test_memory_validation_foundation_plan.py tests/test_local_v1_docs.py
git commit -m "docs(memory): establish validation and long-term foundation baseline"
~~~

---

## Task 1：退休历史静态 HTML 测试基线

**目的：** 让测试契约与当前 React 产品入口一致，不恢复已经删除的双前端页面，
同时保留仍有价值的安全、UTF-8、运行时 hook 和无外部 CDN 断言。

**文件：**

- Modify: tests/test_static_report_ui.py
- Modify: tests/test_utf8_text_contract.py
- Modify: tests/test_react_frontend.py
- Modify: tests/test_page_routes.py
- Modify: tests/test_reference_ui_artifact.py
- Modify: docs/frontend-modification-guide.md
- Modify: docs/memory-validation-long-term-foundation-acceptance.md

### Step 1：分类旧断言

把 tests/test_static_report_ui.py 中的断言分为：

1. 已过期：只证明 app/test*.html 存在或包含旧 DOM。
2. 可迁移到 React source contract：路由、可访问性、UTF-8、无 CDN、无 mock
   runtime value。
3. 可迁移到 API contract：真实路由、数据绑定和错误处理。
4. 必须由 Playwright 验证：布局、键盘、导航、响应式和动态状态。
5. 仍适用于 app/static/*.js 兼容资产：只测试 JS 自身，不再要求对应 HTML。

### Step 2：先添加迁移后的测试

将有价值断言移入 tests/test_react_frontend.py、tests/test_page_routes.py、
tests/test_reference_ui_artifact.py 或 browser suite。新测试通过前，不删除旧断言。

### Step 3：移除已过期文件存在性契约

从 tests/test_static_report_ui.py 和 tests/test_utf8_text_contract.py 中移除对
app/test0.html 至 app/test4.html、app/test-help.html 的读取。不得使用 skip、
xfail 或空文件占位来获得绿色结果。

### Step 4：更新文档

明确 frontend/src 是产品 UI source of truth；app/static/*.js 只保留兼容和
source contract；已删除 HTML 属于历史实现，不再是部署入口。

### Step 5：运行 focused tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_utf8_text_contract.py tests/test_react_frontend.py tests/test_page_routes.py tests/test_reference_ui_artifact.py -q
~~~

预期：全部通过，且仓库中不重新出现 app/test*.html。

### Step 6：建议提交

~~~powershell
git add tests/test_static_report_ui.py tests/test_utf8_text_contract.py tests/test_react_frontend.py tests/test_page_routes.py tests/test_reference_ui_artifact.py docs/frontend-modification-guide.md docs/memory-validation-long-term-foundation-acceptance.md
git commit -m "test(frontend): retire deleted static html contracts"
~~~

---

## Task 2：拆分并稳定 Reference UI 浏览器测试

**目的：** 把一个跨六路由、跨多个 viewport、共享大量状态的巨型测试拆成可定位、
可重试、互不污染的 route-level tests。

**文件：**

- Modify: tests/browser/reference-ui.spec.js
- Create: tests/browser/reference-ui-geometry.js
- Create: tests/browser/prep-ui.spec.js
- Create: tests/browser/interview-ui.spec.js
- Create: tests/browser/report-processing-ui.spec.js
- Create: tests/browser/report-detail-ui.spec.js
- Create: tests/browser/reports-ui.spec.js
- Create: tests/browser/help-ui.spec.js
- Modify: tests/browser_support_app.py
- Modify: scripts/run_browser_tests.js
- Modify: docs/memory-validation-long-term-foundation-acceptance.md

### Step 1：抽取只读 geometry helper

将 expectGeometry 和公共 viewport 定义移到 helper。Helper 只能读取页面状态，
不得 seed report、改变路由或持有跨测试 session。

### Step 2：每个路由独立准备 fixture

每个 spec 自己创建所需的 session/report，测试结束后由 support app 清理。
report detail 测试必须先轮询 report API 到 completed，再等待页面明确的 loading
终态；不能仅依赖 .report-actions 作为后端完成信号。

### Step 3：缩小断言职责

- prep：编辑器、source、计划、responsive。
- interview：conversation、draft、SSE recovery、memory assistance。
- report-processing：progress 和 retry。
- report-detail：report actions、score/evidence rendering。
- reports：filter、requeue、navigation。
- help：内容、导航和 geometry。

### Step 4：增加故障诊断

失败时保留 Playwright trace 和 route 名，不保留真实业务 payload。每个 spec
可独立通过 grep 或文件名运行。

### Step 5：运行 browser tests

~~~powershell
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
~~~

预期：完整 browser suite 通过；结束后 4173、8011 无残留 listener。

### Step 6：建议提交

~~~powershell
git add tests/browser/reference-ui.spec.js tests/browser/reference-ui-geometry.js tests/browser/prep-ui.spec.js tests/browser/interview-ui.spec.js tests/browser/report-processing-ui.spec.js tests/browser/report-detail-ui.spec.js tests/browser/reports-ui.spec.js tests/browser/help-ui.spec.js tests/browser_support_app.py scripts/run_browser_tests.js docs/memory-validation-long-term-foundation-acceptance.md
git commit -m "test(browser): isolate reference ui route acceptance"
~~~

---

## Task 3：执行隔离 live PostgreSQL migration/runtime 验证

**目的：** 将上一阶段被 skip 的 pg_runtime 合同变成真实执行证据，验证 migration
顺序、validate-only store、连接清理、并发约束和历史兼容性。

**文件：**

- Create: scripts/memory_postgres_validation.py
- Create: tests/test_memory_postgres_validation.py
- Modify: tests/postgres_support.py
- Modify: tests/test_postgres_runtime_migrations.py
- Modify: tests/test_postgres_session_store.py
- Modify: tests/test_postgres_question_memory_index.py
- Modify: tests/test_postgres_session_deletion.py
- Modify: tests/test_context_artifact_store_postgres.py
- Modify: tests/test_dual_langgraph_canary_postgres.py
- Modify: docs/local-v1-runbook.md
- Modify: docs/memory-validation-long-term-foundation-acceptance.md

### Step 1：增加安全 preflight

脚本必须验证：

- 显式传入 test database；
- database fingerprint 不匹配 production denylist；
- table prefix 合法且唯一；
- schema_mode 由 migration 阶段 create/migrate，runtime 阶段 validate；
- connection capacity 足够；
- 不输出 DSN、password 或 host credential。

### Step 2：按正式顺序应用 migration

至少覆盖：

1. 现有 runtime 基础 schema。
2. memory_session_policy_v1。
3. question_memory_index_v1。
4. session_deletion_v1。
5. 本计划后续新增 migration 在各自任务中追加。

Migration 必须幂等；重复 migrate 不产生额外表、索引或错误。

### Step 3：验证历史兼容性

构造 legacy、langgraph-v1、langgraph-v2 business session 和 checkpoint，验证：

- 历史 row 可读取；
- memory_policy_version 兼容赋值稳定；
- runtime validate 不执行 DDL；
- v2 Question Memory index 事务和 partial uniqueness 生效；
- session deletion job lease/fencing 生效。

### Step 4：验证并发和进程丢失

至少覆盖：

- 两个 index activation 并发；
- deletion job 双 claim；
- stale lease complete；
- artifact complete 与 owner-ref 绑定边界；
- transaction rollback 后无半写 entry；
- connection 返回正确 pool。

### Step 5：清理并证明清理结果

只删除当前隔离 prefix 下的测试对象。解析出的绝对目标必须仍属于批准的测试
schema。记录 table count、open connection count 和 worker cleanup，不记录 row
内容。

### Step 6：运行 live PostgreSQL tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_runtime_migrations.py tests/test_postgres_session_store.py tests/test_postgres_question_memory_index.py tests/test_postgres_session_deletion.py tests/test_context_artifact_store_postgres.py tests/test_dual_langgraph_canary_postgres.py tests/test_memory_postgres_validation.py -q -m pg_runtime
& 'F:\python3.11\python.exe' -m scripts.memory_postgres_validation
~~~

预期：所有选择的 pg_runtime 测试实际执行，不允许以全 skip 通过。

### Step 7：建议提交

~~~powershell
git add scripts/memory_postgres_validation.py tests/test_memory_postgres_validation.py tests/postgres_support.py tests/test_postgres_runtime_migrations.py tests/test_postgres_session_store.py tests/test_postgres_question_memory_index.py tests/test_postgres_session_deletion.py tests/test_context_artifact_store_postgres.py tests/test_dual_langgraph_canary_postgres.py docs/local-v1-runbook.md docs/memory-validation-long-term-foundation-acceptance.md
git commit -m "test(memory): validate memory stores on isolated postgres"
~~~

---

## Task 4：删除故障注入与备份 Tombstone Replay

**目的：** 证明 session 删除在每个 purge 边界中断后都能恢复，并为旧备份恢复后
重新执行删除提供受控 operator 工具。

**Spec coverage:** MEM-LCY-001 至 MEM-LCY-005、MEM-LCY-030 至
MEM-LCY-037。

**文件：**

- Modify: app/services/session_deletion.py
- Modify: app/services/session_deletion_worker.py
- Modify: app/services/postgres_session_deletion.py
- Modify: app/services/postgres_runtime_migrations.py
- Create: app/services/session_deletion_tombstones.py
- Create: scripts/replay_session_deletion_tombstones.py
- Modify: tests/test_session_deletion.py
- Modify: tests/test_postgres_session_deletion.py
- Create: tests/test_session_deletion_tombstone_replay.py
- Modify: docs/local-v1-runbook.md
- Modify: docs/memory-validation-long-term-foundation-acceptance.md

### Step 1：定义 replay contract

Tombstone 至少包含：

- schema_version；
- deletion_job_id；
- session locator；
- requested_at；
- completed_at；
- policy_version；
- replay status；
- integrity digest。

Session locator 是受保护的 operator data，不进入 metric、普通日志或 acceptance
artifact。Acceptance 只记录 tombstone count 和 digest verification result。

### Step 2：在所有 purge 边界注入进程丢失

覆盖：

1. mark deleting 后、job enqueue 前。
2. job claim 后、第一项 purge 前。
3. checkpoint purge 后。
4. workflow/generation purge 后。
5. Question Memory index/owner refs purge 后。
6. artifact orphan cleanup 前后。
7. business session/report purge 前后。
8. complete 写入前。

每次恢复都必须幂等，不能让 session 回到可变更状态。

### Step 3：实现受控 replay 工具

工具支持：

- validate-only；
- replay 单个 tombstone；
- replay bounded batch；
- dry-run safe counts；
- fencing-aware requeue；
- 失败后可重复执行；
- 输出稳定 error code，不输出 session/principal 内容。

### Step 4：验证旧备份恢复边界

测试过程：

1. 创建 session 并记录旧备份状态 fixture。
2. 请求并完成删除。
3. 恢复旧数据 fixture。
4. 导入删除之后保存的 operator tombstone ledger。
5. 执行 replay。
6. 证明 session、checkpoint、messages、reports、Question Memory、owner refs 和
   后续 Principal Memory source facts 均再次删除。

### Step 5：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_deletion.py tests/test_postgres_session_deletion.py tests/test_session_deletion_tombstone_replay.py -q
~~~

Live PostgreSQL fault matrix 必须在批准的隔离数据库中再运行一次。

### Step 6：建议提交

~~~powershell
git add app/services/session_deletion.py app/services/session_deletion_worker.py app/services/postgres_session_deletion.py app/services/postgres_runtime_migrations.py app/services/session_deletion_tombstones.py scripts/replay_session_deletion_tombstones.py tests/test_session_deletion.py tests/test_postgres_session_deletion.py tests/test_session_deletion_tombstone_replay.py docs/local-v1-runbook.md docs/memory-validation-long-term-foundation-acceptance.md
git commit -m "feat(memory): replay deletion tombstones after restore"
~~~

---

## Task 5：补齐 Knowledge Memory P1 覆盖

**目的：** 让 Question Memory 和后续追问可以依赖经过审核的正向、负向和边界
证据，禁止通过伪造 coverage metadata 绕过 readiness。

**Spec coverage:** MEM-KNW-001 至 MEM-KNW-010。

**文件：**

- Modify/Add: app/data/knowledge_v2 下经过审核的 corpus source
- Modify: app/data/knowledge_v2/manifest.json
- Modify: scripts/build_knowledge_manifest_v2.py
- Modify: scripts/load_knowledge_v2.py
- Modify: tests/golden/knowledge_retrieval_v2_pilot.json
- Modify: tests/test_knowledge_corpus_schema.py
- Modify: tests/test_knowledge_manifest_v2.py
- Modify: tests/test_knowledge_eval_dataset_v2.py
- Modify: tests/test_knowledge_eval_metrics_v2.py
- Modify: tests/test_grounded_knowledge_agent.py
- Modify: docs/stage-44b1-chinese-source-matrix.md
- Create: docs/memory-knowledge-p1-coverage-acceptance.md

### Step 1：冻结 required tag matrix

当前 required tags：

- fastapi；
- kafka；
- mysql；
- postgresql；
- python；
- redis；
- reliability；
- system-design。

每个 tag 至少需要两个经审核的 positive、两个 hard-negative、两个 boundary
案例。一个 chunk 可以覆盖多个 tag，但必须在 source matrix 中显式标注，不得
只靠模型推断计数。

postgresql 不是 mysql 的隐式别名。它已经存在于 CANONICAL_TAXONOMY、
LEGACY_KNOWLEDGE_COVERED_TAGS、P1_REQUIRED_COVERED_TAGS 和 corpus schema，
但当前 active manifest 的 canonical_tags 未包含它，因此本任务必须补齐
PostgreSQL 的独立 evidence-class coverage。八个 tag 对应 48 个 tag/class
审核计数；由于一个经审核 chunk 可以显式覆盖多个 tag，这不等于必须新增
48 个互不相同的 chunk。

### Step 2：增加真实、可引用语料

每条新增内容必须有：

- source provenance；
- license/使用许可；
- evidence class；
- role/tag；
- content SHA-256；
- reviewer；
- approved_at；
- corpus version。

不得为了满足数量门槛复制、轻微改写同一案例或写入候选人回答。

### Step 3：扩展 golden retrieval

覆盖：

- 正常命中；
- 明确不相关的 hard-negative；
- 正确技术在错误边界下的 boundary；
- 中文术语、英文标识符和混合 query；
- manifest drift；
- corpus hash mismatch；
- degraded grounding。

### Step 4：保持 readiness fail-closed

任何 required tag 缺少 evidence class 时：

- 应用可以启动；
- readiness 返回 knowledge coverage unavailable；
- deterministic interview 仍可用；
- Question Memory consumption 和长期记忆 consumption 保持禁止；
- 不允许静态 KNOWN_COVERED_TAGS 覆盖 manifest 结果。

增加回归测试，固定 P1_REQUIRED_COVERED_TAGS 包含 postgresql，并证明当前
manifest 在 PostgreSQL coverage 补齐前保持 readiness=false；不得把
PostgreSQL query 静默降级为 MySQL covered。

### Step 5：重建并验证 manifest

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_corpus_schema.py tests/test_knowledge_manifest_v2.py tests/test_knowledge_eval_dataset_v2.py tests/test_knowledge_eval_metrics_v2.py tests/test_grounded_knowledge_agent.py -q
~~~

### Step 6：建议提交

只 stage 经审核的 corpus、manifest、golden、tests 和 acceptance record。

~~~powershell
git commit -m "data(knowledge): establish reviewed p1 evidence coverage"
~~~

---

## Task 6：持久化隐私安全的聚合记忆指标

**目的：** 把当前 process-local InMemoryMemoryMetricStore 升级为可跨进程、
可跨重启观察的分钟/小时聚合指标，同时不创建 per-session drill-down。

**文件：**

- Create: app/ports/memory_metrics.py
- Modify: app/services/memory_metrics.py
- Create: app/services/postgres_memory_metrics.py
- Modify: app/services/postgres_runtime_migrations.py
- Modify: app/services/runtime.py
- Modify: app/api/routes.py
- Create: tests/test_postgres_memory_metrics.py
- Modify: tests/test_memory_metrics.py
- Modify: tests/test_runtime_provider.py
- Modify: tests/test_postgres_runtime_migrations.py
- Modify: tests/test_api.py
- Modify: docs/local-v1-runbook.md

### Step 1：定义 port 和聚合 schema

持久表按 bucket_start、bucket_width、metric_code、dimensions_sha256 唯一。
允许字段：

- operation；
- route；
- outcome；
- reason；
- policy_version；
- schema_version；
- language_bucket；
- shadow_mode；
- consumption_enabled；
- 聚合数值与 sample count。

禁止字段：

- session_id；
- principal_id；
- fact_id；
- question_id；
- source/artifact digest；
- prompt、回答、摘要和 excerpt。

### Step 2：直接 upsert bucket

Runtime 不先持久化原始 event。每次 publish 对当前 minute bucket 做原子累加。
PostgreSQL 不可用时：

- 面试业务 fail-open；
- readiness 标记 durable_metrics_available=false；
- process-local store 可继续提供诊断；
- 不得假装持久指标完整。

### Step 3：实现 rollup 与 retention

- minute bucket 保留 30 天；
- hour bucket 保留 180 天；
- rollup 幂等；
- 已 rollup minute bucket 可按 retention 清理；
- cleanup bounded；
- 所有时间使用 UTC。

这些是本计划的操作默认值。部署可在隐私评审后缩短；如确有跨季度趋势、
审计或容量分析需求，也可以在记录用途、访问边界、保留窗口和删除责任后，
由隐私/合规、SRE 与技术负责人共同审批延长。任何延长都必须更新 retention
policy version 和 acceptance record，不能通过普通环境变量静默延长。

### Step 4：扩展 aggregate endpoint

GET /api/runtime/memory-metrics 继续只在 trusted-local metrics gate 下可见。
响应增加 store_kind、data_complete 和 latest_bucket_at，不返回任何主键或内部
row locator。

### Step 5：增加并发与隐私测试

覆盖：

- 多 worker 同 bucket 原子累加；
- 不同 dimensions 分桶；
- duplicate publish 的幂等策略；
- rollup 重放；
- retention；
- low-sample language bucket；
- blocked key/value sentinel；
- PostgreSQL 异常不阻断业务。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_metrics.py tests/test_postgres_memory_metrics.py tests/test_runtime_provider.py tests/test_postgres_runtime_migrations.py tests/test_api.py -q
~~~

Live pg_runtime 测试必须在 Task 3 的隔离环境执行。

### Step 7：建议提交

~~~powershell
git add app/ports/memory_metrics.py app/services/memory_metrics.py app/services/postgres_memory_metrics.py app/services/postgres_runtime_migrations.py app/services/runtime.py app/api/routes.py tests/test_postgres_memory_metrics.py tests/test_memory_metrics.py tests/test_runtime_provider.py tests/test_postgres_runtime_migrations.py tests/test_api.py docs/local-v1-runbook.md
git commit -m "feat(memory): persist aggregate shadow metrics"
~~~

---

## Task 7：建立固定长上下文质量评测

**目的：** 在真实 Shadow 前，用固定 fixture 量化 deterministic selection、
Question Memory 和 proposal extraction 对关键事实的保留与失真。

**文件：**

- Create: app/services/memory_quality_eval.py
- Create: app/services/memory_quality_dataset.py
- Create: tests/golden/memory_long_context_v1.json
- Create: tests/test_memory_quality_dataset.py
- Create: tests/test_memory_quality_eval.py
- Create: scripts/evaluate_memory_quality.py
- Create: docs/memory-long-context-quality-acceptance.md
- Modify: tests/test_question_memory.py
- Modify: tests/test_question_memory_recovery.py
- Modify: tests/test_context_language.py

### Step 1：定义 fixture 维度

每个 case 使用 20 至 50 轮，并覆盖：

- zh_hans；
- en；
- mixed；
- 数字、百分比、日期和容量；
- Python/Java/SQL 标识符；
- 否定和双重否定；
- 候选人纠正上一回答；
- 同一问题追加回答；
- unresolved topic；
- skipped question；
- provider failure 和 deterministic fallback；
- Question Memory supersede；
- 删除中的 session；
- Prompt Injection 文本。

所有内容均为合成数据，不使用真实候选人材料。

### Step 2：区分硬不变量与语义质量

硬不变量必须 100%：

- mandatory current answer 保留；
- source anchor/excerpt digest 校验；
- 数字和代码标识符保持；
- corrected answer 优先；
- deleted/revoked source 不进入候选集；
- Question Memory 不进入 scoring Evidence；
- personal fact 不进入 Prompt；
- cross-principal contamination 为零；
- known-over-budget provider call 为零。

语义质量初始门槛：

- golden atomic fact recall 不低于 95%；
- unresolved topic recall 不低于 90%；
- unsupported atomic claim rate 为 0；
- deterministic 与 memory route 的关键结论冲突为 0。

自由自然语言等价仍不声称可完全自动证明；人工或受控 model judge 结果必须与
可编程硬门禁分开报告。

### Step 3：实现 deterministic evaluator

仓库默认 evaluator 不调用真实模型。它比较：

- expected atomic facts；
- selected exact messages；
- Question Memory claims/excerpts；
- numbers/identifiers；
- supersede chain；
- token estimates；
- route 和 fallback。

### Step 4：提供可选真实模型入口

scripts/evaluate_memory_quality.py 支持显式 --real-provider，但默认拒绝运行。
只有获得 provider、数据集、预算和输出位置授权后才能开启。真实模型报告也不得
包含原始 prompt 或完整回答。

### Step 5：运行 deterministic evaluation

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_quality_dataset.py tests/test_memory_quality_eval.py tests/test_question_memory.py tests/test_question_memory_recovery.py tests/test_context_language.py -q
& 'F:\python3.11\python.exe' -m scripts.evaluate_memory_quality --deterministic
~~~

### Step 6：建议提交

~~~powershell
git add app/services/memory_quality_eval.py app/services/memory_quality_dataset.py tests/golden/memory_long_context_v1.json tests/test_memory_quality_dataset.py tests/test_memory_quality_eval.py scripts/evaluate_memory_quality.py docs/memory-long-context-quality-acceptance.md tests/test_question_memory.py tests/test_question_memory_recovery.py tests/test_context_language.py
git commit -m "test(memory): add long-context semantic quality gate"
~~~

---

## Task 8：准备 Budget Shadow 操作路径

**目的：** 在不改变模型输入和业务行为的前提下，收集真实预算估算、语言误差、
route、latency 和 fallback 的聚合数据，并提供自动停止条件。

**依赖：** Task 2、Task 3、Task 5、Task 6、Task 7。

**文件：**

- Create: scripts/memory_budget_shadow.py
- Create: tests/test_memory_budget_shadow.py
- Modify: app/services/memory_config.py
- Modify: app/services/context_runtime.py
- Modify: app/services/memory_metrics.py
- Modify: app/api/routes.py
- Create: docs/memory-budget-shadow-runbook.md
- Modify: docs/local-v1-runbook.md
- Modify: .env.example

### Step 1：实现 validate-only preflight

在任何 shadow 配置改变前验证：

- durable aggregate metrics 可用；
- live PostgreSQL validation 已通过；
- Knowledge P1 readiness 可用；
- long-context deterministic gate 通过；
- browser/full Python baseline 绿色；
- Question Memory consumption=false；
- long-term memory consumption 不存在；
- target environment 和 observation window 已显式提供。

### Step 2：Shadow 只观察，不改变选择

Budget Shadow 可以计算 hypothetical selection 和 rendered prompt estimate，
但实际 provider 输入仍使用当前未 enforcement 路径。事件只发布聚合值。

### Step 3：定义自动停止条件

默认停止条件：

- 任意 known-over-budget provider call；
- 任意 mandatory current content loss；
- 任意隐私审计命中；
- budget resolve/config conflict；
- follow-up error rate 在样本数至少 200 时高于 baseline 0.5 个百分点；
- P95 follow-up latency 在样本数至少 200 时高于 baseline 20%；
- metric gap 或 data_complete=false；
- PostgreSQL/metric store 不可用超过一个 observation bucket。

低样本只允许继续观察，不允许扩大流量。

### Step 4：输出聚合观察记录

输出：

- observation ID；
- config digest；
- time window；
- language bucket sample status；
- estimator error direction；
- route counts；
- fallback counts；
- latency/cost aggregates；
- stop-gate status。

禁止输出 session、principal、prompt、answer 和 source identifiers。

### Step 5：运行仓库测试

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_budget_shadow.py tests/test_memory_metrics.py tests/test_memory_config.py tests/test_context_runtime.py -q
~~~

不要在本任务中实际开启环境 Shadow。

### Step 6：建议提交

~~~powershell
git add scripts/memory_budget_shadow.py tests/test_memory_budget_shadow.py app/services/memory_config.py app/services/context_runtime.py app/services/memory_metrics.py app/api/routes.py docs/memory-budget-shadow-runbook.md docs/local-v1-runbook.md .env.example
git commit -m "feat(memory): prepare budget shadow observation"
~~~

---

## Task 9：建立长期记忆配置与领域契约

**目的：** 为 Principal Memory 建立独立、默认关闭、无法误入 consume 的配置和
领域模型，先用测试锁定允许事实、状态和证据边界。

**Spec coverage:** MEM-ARCH-009、MEM-LTM-001 至 MEM-LTM-007、
MEM-LTM-010 至 MEM-LTM-014。

**文件：**

- Create: app/ports/principal_memory.py
- Create: app/services/principal_memory_contracts.py
- Modify: app/services/memory_config.py
- Modify: app/services/runtime.py
- Modify: app/services/postgres_schema_contract.py
- Create: tests/test_principal_memory_contracts.py
- Modify: tests/test_memory_config.py
- Modify: tests/test_memory_config_source_audit.py
- Modify: tests/test_runtime_ports.py
- Modify: .env.example
- Modify: docs/local-v1-runbook.md

### Step 1：扩展 EffectiveMemoryConfig

增加 frozen LongTermMemoryConfig：

~~~python
class LongTermMemoryConfig(FrozenMemoryModel):
    mode: Literal[
        "disabled",
        "write_shadow",
        "read_shadow",
    ] = "disabled"
    write_shadow_enabled: bool = False
    read_shadow_enabled: bool = False
    trusted_local_api_enabled: bool = False
    consent_policy_version: str = "principal-memory-consent-v1"
    fact_schema_version: str = "principal-memory-fact-v1"
    taxonomy_version: str = "principal-memory-taxonomy-v1"
    max_proposals_per_session: int = 8
    max_shadow_facts: int = 6
    max_shadow_tokens: int = 800
    proposal_retention_days: int = 30
    active_fact_default_days: int = 365
~~~

Loader 不接受 consume。若环境中出现 MEMORY_LONG_TERM_MODE=consume，preflight
必须失败，而不是降级成 read_shadow。

### Step 2：定义 principal fact contract

PrincipalMemoryFact 至少包含：

~~~python
class PrincipalMemoryFact(BaseModel):
    schema_version: Literal["principal-memory-fact-v1"]
    fact_id: str
    deployment_id: str
    principal_id: str
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_fact: str
    confidence: float
    authority: Literal["user_declared", "model_proposed"]
    canonicalization_version: Literal[
        "principal-memory-canonical-json-v1"
    ]
    status: Literal[
        "proposed",
        "active",
        "rejected",
        "superseded",
        "expired",
        "revoked",
        "deleted",
    ]
    source_session_id: str
    source_question_id: str | None
    source_manifest_sha256: str
    source_excerpt_sha256: str
    consent_policy_version: str
    taxonomy_version: str
    user_confirmed: bool
    created_at: datetime
    confirmed_at: datetime | None
    expires_at: datetime | None
    supersedes_fact_id: str | None
    revoked_at: datetime | None
    deleted_at: datetime | None
~~~

这里新增的状态字段是对 MEM-LTM-003、004、006 的实现细化，不创建新的 MEM
requirement ID。具体而言，rejected 和 superseded 是 Spec 基础状态集合之上的
实现细化：rejected 表示用户或规则明确拒绝 proposal；superseded 表示同一
fact key 的旧确认记录已被新的确认来源替代。它们需要在实现和 migration 中
持久化，但不要求为此创建新的 MEM 编号。Spec 的 deployment_id +
principal_id 边界保持不变。
confidence 保持 Spec 的 0 至 1 浮点语义；只有在聚合 metric 中才转换为整数
basis points，避免改变领域字段含义。

### Step 3：定义 normalized_fact taxonomy

normalized_fact 是 canonical JSON string。只允许：

- interview_language：zh_hans、en、mixed；
- target_role_family：版本化岗位分类；
- focus_topic：受控 skill taxonomy；
- confirmed_skill：受控 skill taxonomy；
- learning_goal：受控 skill taxonomy；
- accessibility_preference：受批准的 UI/交互枚举。

拒绝任意自由文本、公司名、项目名、人格标签、评分和招聘结论。

Canonicalization 必须由一个共享函数实现，禁止调用方各自序列化。v1 规则为：

~~~python
import json
import unicodedata


def canonical_principal_fact(value: dict[str, str]) -> str:
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = unicodedata.normalize("NFC", key)
        normalized_value = unicodedata.normalize("NFC", item)
        validate_canonical_scalar(normalized_key)
        validate_canonical_scalar(normalized_value)
        if normalized_key in normalized:
            raise ValueError("principal fact keys collide after NFC")
        normalized[normalized_key] = normalized_value
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
~~~

输入只允许 object 和 allowlisted string scalar；不允许 array、float、NaN、
Infinity、重复语义键、控制字符或 lone surrogate。键名和值在序列化前都做
Unicode NFC。输出不缩进、不带多余空格、按键排序并使用 UTF-8。Fact identity
必须包含 canonicalization_version；未来改变规则时发布新版本，不能静默改变
已有 fact_id。

### Step 4：定义 port

PrincipalMemoryFactStore protocol 至少提供：

- propose；
- get；
- list_by_principal；
- confirm；
- reject；
- supersede；
- revoke；
- expire_due；
- mark_deleted；
- purge_by_session；
- purge_by_principal；
- list_shadow_eligible。

所有方法显式接收 deployment_id 和 principal_id；禁止只有 fact_id 的无边界读取。

### Step 5：增加 contract tests

覆盖：

- mode 默认 disabled；
- consume 配置被拒绝；
- taxonomy allowlist；
- status/timestamp 一致性；
- model proposal 不能直接 active；
- active 必须 user_confirmed；
- source digest 必填；
- expired/revoked/deleted 不可 shadow eligible；
- deployment/principal 参数不可省略；
- public vector store 不依赖 principal_memory module。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_memory_contracts.py tests/test_memory_config.py tests/test_memory_config_source_audit.py tests/test_runtime_ports.py -q
~~~

### Step 7：建议提交

~~~powershell
git add app/ports/principal_memory.py app/services/principal_memory_contracts.py app/services/memory_config.py app/services/runtime.py app/services/postgres_schema_contract.py tests/test_principal_memory_contracts.py tests/test_memory_config.py tests/test_memory_config_source_audit.py tests/test_runtime_ports.py .env.example docs/local-v1-runbook.md
git commit -m "feat(memory): define principal memory contracts"
~~~

---

## Task 10：建立 Principal Identity 与 Consent 边界

**目的：** 确保每次长期记忆写入和 Shadow 读取都有可信 principal identity 和
版本化授权；身份不可从简历、浏览器指纹或模型输出推断。

**Spec coverage:** MEM-LTM-001、MEM-LTM-002、MEM-LTM-010、
MEM-SEC-001 至 MEM-SEC-005。

**文件：**

- Create: app/ports/principal_identity.py
- Create: app/ports/principal_memory_consent.py
- Create: app/services/principal_identity.py
- Create: app/services/principal_memory_consent.py
- Create: app/services/in_memory_principal_memory_consent.py
- Modify: app/services/runtime.py
- Modify: app/api/routes.py
- Modify: .env.example
- Create: tests/test_principal_identity.py
- Create: tests/test_principal_memory_consent.py
- Modify: tests/test_api.py
- Modify: tests/test_memory_config.py
- Modify: tests/test_local_v1_docs.py
- Modify: tests/test_runtime_provider.py

### Step 1：定义 PrincipalIdentityResolver

Resolver 返回：

~~~python
class PrincipalIdentity(BaseModel):
    deployment_id: str
    principal_id: str
    assurance: Literal["test", "trusted_local", "authenticated"]
    resolved_at: datetime
~~~

默认实现是 NullPrincipalIdentityResolver，返回 unavailable。测试使用显式 fake。
Trusted-local resolver 只有在独立 gate 打开且 principal 明确提供时可用。

### Step 2：禁止自动身份合并

增加 source audit 和 tests，确保以下字段不会用于 principal resolution：

- resume hash；
- email/phone 文本；
- browser localStorage random ID；
- IP/User-Agent；
- candidate name；
- embedding similarity；
- model-generated identity。

### Step 3：定义 Consent record

~~~python
class PrincipalMemoryConsent(BaseModel):
    schema_version: Literal["principal-memory-consent-v1"]
    deployment_id: str
    principal_id: str
    policy_version: str
    allowed_purposes: list[
        Literal["proposal_write", "fact_storage", "read_shadow"]
    ]
    granted_at: datetime
    revoked_at: datetime | None
~~~

Consent 不能从全局配置推断，也不能由管理员默认替用户授予。

### Step 4：实现授权决策

每次 proposal/write/read-shadow 都在操作时读取当前 consent，而不是只在 session
创建时缓存。撤回后：

- 新 proposal 不再生成；
- queued effect 在执行前取消；
- read-shadow 返回 disabled；
- 已有 facts 进入 principal purge 或显式保留选择流程；
- 任何 failure 不输出 principal ID。

### Step 5：限制 HTTP 暴露

当前没有正式账号系统，因此普通 candidate route 不挂载长期记忆 API。测试 API
或 trusted-local 管理端点必须同时满足：

- MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=true；
- PrincipalIdentityResolver 可用；
- consent policy 匹配；
- response 不返回内部 source identifiers。

具体挂载在 /api/runtime/principal-memory 下，只提供测试和本地操作所需的
consent、list、confirm、reject、revoke、delete endpoints。Confirm/reject/
revoke 使用受控 fact key + expected version，不要求把数据库 fact_id 暴露给
客户端。Principal ID 不允许出现在 path、query 或 request body 中，必须由
PrincipalIdentityResolver 解析。默认配置下 router 不挂载或统一返回 404；
测试使用 FakePrincipalIdentityResolver，不通过 header/body 临时伪造身份。

正式用户自助 UI 留到具备 authenticated principal 的后续产品阶段。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_identity.py tests/test_principal_memory_consent.py tests/test_api.py tests/test_memory_config.py tests/test_local_v1_docs.py tests/test_runtime_provider.py -q
~~~

### Step 7：建议提交

~~~powershell
git add app/ports/principal_identity.py app/ports/principal_memory_consent.py app/services/principal_identity.py app/services/principal_memory_consent.py app/services/in_memory_principal_memory_consent.py app/services/runtime.py app/api/routes.py .env.example tests/test_principal_identity.py tests/test_principal_memory_consent.py tests/test_api.py tests/test_memory_config.py tests/test_local_v1_docs.py tests/test_runtime_provider.py
git commit -m "feat(memory): require identity and consent for personal memory"
~~~

---

## Task 11：实现 Principal Fact Store 与 Migration

**目的：** 建立独立、不可与公共知识混用、支持状态转换和 source/session purge
的 in-memory/PostgreSQL fact store。

**依赖：** Task 3、Task 9、Task 10。领域 contract 和 in-memory 测试可以在
Task 9/10 后准备，但 migration registry、PostgreSQL store 和 pg_runtime
验证必须在 Task 3 完成后开始；不得与 Task 3 并行修改
app/services/postgres_runtime_migrations.py。

**Spec coverage:** MEM-LTM-003 至 MEM-LTM-007、MEM-LTM-010、
MEM-LTM-014。

**文件：**

- Create: app/services/in_memory_principal_memory.py
- Create: app/services/postgres_principal_memory.py
- Create: app/services/postgres_principal_memory_consent.py
- Modify: app/services/postgres_runtime_migrations.py
- Modify: app/services/postgres_schema_contract.py
- Modify: app/services/runtime.py
- Create: tests/test_in_memory_principal_memory.py
- Create: tests/test_postgres_principal_memory.py
- Create: tests/test_postgres_principal_memory_consent.py
- Modify: tests/test_postgres_runtime_migrations.py
- Modify: tests/test_postgres_store_provider_injection.py

### Step 1：定义 proposal identity

fact_id 由以下内容确定性派生：

~~~text
deployment_id
+ principal_id
+ fact_type
+ normalized_fact
+ source_manifest_sha256
+ source_excerpt_sha256
+ consent_policy_version
+ taxonomy_version
+ canonicalization_version
→ SHA-256
~~~

相同 source 和事实重放得到同一 proposal；source、taxonomy 或 consent version
变化得到新 ID。

### Step 2：新增 migration principal_memory_v1

表至少包括：

- principal_memory_consents；
- principal_memory_facts；
- principal_memory_effects 或复用现有 runtime outbox；
- 必要索引和 check constraints。

PostgresPrincipalMemoryConsentStore 实现 Task 10 定义的 consent port；
PostgresPrincipalMemoryFactStore 实现 fact port。两者可以共享同一 connection
domain，但不能通过全局变量共享未提交 transaction。

约束：

- deployment_id + principal_id + fact_id 唯一；
- active 状态必须 user_confirmed；
- timestamp 与 status 一致；
- source digest 为 64 位小写十六进制；
- normalized_fact 长度有界；
- 不能建立 vector 列；
- 不能外键到 public knowledge corpus。

### Step 3：实现事务状态转换

所有 transition 使用 compare-and-set 或 SELECT FOR UPDATE：

- proposed → active/rejected/deleted；
- active → superseded/expired/revoked/deleted；
- terminal 状态不能恢复为 active；
- supersedes 指向同 deployment/principal/fact_type；
- 重复 confirm/revoke/delete 幂等；
- stale expected version 返回稳定 conflict。

### Step 4：实现 bounded query

list_by_principal 和 list_shadow_eligible 必须：

- 同时过滤 deployment/principal；
- 有固定 limit；
- 有稳定 order；
- 默认排除 terminal 状态；
- 不扫描自由文本；
- 返回 domain model，不返回 raw database row。

### Step 5：增加 isolation 和 concurrency tests

覆盖：

- 两 principal 使用相同 normalized_fact；
- 跨 deployment 访问；
- 并发 proposal dedupe；
- 并发 confirm/revoke；
- stale transition；
- expire batch；
- purge_by_session；
- purge_by_principal；
- schema_mode validate；
- connection ownership。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_in_memory_principal_memory.py tests/test_postgres_principal_memory.py tests/test_postgres_principal_memory_consent.py tests/test_postgres_runtime_migrations.py tests/test_postgres_store_provider_injection.py -q
~~~

pg_runtime 部分必须在 Task 3 隔离数据库真实执行。

### Step 7：建议提交

~~~powershell
git add app/services/in_memory_principal_memory.py app/services/postgres_principal_memory.py app/services/postgres_principal_memory_consent.py app/services/postgres_runtime_migrations.py app/services/postgres_schema_contract.py app/services/runtime.py tests/test_in_memory_principal_memory.py tests/test_postgres_principal_memory.py tests/test_postgres_principal_memory_consent.py tests/test_postgres_runtime_migrations.py tests/test_postgres_store_provider_injection.py
git commit -m "feat(memory): persist isolated principal fact proposals"
~~~

---

## Task 12：实现 Write-only Fact Proposal Extraction

**目的：** 在明确 consent 下，从权威 session source 生成数量受限、
non-authoritative、默认 proposed 的个人事实提案，不影响 finish latency，也不
自动激活。

**依赖：** Task 7、Task 10、Task 11。

**文件：**

- Create: app/services/principal_memory_proposals.py
- Create: app/services/principal_memory_extractor.py
- Create: app/services/principal_memory_tasks.py
- Modify: app/services/runtime_domain_events.py
- Modify: app/services/runtime_outbox_dispatcher.py
- Modify: app/services/runtime_outbox_worker.py
- Modify: app/services/interview_workflow.py
- Modify: app/services/session.py
- Modify: app/services/runtime.py
- Create: tests/test_principal_memory_proposals.py
- Create: tests/test_principal_memory_extractor.py
- Create: tests/test_principal_memory_tasks.py
- Modify: tests/test_runtime_outbox_dispatcher.py
- Modify: tests/test_interview_workflow_consumer.py

### Step 1：定义 opaque event

新增 principal_memory_proposal_requested_v1。Event 只包含：

- effect ID；
- deployment locator；
- opaque principal locator；
- session locator；
- consent policy version；
- source state version；
- requested_at。

Runtime event metric 和 trace 不得包含 locator 原值；只使用 operation、outcome、
policy version 和 aggregate count。

### Step 2：只在安全条件下 enqueue

条件全部满足才 enqueue：

- session finished；
- principal identity 可用；
- consent 允许 proposal_write；
- mode 为 write_shadow 或 read_shadow；
- source session 未 deleting/deleted；
- source policy/version 可支持；
- 同一 session/effect 未成功完成。

Enqueue 失败不能把 session finish 回滚。

### Step 3：Worker 读取权威 source

Worker 在执行时重新校验 identity、consent、session deletion state 和 source
version。它可以使用 Question Memory 作为候选提示，但必须回到原始 message
验证 source excerpt digest；不能仅从 summary 生成 fact。

Extractor 的结构化结果必须携带 exact excerpt 和 source segment locator。
Validator 验证 excerpt 是对应权威 message 的精确子串，计算
source_excerpt_sha256 后只把 digest 写入 fact store；原始 excerpt 不进入索引、
metric、trace 或 acceptance artifact。

### Step 4：限定输出

Extractor 每 session 最多生成 max_proposals_per_session。允许：

- 明确表达的 interview preference；
- 明确表达并可确认的 skill statement；
- 明确 learning goal；
- 明确 accessibility preference。

Accessibility preference 只能来自用户直接声明，不允许根据输入速度、措辞、
设备、错误次数或模型观察推断。

禁止：

- 推断人格、情绪、诚信；
- 从评分反推 skill；
- 从简历或回答推断敏感属性；
- 复制自由文本；
- 创建招聘结论；
- 创建公共 Knowledge entry。

### Step 5：所有模型输出保持 proposed

authority=model_proposed、user_confirmed=false、status=proposed。
即使 confidence 很高也不能自动 active。直接由用户提交的受控 preference 可在
后续 confirm service 中采用 explicit-rule activation，但不能由 extractor
自行决定。

### Step 6：故障和重放

覆盖：

- outbox duplicate；
- worker lease loss；
- provider timeout；
- malformed schema；
- unsupported fact type；
- consent revoked after enqueue；
- session deleted after enqueue；
- process loss before/after fact insert；
- max proposal bound；
- idempotent replay。

### Step 7：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_memory_proposals.py tests/test_principal_memory_extractor.py tests/test_principal_memory_tasks.py tests/test_runtime_outbox_dispatcher.py tests/test_interview_workflow_consumer.py -q
~~~

仓库测试使用 fake extractor，不调用真实 provider。

### Step 8：建议提交

~~~powershell
git add app/services/principal_memory_proposals.py app/services/principal_memory_extractor.py app/services/principal_memory_tasks.py app/services/runtime_domain_events.py app/services/runtime_outbox_dispatcher.py app/services/runtime_outbox_worker.py app/services/interview_workflow.py app/services/session.py app/services/runtime.py tests/test_principal_memory_proposals.py tests/test_principal_memory_extractor.py tests/test_principal_memory_tasks.py tests/test_runtime_outbox_dispatcher.py tests/test_interview_workflow_consumer.py
git commit -m "feat(memory): generate consented personal fact proposals"
~~~

---

## Task 13：实现 Fact 生命周期、确认和删除传播

**目的：** 让 proposed fact 可被确认、拒绝、替代、撤销、过期和删除，并把
session/principal 删除传播到长期记忆。

**依赖：** Task 4、Task 11、Task 12。

**Spec coverage:** MEM-LTM-004、MEM-LTM-006、MEM-LTM-013、
MEM-LTM-014、MEM-LCY-001 至 MEM-LCY-005、MEM-LCY-030 至
MEM-LCY-037。

**文件：**

- Create: app/services/principal_memory_lifecycle.py
- Create: app/services/principal_memory_deletion.py
- Modify: app/services/session_deletion_worker.py
- Modify: app/services/session_deletion_tombstones.py
- Modify: app/api/routes.py
- Modify: app/services/runtime.py
- Create: tests/test_principal_memory_lifecycle.py
- Create: tests/test_principal_memory_deletion.py
- Modify: tests/test_session_deletion.py
- Modify: tests/test_session_deletion_tombstone_replay.py
- Modify: tests/test_api.py

### Step 1：实现 confirm/reject

Confirm 必须：

- 重新解析当前 principal；
- 验证 consent policy；
- 验证 proposal 仍有可用 source；
- 验证 normalized_fact 仍在 taxonomy；
- 设置 user_confirmed=true；
- 写 confirmed_at 和 expires_at；
- 使用 expected version 防止 stale update。

Reject 是 terminal transition，不能被普通 API 恢复。

### Step 2：实现 supersede

第一版 fact key 精确定义为：

~~~text
(deployment_id, principal_id, fact_type, normalized_fact)
~~~

同一 fact key 被新的独立 source 再次确认时，新 fact 直接 supersede 当前
active predecessor。source manifest/excerpt 不属于 fact key，但属于 fact_id，
因此重新确认产生新 immutable fact record 和直接 predecessor 链。

不同 normalized_fact 之间不自动建立 supersede。例如 confirmed_skill=python
与 confirmed_skill=fastapi 是两个事实；interview_language=zh_hans 与
interview_language=en 是潜在冲突值。第一版由 Task 14 的 conflict detection
保守处理：存在未解决冲突时相关 facts 都不进入 would-select，不通过模糊匹配
猜测哪一个应该 supersede。用户或受信规则必须先显式 revoke/reject 旧值，再
确认新值，才能解除冲突。

读取对同一 fact key 只返回 chain 中最新 active fact；不能让同 key 的多个
active predecessor 同时生效。

### Step 3：实现 expire/revoke

- expire job 使用 bounded batch；
- expires_at 到期后 status=expired；
- consent revoke 后 active/proposed facts 进入 revoked 或 deletion flow；
- revoked/expired 不能 read-shadow；
- 不通过延长 expires_at 恢复旧事实，必须创建新确认事实。

### Step 4：接入 session deletion

删除 session 时同步或分步清理：

- sourced proposals；
- sourced active facts；
- proposal effects；
- consent 不因单个 session 删除而自动删除；
- principal memory owner refs；
- tombstone replay 覆盖上述数据。

如果一个 active fact 有多个独立 source，只删除被删 source 的 binding；第一版
不实现多 source 合并时，必须删除整个 fact，采用更保守语义。

### Step 5：实现 principal purge

principal purge 删除：

- consent；
- proposals；
- active/terminal facts；
- outbox effects；
- source bindings；
- pending shadow observations。

完成后同 deployment/principal 的读取返回空，重复 purge 幂等。

### Step 6：限制 API

仓库可以实现 service contract 和 gated trusted-local route，但普通 candidate
API 仍不公开，直到 authenticated principal 可用。Response 只返回：

- fact type；
- normalized display value；
- status；
- created/confirmed/expires timestamps；
- 是否可撤销。

不返回 source session ID、question ID、digest 或内部 fact locator。

### Step 7：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_memory_lifecycle.py tests/test_principal_memory_deletion.py tests/test_session_deletion.py tests/test_session_deletion_tombstone_replay.py tests/test_api.py -q
~~~

### Step 8：建议提交

~~~powershell
git add app/services/principal_memory_lifecycle.py app/services/principal_memory_deletion.py app/services/session_deletion_worker.py app/services/session_deletion_tombstones.py app/api/routes.py app/services/runtime.py tests/test_principal_memory_lifecycle.py tests/test_principal_memory_deletion.py tests/test_session_deletion.py tests/test_session_deletion_tombstone_replay.py tests/test_api.py
git commit -m "feat(memory): govern personal fact lifecycle and deletion"
~~~

---

## Task 14：实现有界 Read-shadow，保持 Prompt 零注入

**目的：** 评估长期事实的可检索性、相关性和冲突处理，但不改变 Interview
Agent 的任何模型输入或用户可见输出。

**依赖：** Task 6、Task 7、Task 13。

**Spec coverage:** MEM-LTM-010 至 MEM-LTM-014。

**文件：**

- Create: app/services/principal_memory_retrieval.py
- Create: app/services/principal_memory_shadow.py
- Modify: app/services/memory_metrics.py
- Modify: app/services/runtime.py
- Modify: app/services/interview_workflow.py
- Modify: app/graphs/durable_interview_graph.py
- Create: tests/test_principal_memory_retrieval.py
- Create: tests/test_principal_memory_shadow.py
- Create: tests/test_principal_memory_prompt_isolation.py
- Modify: tests/test_memory_metrics.py
- Modify: tests/test_durable_interview_graph.py

### Step 1：定义 eligibility

只有同时满足以下条件的 fact 可进入 shadow candidate：

- deployment/principal 精确匹配；
- status=active；
- user_confirmed=true；
- consent 允许 read_shadow；
- 未过期、未撤销、未删除；
- taxonomy 和 consent policy 兼容；
- source 未 deleting/deleted；
- confidence/authority 符合 fact type 规则。

### Step 2：有界选择

每次最多 max_shadow_facts，estimated token 不超过 max_shadow_tokens。
优先级：

1. 用户明确的 accessibility/interview preference；
2. 当前目标岗位相关 learning goal；
3. 当前问题相关 confirmed skill；
4. 其他事实不选。

不得使用向量相似度；第一版使用受控 taxonomy 精确匹配。

### Step 3：冲突和新鲜度

- supersede chain 只选最新 active；
- 相互冲突但无直接 predecessor 时，两者都不选并记录 aggregate conflict；
- 临近过期的 skill fact 可以被选为 would-confirm，不作为稳定真相；
- 历史评分或 evaluation 不参与排序。

### Step 4：实现零注入 Shadow

Shadow resolver 可以在 follow-up 边界异步或旁路执行，但必须：

- 不修改 state.messages；
- 不修改 deterministic_context；
- 不修改 prompt；
- 不修改 plan/question selection；
- 不修改 scoring Evidence；
- 不改变 candidate response；
- 失败时不影响业务。

仅记录 would_select_count、would_select_type bucket、conflict_count、
estimated_tokens、latency 和 outcome。

### Step 5：增加源码与运行时隔离测试

tests/test_principal_memory_prompt_isolation.py 必须：

- 扫描 Examiner、Evaluator、Report、Knowledge corpus 路径，禁止直接读取
  PrincipalMemoryFactStore；
- 比较开启/关闭 read_shadow 时 fake provider 收到的 prompt 完全相同；
- 比较回答和评分输出完全相同；
- 注入跨 principal fact，证明不会被选择；
- 证明 terminal fact 不会进入 would-select。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_memory_retrieval.py tests/test_principal_memory_shadow.py tests/test_principal_memory_prompt_isolation.py tests/test_memory_metrics.py tests/test_durable_interview_graph.py -q
~~~

### Step 7：建议提交

~~~powershell
git add app/services/principal_memory_retrieval.py app/services/principal_memory_shadow.py app/services/memory_metrics.py app/services/runtime.py app/services/interview_workflow.py app/graphs/durable_interview_graph.py tests/test_principal_memory_retrieval.py tests/test_principal_memory_shadow.py tests/test_principal_memory_prompt_isolation.py tests/test_memory_metrics.py tests/test_durable_interview_graph.py
git commit -m "feat(memory): evaluate bounded personal memory in read shadow"
~~~

---

## Task 15：执行隐私、安全与隔离审计

**目的：** 证明长期记忆不会跨 principal 泄漏、不会污染公共知识、不会进入评分，
且指标、日志、异常和 acceptance artifact 不携带敏感数据。

**依赖：** Task 4、Task 6、Task 8、Task 14。

**Spec coverage:** MEM-SEC-001 至 MEM-SEC-005、MEM-LTM-007、
MEM-LTM-010、MEM-LTM-012、MEM-OBS-010 至 MEM-OBS-020。

**文件：**

- Create: docs/principal-memory-threat-model.md
- Create: tests/test_principal_memory_privacy.py
- Create: tests/test_principal_memory_isolation.py
- Create: tests/test_principal_memory_knowledge_firewall.py
- Modify: tests/test_memory_system_artifact_audit.py
- Modify: tests/test_trace_sanitization.py
- Modify: tests/test_memory_metrics.py
- Modify: app/services/trace_sanitization.py
- Modify: app/services/principal_memory_proposals.py
- Modify: app/services/principal_memory_shadow.py

### Step 1：建立 threat model

至少分析：

- principal collision；
- deployment confusion；
- stale consent；
- source session deletion race；
- prompt injection into fact proposal；
- taxonomy bypass；
- public corpus contamination；
- metric/log leakage；
- backup restore resurrection；
- malicious operator replay；
- cross-principal cache key collision；
- historical score anchoring。

### Step 2：Prompt Injection 测试

合成回答包含：

- 要求记住敏感属性；
- 要求把回答加入公共知识库；
- 要求忽略 consent；
- 要求把 fact 标记 active；
- 伪造 source digest；
- 伪造另一个 principal。

Extractor 必须忽略指令语义，只输出 schema allowlist，且所有模型 proposal
保持 proposed。

### Step 3：公共知识防火墙

源码和运行时测试证明：

- vector_store/load_knowledge/build_manifest 不导入 principal store；
- principal fact 无法转换为 KnowledgeChunk；
- corpus loader 拒绝 principal-memory schema；
- fact deletion 不触发 corpus mutation；
- Knowledge retrieval 不按 principal 查询。

### Step 4：隐私 artifact audit

Blocked sentinels 包括：

- synthetic session/principal/fact IDs；
- prompt/answer/excerpt；
- source/artifact digests；
- email/phone；
- DSN/password；
- normalized free text；
- provider response。

扫描 pytest logs、acceptance Markdown、JSON、trace fixtures、browser artifacts 和
Shadow 输出。

### Step 5：权限与删除审计

证明：

- 无 identity 时所有 personal memory operation 拒绝；
- identity 不匹配时不暴露 fact 是否存在；
- consent revoke 后 queued job 不写入；
- session/principal deletion 后 read-shadow 为空；
- tombstone replay 不跨 deployment；
- 错误响应不包含 locator。

### Step 6：运行 tests

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_principal_memory_privacy.py tests/test_principal_memory_isolation.py tests/test_principal_memory_knowledge_firewall.py tests/test_memory_system_artifact_audit.py tests/test_trace_sanitization.py tests/test_memory_metrics.py -q
~~~

### Step 7：建议提交

~~~powershell
git add docs/principal-memory-threat-model.md tests/test_principal_memory_privacy.py tests/test_principal_memory_isolation.py tests/test_principal_memory_knowledge_firewall.py tests/test_memory_system_artifact_audit.py tests/test_trace_sanitization.py tests/test_memory_metrics.py app/services/trace_sanitization.py app/services/principal_memory_proposals.py app/services/principal_memory_shadow.py
git commit -m "test(memory): audit personal memory privacy boundaries"
~~~

---

## Task 16：增加阶段 Acceptance Gate

**目的：** 用独立 runner 证明两条轨道的仓库实现和隔离验证都满足进入操作 Shadow
的条件，同时明确长期记忆 consumption 仍被阻止。

**文件：**

- Create: scripts/memory_validation_foundation_acceptance.py
- Create: tests/test_memory_validation_foundation_acceptance.py
- Modify: tests/test_memory_validation_foundation_plan.py
- Modify: tests/test_memory_system_artifact_audit.py
- Modify: docs/memory-validation-long-term-foundation-acceptance.md
- Modify: README.md
- Modify: docs/local-v1-runbook.md

### Step 1：组装强制 gates

Runner 至少验证：

- 旧 HTML 测试契约已退休且文件未恢复；
- 完整 browser suite 通过；
- live PostgreSQL migration/runtime 实际执行；
- deletion fault matrix 和 tombstone replay；
- Knowledge P1 coverage；
- durable aggregate metrics；
- deterministic long-context thresholds；
- Budget Shadow preflight；
- principal config/identity/consent；
- fact store/migration/concurrency；
- write-only proposal；
- lifecycle/delete propagation；
- read-shadow zero prompt injection；
- privacy/knowledge firewall；
- safe defaults；
- compileall 和 diff check。

### Step 2：验证 requirement traceability

Runner 读取 Spec v1.1.1-draft 并验证本计划引用的 requirement 全部存在。
本计划的 live PostgreSQL、baseline cleanup、durable metrics 和长上下文阈值属于
阶段验收义务，不伪装成新的 MEM-* requirement。

### Step 3：验证 operational evidence

与上一阶段不同，本阶段的 READY 状态要求：

- pg_runtime 不是 NOT_RUN；
- browser full suite 不是 partial；
- baseline failure count 为 0；
- durable metrics store 不是 process-local-only；
- Knowledge P1 readiness=true；
- deletion replay drill=passed。

若任一项缺失，runner 输出 BLOCKED，并列出稳定 gate code；不得输出 READY。

### Step 4：精确成功输出

成功时只输出：

~~~text
READY_FOR_MEMORY_VALIDATION_SHADOW
LONG_TERM_MEMORY_WRITE_SHADOW_READY
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

不得输出 PASS_FOR_PRODUCTION。

### Step 5：运行 acceptance

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_validation_foundation_acceptance.py tests/test_memory_validation_foundation_plan.py tests/test_memory_system_artifact_audit.py -q
& 'F:\python3.11\python.exe' -m scripts.memory_validation_foundation_acceptance
~~~

### Step 6：建议提交

~~~powershell
git add scripts/memory_validation_foundation_acceptance.py tests/test_memory_validation_foundation_acceptance.py tests/test_memory_validation_foundation_plan.py tests/test_memory_system_artifact_audit.py docs/memory-validation-long-term-foundation-acceptance.md README.md docs/local-v1-runbook.md
git commit -m "test(memory): gate validation and long-term write shadow"
~~~

---

## Task 17：完整回归与阶段发布记录

**目的：** 关闭仓库实现阶段，给出可复现证据；不把 Shadow readiness 描述成
生产 rollout 授权。

**文件：**

- Modify: docs/memory-validation-long-term-foundation-acceptance.md
- Modify only if evidence requires: README.md

### Step 1：运行 focused memory suite

运行上一阶段所有 memory tests，加上：

- PostgreSQL metrics；
- quality eval；
- principal contracts/identity/consent；
- in-memory/PostgreSQL fact store；
- proposal/outbox；
- lifecycle/deletion；
- read-shadow；
- privacy/isolation/firewall；
- acceptance contracts。

不得通过排除失败测试获得绿色状态。

### Step 2：运行 live PostgreSQL suite

~~~powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_runtime_migrations.py tests/test_postgres_session_store.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py tests/test_postgres_session_deletion.py tests/test_postgres_memory_metrics.py tests/test_postgres_principal_memory.py tests/test_postgres_principal_memory_consent.py tests/test_dual_langgraph_canary_postgres.py -q -m pg_runtime
~~~

Expected：选择的测试实际执行并全部通过。全 skip 视为未完成。

### Step 3：运行完整 Python regression

~~~powershell
& 'F:\python3.11\python.exe' -m pytest -q
~~~

Expected：所有测试通过，仅保留经过审查的 intentional skip 和 warning。

### Step 4：运行前端和完整 browser

~~~powershell
npm.cmd run build:frontend
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
~~~

Expected：build 和完整 browser suite 通过；无 report-actions timeout；无残留
4173/8011 listener。

### Step 5：运行质量、隐私和 acceptance

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.evaluate_memory_quality --deterministic
& 'F:\python3.11\python.exe' -m compileall app scripts tests
git diff --check
& 'F:\python3.11\python.exe' -m scripts.memory_validation_foundation_acceptance
~~~

### Step 6：验证安全默认值

最终记录必须证明：

- rollout=0；
- budget enforcement disabled；
- Question Memory consumption disabled；
- long-term mode disabled；
- write/read shadow 默认 disabled；
- consume 配置仍被 preflight 拒绝；
- trusted-local deletion/metrics/API 默认隐藏。

### Step 7：记录 exact evidence

Acceptance record 包含：

- revision、时间、环境；
- focused/full/pg/browser 数量；
- migration prefix；
- Knowledge manifest version/hash；
- quality metrics；
- deletion replay result；
- privacy result；
- connection/process/listener cleanup；
- safe defaults；
- production observation NOT_RUN。

不得包含测试数据内容、ID、DSN 或 provider payload。

### Step 8：建议提交

~~~powershell
git add docs/memory-validation-long-term-foundation-acceptance.md README.md
git commit -m "docs(memory): record validation and write-shadow readiness"
~~~

---

## 7. 仓库实现后的操作阶段

下面是后续操作顺序，不由本计划的仓库实现自动授权。

### Phase A：Budget Shadow

前置条件：

- Task 16 acceptance 通过；
- target environment、观察窗口和 operator 已批准；
- durable metrics data_complete=true；
- rollback/stop signal 可用。

行为：

- 只启用 MEMORY_BUDGET_MODE=shadow；
- enforcement 保持关闭；
- Question Memory consumption 保持关闭；
- Long-term Memory 保持 disabled；
- 观察至少一个批准窗口；
- 不扩大流量，只收集聚合指标。

退出：

- 所有 hard stop 为零；
- language bucket 样本足够；
- estimator、latency 和 failure rate 达标；
- 形成独立 observation record。

### Phase B：Long-term Write Shadow

前置条件：

- 使用合成数据或明确授权的测试 principal；
- consent policy 已展示并获得记录；
- proposal deletion drill 通过；
- public knowledge firewall 通过。

行为：

- mode=write_shadow；
- 生成 proposed facts；
- 不 active；
- 不 read-shadow；
- 不进入 Prompt；
- 比较 proposal precision、unsupported rate、dedupe 和 latency。

### Phase C：Long-term Read Shadow

前置条件：

- 只使用用户直接声明或测试中显式 confirm 的 facts；
- active fact lifecycle、expire、revoke、delete 演练通过；
- durable metrics 可用。

行为：

- mode=read_shadow；
- 计算 would-select；
- 实际 Prompt、question、score、report 与 disabled 组完全一致；
- 观察 conflict、staleness、token 和 relevance。

### Phase D：候选人确认体验试点

此阶段需要新的产品/身份 Plan。只有具备 authenticated principal、查看、更正、
撤销和删除 UI 后，才允许真实用户确认 personal facts。

### Phase E：长期记忆 Consumption

本计划明确不覆盖。进入此阶段前必须新建 Spec/Plan，并至少证明：

- 单会话 Budget/Question Memory canary 稳定；
- real-model long-context quality 达标；
- authenticated principal 可用；
- 用户可见、可更正、可删除；
- 偏差/公平性评估通过；
- 历史 fact 不直接参与评分；
- 1% 新 session 的显式生产批准已记录。

---

## 8. Entry/Exit Gate

### 8.1 进入仓库实现

- 上一阶段变更所有权明确。
- 历史 HTML 删除不再有歧义。
- 不需要生产 credential。
- safe defaults contract 通过。

### 8.2 进入 live PostgreSQL 验证

- 批准的测试 database 和 table prefix。
- database fingerprint 通过。
- cleanup procedure 可执行。
- 无生产数据。

### 8.3 进入 Budget Shadow

- full Python/browser 绿色。
- live PostgreSQL 绿色。
- durable metrics 绿色。
- Knowledge P1 readiness 绿色。
- deletion replay 绿色。
- deterministic long-context gate 绿色。

### 8.4 进入 Long-term Write Shadow

- principal identity 和 consent 可用。
- facts 只能 proposed。
- deletion/purge/replay 绿色。
- public knowledge firewall 绿色。
- 真实数据授权单独记录。

### 8.5 进入 Long-term Read Shadow

- active facts 仅来自显式确认。
- read-shadow prompt isolation 绿色。
- conflict/expiry/revoke 绿色。
- durable aggregate telemetry 绿色。

### 8.6 本计划的终态

允许：

~~~text
Budget Shadow ready
Long-term write-shadow ready
Long-term read-shadow repository ready
~~~

不允许：

~~~text
Budget enforcement production canary
Question Memory production consumption
Principal Memory Prompt consumption
Principal Memory scoring use
Public corpus auto-learning
~~~

---

## 9. 回滚矩阵

| 故障 | 立即行动 | 已有 session/fact | 新 session/fact |
|---|---|---|---|
| Browser baseline regression | 停止发布，保留 route-level trace | 不影响后端状态 | 不扩大 Shadow |
| PostgreSQL migration mismatch | 停止 runtime validate，保留 migration evidence | 不删除历史 schema | 不运行新 store |
| Metrics store 不完整 | 停止 Shadow 扩大，data_complete=false | 业务继续，观察记录无效 | 不进入下一阶段 |
| Knowledge coverage mismatch | readiness=false | deterministic 路径继续 | 禁止 memory consumption |
| Budget hard stop | 关闭 shadow/enforcement gate | 当前业务路径继续 | 不扩大流量 |
| Proposal schema/quality 失败 | mode=disabled，停止 worker leasing | proposed facts 保留待审或删除 | 不创建新 proposal |
| Consent 失效 | 立即停止 write/read shadow | 进入 revoke/purge | 不创建新 fact |
| Cross-principal 泄漏 | 关闭全部 Principal Memory runtime，启动隐私事件处理 | 保全最小审计证据后 purge | 全部拒绝 |
| Public corpus contamination | 停止 corpus publish/load，隔离 manifest | 回退到上一个 approved corpus | 禁止新 corpus |
| Deletion/replay 缺陷 | 停止新长期写入和删除 worker leasing | 保留 tombstone，人工修复 | 不写长期事实 |
| Read-shadow 影响 Prompt | mode=disabled，阻止发布 | 使用 deterministic/Question Memory 原路径 | 不执行 shadow resolver |
| Operator artifact 泄密 | 停止工具，限制 artifact 访问并轮换受影响凭据 | 按事件响应处理 | 禁止新导出 |

回滚不得删除被现有 session 引用的 graph definition、migration 或 immutable
artifact。长期记忆回滚优先关闭 mode 和 worker leasing，再修复数据；不得通过
恢复 terminal fact 为 active 进行回滚。

---

## 10. 风险登记

| 风险 | 缓解措施 | 必须证据 |
|---|---|---|
| 未验证摘要固化为长期事实 | 原始 source 验证、proposed 默认、用户确认 | proposal/source tests |
| Principal 错误合并 | 显式 resolver，禁止简历/指纹推断 | identity source audit |
| 历史记忆影响评分公平 | 本阶段零 Prompt 注入，评分路径源码审计 | prompt isolation |
| 候选人回答污染公共知识 | 独立 store 和 knowledge firewall | corpus isolation tests |
| Consent 撤回后仍处理 | execution-time consent check，cancel/purge | revoke race tests |
| 删除后备份恢复复活 | operator tombstone ledger 和 replay | restore drill |
| 指标包含标识符 | strict allowlist，aggregate-only table | privacy artifact audit |
| PostgreSQL 并发多 active | transaction、partial unique/CAS | pg concurrency tests |
| normalized_fact 成为自由文本仓库 | canonical taxonomy + length/schema checks | contract/property tests |
| Prompt Injection 激活事实 | model output 永远 proposed，rule allowlist | adversarial fixtures |
| Shadow 失败影响面试 | sidecar/fail-open，prompt equality tests | graph integration tests |
| 长上下文评测过拟合 | 多语言、纠正、否定、标识符、人工复核 | quality acceptance |
| Knowledge 数量达标但质量不足 | provenance、reviewer、golden retrieval | corpus acceptance |
| Dirty worktree 混入用户改动 | task ownership、focused staging | release record |

---

## 11. Definition of Done

本计划完成必须同时满足：

1. 已删除历史 HTML 不被恢复，相关旧测试已迁移且全绿。
2. Reference UI browser tests 已按路由拆分，完整 browser suite 通过。
3. live PostgreSQL pg_runtime 实际执行，不是 skip/NOT_RUN。
4. memory_session_policy_v1、question_memory_index_v1、session_deletion_v1
   migration 和 validate-only runtime 通过。
5. 删除 fault matrix 和 backup tombstone replay 通过。
6. Knowledge required tags 的 positive、hard-negative、boundary P1 覆盖通过。
7. 覆盖不足时 readiness fail-closed，不伪造 covered tags。
8. 记忆指标已持久化为聚合 minute/hour bucket，无 per-session drill-down。
9. 长上下文硬不变量 100% 通过，语义质量达到固定门槛。
10. Budget Shadow preflight、stop gates 和 runbook 完成。
11. Long-term mode 默认 disabled，consume 配置被拒绝。
12. Principal identity 不通过简历、指纹或模型推断。
13. Consent 有版本、用途、授予和撤回时间。
14. Principal Fact contract 只允许四类低风险事实和受控 taxonomy。
15. 模型输出只能创建 proposed、non-authoritative fact。
16. Fact store 与公共 Knowledge Corpus 完全隔离。
17. PostgreSQL fact store 的 dedupe、并发、状态机和边界隔离通过。
18. Proposal extraction 异步、幂等、有界，不影响 session finish。
19. confirm、reject、supersede、expire、revoke、delete 全部可测试。
20. Session/principal 删除和 tombstone replay 覆盖 Principal Memory。
21. Read-shadow 有界，只选择 active/confirmed/current-consent facts。
22. 开启 read-shadow 前后 provider prompt、问题、评分和报告完全相同。
23. Cross-principal、Prompt Injection、privacy 和 knowledge firewall 审计通过。
24. 完整 Python、frontend build、browser、compileall、diff check 全部通过。
25. Acceptance 精确输出：

~~~text
READY_FOR_MEMORY_VALIDATION_SHADOW
LONG_TERM_MEMORY_WRITE_SHADOW_READY
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

26. 所有 rollout、enforcement、Question Memory consumption 和长期记忆开关的
    committed defaults 仍为 disabled/0。
27. 没有对生产 readiness、真实用户试点或长期记忆消费作出未经批准的声明。

---

## 12. Spec Traceability Matrix

本计划只引用 Spec v1.1.1-draft 中已经存在的 MEM requirement。Baseline cleanup、
live PostgreSQL execution、durable metrics implementation 和质量阈值是本阶段的
验收义务，不创建临时 MEM 编号。

| Spec requirements | Implementation tasks |
|---|---|
| MEM-ARCH-001 至 MEM-ARCH-009 | Tasks 3-17、固定决策、回滚矩阵 |
| MEM-BUD-001 至 MEM-BUD-014 | Tasks 7、8、16、17 |
| MEM-SEL-001 至 MEM-SEL-010 | Tasks 7、8、14 |
| MEM-ART-001 至 MEM-ART-030 | Tasks 3、4、7、17 |
| MEM-SUM-001 至 MEM-SUM-010 | Tasks 7、12、15 |
| MEM-KNW-001 至 MEM-KNW-010 | Task 5、Task 8、Task 16 |
| MEM-LCY-001 至 MEM-LCY-005 | Tasks 4、13、15 |
| MEM-LCY-010 至 MEM-LCY-037 | Tasks 3、4、6、11、13、17 |
| MEM-CFG-001 至 MEM-CFG-010 | Tasks 0、8、9、16、17 |
| MEM-OBS-001 至 MEM-OBS-020 | Tasks 6、8、14、15、16 |
| MEM-UX-001 至 MEM-UX-008 | Tasks 1、2；这些编号已定义于 Spec v1.1.1-draft 的候选人降级体验要求，长期记忆用户确认 UI 延后 |
| MEM-SEC-001 至 MEM-SEC-005 | Tasks 4、9-16 |
| MEM-LTM-001 至 MEM-LTM-007 | Tasks 9-13、15、16 |
| MEM-LTM-010 至 MEM-LTM-014 | Tasks 9、10、13-16 |
| MEM-TST-001 至 MEM-TST-035 | Tasks 0-8、14-17 |

特别说明：

- MEM-LTM-012 在本阶段采取更严格实现：personal fact 完全不进入 Prompt，
  因而也不可能直接决定评分。
- MEM-LTM-013 的“用户知道系统正在使用历史偏好”只在未来 consumption 阶段
  适用。本阶段 read-shadow 不改变用户体验，但 consent 和事实查看/删除能力
  仍需在正式用户试点前完成。
- Principal Memory consumption 没有被本计划标记为已实现或已验收。

---

## 13. 阶段结论

本计划不是把长期记忆推迟，而是把它提前到一个安全的基础阶段：

~~~text
现在实现：
identity
+ consent
+ proposed facts
+ lifecycle
+ deletion
+ write-shadow
+ read-shadow

暂不实现：
Prompt consumption
+ scoring influence
+ public knowledge auto-learning
~~~

只有当单会话记忆经过真实 Shadow、长期事实经过身份和授权治理、删除与恢复演练
通过、持久指标可用、质量和公平性评测达标后，下一份独立计划才可以讨论
Principal Memory 的 1% consumption canary。
