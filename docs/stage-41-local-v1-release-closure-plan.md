# Stage 41 Local V1 Release Closure Plan

## 1. 阶段目标

Stage 41 不增加新的业务能力，目标是把当前 Local V1 从“功能基本完成”推进到“可复现、可验收、可演示、可回归”的正式发布候选版本。

本阶段完成后，应能够用一套明确命令启动系统，并在真实浏览器中稳定完成以下闭环：

`输入 JD/简历 -> 生成面试计划 -> 完成模拟面试 -> 异步逐题评估 -> 生成最终报告 -> 查看评分证据 -> 下载 PDF`

## 2. 当前基线

- 离线测试：`466 passed, 31 skipped`。
- JavaScript 语法检查通过。
- Tailwind CSS 构建通过。
- Stage 40 真实模型评测完成 `40/40` 次调用，发布门禁为 `PASS`。
- PostgreSQL/pgvector 和 Redis Docker 容器可用。
- HTTP/API 主流程、SSE 接口、报告 Worker、PDF 和版本冲突 API 已通过已有验收。
- Local V1 尚未通过真实 GUI 浏览器 RC 验收。
- Stage 40 仍有较多未提交源码、测试、数据集和验收产物。

## 发布配置与可复现约束

Local V1 分为 Core 和 Celery profile。Core 使用 `INTERVIEW_EVENT_BACKEND=local`，不依赖 Redis；Celery profile 使用 `INTERVIEW_EVENT_BACKEND=celery`，只有通过 Redis/Celery 集成验证后才能声明支持。

发布环境固定并记录 Python `3.11.x`、Node.js `20.x` 或 `22.x` LTS、npm 版本、PostgreSQL 主版本和 pgvector 版本。计划、README 和运行手册统一使用 `python -m ...`、`npm ...`、`npx ...`，不得出现机器特定 Python 路径。

干净环境安装流程：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock.txt
npm ci
npx playwright install chromium
```

Stage 41 使用 Node.js 版 `@playwright/test`，不同时引入 Python Playwright。Python 使用 `pip-tools` 生成并提交带哈希的 `requirements.lock.txt`；前端提交包含 Playwright 的 `package.json` 和 `package-lock.json`。安装后执行 `python -m pip check`。

## 3. 范围

### 3.1 本阶段包含

1. 收口 Stage 40 工作区，明确应提交和不应提交的文件。
2. 补跑 PostgreSQL、pgvector、Report Job 和 Worker 集成测试。
3. 建立真实浏览器端到端自动化验收。
4. 验证 SSE、刷新恢复、幂等命令和 409 冲突恢复。
5. 验证逐题微批评估、最终报告复用、评分证据和 PDF 下载。
6. 修复验收中发现的阻塞性或高优先级缺陷。
7. 更新 README、运行手册和 RC 验收记录。
8. 形成可演示的发布基线提交和版本标签。

### 3.2 本阶段不包含

- WebSocket 传输改造。
- Redis Graph State Checkpoint。
- Knowledge Agent 2.0 或 Parent-Child RAG。
- 登录、用户体系和多租户隔离。
- 公网部署和生产安全加固。
- 语音识别、语音合成和数字人。
- 前端框架重写或大规模视觉改版。

## 4. 实施任务

### Task 1：收口 Stage 40 变更

目标：让评分可信闭环成为一个边界清晰、可独立回归的代码基线。

执行内容：

- 审核 Stage 40 修改文件和新增文件。
- 将源码、测试、数据集、CLI、评分解释 UI 和验收文档纳入版本控制。
- 为 `reports/` 生成包含相对路径、大小和 SHA-256 的机器可读清单。
- 正式白名单仅包含 `reports/stage40-acceptance/20260710T124843Z/` 下的 `manifest.json`、`metrics.json`、`report.md` 和 `attempts/**`。当前为 163 个文件、约 579 KB，保留完整 Provider 响应、标准化证据和重评分审计链。
- `reports/stage40-group*/`、`reports/stage40-smoke*/`、其他时间戳运行目录和临时文件不纳入发布基线。当前探索性 group/smoke 目录合计 137 个文件；删除或忽略前先确认正式白名单完整，并保留用户另有用途的产物。
- 对白名单执行密钥和隐私扫描，至少覆盖 `sk-`、`api_key`、`authorization`、`bearer`、数据库密码、完整秘密 URL 和本机绝对路径。
- 核对 Dataset SHA-256、run id、模型、prompt 和 rubric 版本在 manifest、metrics 与验收文档中一致。
- 排除 `.idea/`、`.claude/`、临时日志、PID 和一次性运行文件。
- 检查 `.env.example` 只包含占位值，不包含真实 API Key。
- 执行 `git diff --check`，消除格式错误和意外编码问题。

完成标准：

- Stage 40 所需文件没有遗漏。
- 仓库不包含密钥、个人 IDE 配置和无关临时产物。
- Stage 40 离线测试与保存证据重评分均通过。
- `reports/` 的保留和排除项均可由另一位开发者依据清单复核。

### Task 2：补齐真实基础设施测试

目标：锁定依赖，从空数据库幂等初始化运行环境，并验证 PostgreSQL、pgvector、Redis 和 Celery profile。

依赖和环境预检：

- 保存 `python --version`、`node --version` 和 `npm --version`。
- 在全新虚拟环境从 `requirements.lock.txt` 安装并执行 `python -m pip check`。
- 删除 `node_modules` 后执行 `npm ci`、JavaScript 检查和 CSS 构建。
- 增加预检命令，检查 PostgreSQL、vector 扩展以及所选事件后端。

数据库初始化：

- 新增幂等入口，例如 `python -m scripts.init_local_runtime --check` 和 `python -m scripts.init_local_runtime --seed-knowledge`。
- 初始化或验证 vector 扩展、Session、Message、Report、Question Evaluation、Report Job 和 `knowledge_chunks`。
- 知识种子可重复加载且不产生重复 chunk。
- `--check` 只检查不修改；输出表名、schema 版本、知识条数和表前缀，不输出密码。
- 验收使用唯一表前缀，禁止污染默认演示表。

PostgreSQL/pgvector 测试：

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:PGVECTOR_TABLE="knowledge_chunks_stage41"
python -m pytest -q -rs
```

重点覆盖：

- `tests/test_postgres_session_store.py`
- `tests/test_report_jobs.py`
- `tests/test_report_worker.py`
- `tests/test_stage38_postgres_api_contract.py`
- `tests/test_vector_store.py`
- `tests/test_vector_store_pgvector.py`

Redis/Celery 验证：

1. 使用项目的 `redis` Python 依赖执行 `PING`、带 TTL 的写入/读取和清理，失败信息不得泄露 Redis URL 密码。
2. 启动 Celery worker，发布隔离的 `round_closed` 事件，确认任务被消费，并在 PostgreSQL 中生成对应 `QuestionEvaluationRecord`。
3. 覆盖 Redis 不可用时的明确错误。

Core RC 不因 Redis 不可用而失败，因为默认 local backend 不依赖 Redis；Celery profile 只有在 Redis 冒烟和 Celery 事件消费均通过时才能声明支持。README 和 `/api/runtime` 必须准确反映该边界。

完成标准：

- 除显式真实模型测试外，不再因为 `POSTGRES_DSN` 缺失而跳过测试。
- 会话、版本号、幂等命令、报告任务租约、Worker 恢复和 pgvector 检索全部通过。
- 测试使用隔离表前缀，不污染默认演示数据。
- 锁文件可以在全新虚拟环境和空 `node_modules` 下完成安装。
- 数据库和知识种子能够从零初始化并重复执行。
- Redis PING、TTL 和 Celery 事件消费结果被记录；未通过时不得声明 Celery profile 可用。

### Task 3：建立浏览器自动化骨架

目标：将当前依赖人工观察的 RC 清单变成可重复执行的浏览器测试。

建议采用 Playwright，并将测试放在独立目录，例如：

```text
tests/browser/
|-- prep.spec.js
|-- interview-resume.spec.js
|-- report-flow.spec.js
`-- error-states.spec.js
```

首次引入依赖：

```powershell
npm install --save-dev @playwright/test
npx playwright install chromium
npx playwright test
```

提交更新后的 `package.json` 和 `package-lock.json`。此后干净环境统一使用 `npm ci`。Windows 使用 `npx playwright install chromium`；Linux CI 缺少系统库时使用 `npx playwright install --with-deps chromium`。

自动化环境应支持：

- 启动隔离的 FastAPI 进程。
- 启动隔离的报告 Worker。
- 使用独立 PostgreSQL 表前缀。
- 注入确定性 fake LLM 完成日常浏览器回归。
- 单独保留一个真实模型浏览器 smoke 流程，不并入普通测试。
- 测试结束后可靠关闭子进程。
- 失败时保存截图、浏览器控制台日志和网络错误。

完成标准：

- 一条命令可以执行全部浏览器回归。
- 浏览器测试不依赖手动点击。
- 失败产物能够定位到页面、接口和会话 ID。

### Task 4：验证 Prep 与会话启动

覆盖场景：

1. `/prep` 页面中文文本正常，无乱码和布局遮挡。
2. 空 JD、空简历显示明确校验提示。
3. 输入 JD 和简历后成功生成题目、岗位标签和 `prep_context`。
4. 匿名草稿可以保存、刷新并恢复。
5. 开始面试后跳转到带 `session_id` 的面试页。
6. 首次会话快照包含 `state_version`、`checkpoint_version`、`phase` 和 `review_status`。

完成标准：

- Prep 到 Interview 的正常路径和输入错误路径均自动通过。

### Task 5：验证 SSE、刷新恢复和冲突处理

覆盖场景：

1. SSE 回答流逐步显示追问，不重复渲染消息。
2. 请求携带 `expected_version` 和唯一 `command_id`。
3. 页面刷新后恢复对话、当前问题、题目状态和版本号。
4. 重复提交同一个 `command_id` 不产生重复回答或追问。
5. 构造陈旧 `expected_version` 后，接口返回 `409`。
6. 浏览器收到 `409` 后重新拉取最新快照。
7. 冲突恢复过程中保留用户输入，且不自动重复执行 skip/finish。
8. 网络错误时页面恢复可操作状态，不永久停留在加载中。

完成标准：

- 刷新、重试和冲突不会导致重复消息、重复评分或状态倒退。
- 所有动态文本和按钮状态可在桌面与移动视口正常使用。

### Task 6：验证异步评估与最终报告

覆盖场景：

1. 一个完整问题关闭后生成 `round_closed` 事件。
2. 本地异步 Reviewer 最终写入逐题评估记录。
3. Worker 未启动时，报告页保持处理中状态。
4. Worker 启动后可以领取任务并完成报告。
5. 已完成逐题评估被最终报告复用。
6. 缺失或失败的逐题评估能够重跑或进入完整会话 fallback。
7. 报告进度展示 `report_path`、复用题数、重跑题数和 fallback 原因。
8. 报告详情展示总分、维度分、证据、缺失点、改进回答和评分规则解释。
9. 前端展示分数与后端确定性重算结果一致。

完成标准：

- 每一个可见问题分数都能追溯到候选人回答证据和后端规则。
- Worker 延迟、重启和 fallback 不会丢失最终报告。

### Task 7：验证 PDF 与错误状态

覆盖场景：

- PDF 返回 `application/pdf`，文件非空且包含当前报告内容。
- PDF 下载后报告页面内容仍然保留。
- 无 `session_id`、错误 `session_id`、报告失败和 PDF 失败都有明确提示。
- 错误提示不会暴露数据库 DSN、API Key、堆栈或供应商原始响应。
- 页面发生接口错误后仍可刷新或重试。

完成标准：

- 正常下载和所有主要失败路径均有浏览器自动化覆盖。

### Task 8：真实模型 RC 验收

目标：同时保留不依赖外部 API 的真实证据硬门禁，以及有时效性的 DeepSeek 浏览器 smoke。

执行要求：

- 使用专用测试 JD 和简历，避免依赖个人敏感信息。
- 至少完整回答两道题，并触发一次追问。
- 至少关闭两个问题，使微批复用路径可被观察。
- 保存关键页面截图、会话 ID、报告 ID、模型名和运行时间。
- 核对报告中的事实均来自候选人回答或检索证据。
- 不在文档、截图文件名或日志中记录 API Key。

外部故障策略：

1. Stage 40 正式运行目录的哈希校验和保存证据重评分始终是 RC 硬门禁，结果必须为 `PASS`。
2. 保存的真实模型证据有效期设为 30 天。
3. DeepSeek 浏览器 smoke 遇到限流、欠费、DNS、Provider 5xx 或计划维护时，在 24 小时窗口内最多重试 3 次，并保存脱敏失败证据。
4. 若重试后仍为外部故障，且 30 天内的保存证据重评分通过，可发布 `local-v1.0.0-rc1`，状态为 `PASS_WITH_PROVIDER_RECHECK`。
5. 最终 `local-v1.0.0` 仍要求新鲜真实模型 smoke 为 `PASS`。
6. Prompt/Schema 不兼容、稳定复现的 4xx 或应用代码错误不得按外部故障降级。

完成标准：

- 真实浏览器完整闭环为 `PASS`；仅在满足外部故障策略时允许 RC 记录为 `PASS_WITH_PROVIDER_RECHECK`。
- Stage 39 遗留的 GUI 浏览器阻塞项被关闭或明确转为限时 Provider recheck。

### Task 9：文档、提交与发布基线

更新内容：

- 更新 `README.md`，修正过时的架构阶段描述。
- 更新 `docs/local-v1-runbook.md`，提供唯一、可复制的启动和验收命令。
- 更新 `docs/stage-21-browser-e2e-acceptance.md`，记录自动化和真实模型结果。
- 新建 Stage 41 验收记录，包含环境、命令、结果、缺陷和证据路径。
- 记录全部跳过测试及其合理原因。

建议提交边界：

1. `feat: complete stage 40 scoring trust loop`
2. `build: lock dependencies and initialize local runtime`
3. `test: add local v1 browser and celery profile acceptance`
4. `fix: close local v1 release blockers`
5. `docs: record stage 41 local v1 rc acceptance`

完成标准：

- 工作区只剩明确不纳入版本控制的个人文件。
- 发布提交可从干净环境按运行手册启动。
- 创建 Local V1 发布标签，例如 `local-v1.0.0-rc1`。

## 5. 缺陷处理规则

浏览器验收期间只处理影响发布的缺陷：

- P0：数据丢失、重复提交、报告错误归属、密钥泄露。立即阻断发布。
- P1：主流程无法完成、Worker 无法恢复、报告或 PDF 不可用。必须在本阶段修复。
- P2：影响指定验收视口或核心状态理解，但存在可用绕行路径。Stage 41 最多修复 3 个或投入 1 个工作日，以先到者为准；超出预算的 P2 进入后续 backlog，除非重新判定为 P1。
- P3：纯视觉细节或不影响任务完成的优化。记录到后续阶段，不扩大 Stage 41。

每个缺陷必须包含：复现步骤、预期结果、实际结果、修复提交和回归测试。

## 6. 发布门禁

Stage 41 只有同时满足以下条件才能标记为完成：

- 离线全量测试通过。
- PostgreSQL/pgvector 集成测试通过。
- Python/Node 版本声明、Python 锁文件和 `package-lock.json` 可在干净环境安装，`python -m pip check` 通过。
- 数据库、vector 扩展、运行表和知识种子可幂等初始化。
- JavaScript 语法检查和 CSS 构建通过。
- 浏览器自动化主流程及错误流程通过。
- Stage 40 白名单通过密钥扫描、哈希校验和保存证据重评分。
- 真实模型浏览器 smoke 通过；RC 可按外部故障规则临时使用 `PASS_WITH_PROVIDER_RECHECK`，最终版不可使用。
- Celery profile 只有在 Redis PING、TTL 和 Celery 事件消费通过后才声明支持。
- 没有 P0/P1 未关闭缺陷。
- P2 没有超过数量和时间预算。
- 没有密钥或个人环境文件进入提交。
- RC 验收记录从 `Not accepted` 更新为 `PASS`，或在满足外部故障策略时更新为 `PASS_WITH_PROVIDER_RECHECK`。
- 干净环境能够依据运行手册复现完整流程。

## 7. 并行执行轨道

Stage 41 不采用严格串行链。四条轨道可同步开始：

| 轨道 | 工作内容 | 汇合条件 |
| --- | --- | --- |
| A | Stage 40 源码边界、产物白名单、哈希和密钥扫描 | Task 1 完成 |
| B | 依赖锁、环境预检、数据库初始化、Postgres/pgvector/Redis/Celery 验证 | Task 2 完成 |
| C | Playwright fixture、Prep、SSE/恢复、报告/PDF 和错误用例 | Task 3-7 完成 |
| D | README、runbook、验收模板和证据索引 | Task 9 初稿完成，RC 后收口 |

Task 4、5、6、7 共享 Task 3 的 fixture，但测试文件可并行开发。Task 1 不阻塞 Task 2、Task 3 和文档初稿。四条轨道只在最终 RC 汇总、真实模型新鲜度检查和发布标签处汇合。

## 8. 后续阶段入口

Stage 41 通过后再启动 Stage 42：Knowledge Agent 2.0。

Stage 42 的目标是将当前关键词预热升级为真正的检索驱动闭环：

`JD/简历结构化解析 -> pgvector 检索 -> 考点树 -> 题目证据绑定 -> 下一轮追问 -> 报告复用同一证据`

WebSocket、Redis Checkpoint、语音和多用户能力继续保持非当前优先级。
