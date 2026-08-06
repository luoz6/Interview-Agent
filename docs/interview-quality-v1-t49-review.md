# Interview Quality V1 — T49 自动审查

## 结论

T49 Engineering 为 PASS，自动审查为 PASS。已冻结 24 对 synthetic v1/v2 报告语义样本，其中 20 对是候选人经历、责任、公司、规模、数字、金额、延迟、结果、知识引用、Principal Memory、否定语境、反事实或 Prompt injection 的关键事实边界。

T49 Quality 为 BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN，人工审查状态为 NOT_RUN。当前没有独立技术审查人的真实 judgment，因此没有声明以下结论：

- 人工判断的候选人经历编造 observed_count=0；
- v2 在准确性、可解释性或可执行性上不劣于或优于 v1；
- 第 5.5 节离线语义阈值 PASS；
- Gate 4 Quality PASS。

该 Quality 阻塞不撤销 T49/Gate 4 的确定性 Engineering 证据，也不阻止继续执行后续无外部依赖的 Engineering 任务。

## 冻结数据集

数据集位于 tests/fixtures/report_semantic_blind_test_v1.json：

    dataset_id: report-semantic-blind-test-v1
    dataset_version: 2026-08-06.1
    sample_size: 24
    critical_case_count: 20
    source_classification: synthetic
    contains_real_candidate_data: false
    contains_principal_memory: false
    raw_sha256: 39bde7b72e3cfbf381645965cae48fcb01c6935a0b45cfa55503cf3d4634a62c
    canonical_sha256: c974a5e97e543a54e29a322c06353e4c816227de6556d807799569a096a6b6a9

冻结 manifest 位于 tests/fixtures/report_semantic_blind_test_manifest_v1.json。它保存 dataset raw/canonical hash、样本下限、关键 case 下限、精确场景集合、每个 case 的预期 v2 发布状态、v1/v2 拒绝原因、禁止扩写的候选人 claim 和 reviewer focus。

24 个 case 覆盖 Plan 指定的全部 17 类场景：

    high_quality_style_variation
    technically_correct_plain_expression
    polished_technically_incorrect
    mixed_strengths
    partial_skip
    insufficient_evidence
    summary_provider_failure
    action_provider_failure
    no_knowledge_reference_scorable
    project_experience
    numeric_claim
    negation_context
    counterfactual
    prompt_injection
    legacy
    partial
    unscored

前两个对照 case 的 v1/v2 都可以接受，因此它们显式保存空拒绝原因；其余 22 个 case 保存明确的 v1 拒绝原因。冻结 v2 presentation 没有预期拒绝原因，但这只是 coordinator 的数据集预期，不是人工盲审结果，也不能被当作 Quality PASS。

## Provider 失败和发布状态

summary timeout 与 action invalid-output 两个 case 的 v2 disposition 固定为 publish_degraded；其余 22 个 case 固定为 publish。校验器双向强制该契约：

- 有 summary/action Provider component failure 场景时必须 publish_degraded；
- 没有该场景时不能伪装为 publish_degraded。

这些都是离线冻结 presentation。T49 没有发出 Provider 请求，provider_calls=0。

## 实际盲审交付包

生成器为 scripts/generate_t49_semantic_review_artifacts.py，冻结输出如下：

    reports/interview-quality-v1/t49-blind-review-v1/
    ├── reviewer/
    │   ├── packet.json
    │   └── empty-review-sheet.json
    ├── coordinator-only/
    │   └── assignment-key.json
    ├── dataset-validation.json
    └── evidence-ledger.json

reviewer packet 只包含 A/B 标签、候选人回答、覆盖类型、关键 case 标记和两个 presentation；不包含 variant_a_version、variant_b_version、randomization seed 或 assignment mapping。coordinator-only key 单独保存可复核承诺和 24 个版本映射，不能发给 reviewer。

冻结 packet canonical SHA-256：

    c174e36df9eaa84b2841a4d6682bda6ae572079b225402bdb7fcb88066abd1c5

生成器在输出目录已存在时拒绝运行，不覆盖既有审查证据。实际人工审查必须另存 completed review sheet，不能改写 empty-review-sheet.json；随后使用 append-only ledger API 添加新 entry。

## NOT_RUN 证据和 append-only ledger

当前空白 review sheet 有 0 条 judgment。Gate 重放结果为：

    quality_status: BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN
    human_review_status: NOT_RUN
    sample_size: 24
    critical_case_count: 20
    completed_judgment_count: 0
    independent_reviewer_count: 0
    provider_calls: 0
    offline_judge_used: false

所有人工派生统计都为 null，包括 fabrication observed/uncertain、false positive/negative、v1/v2 preference、tie 和各语义通过率。没有用数字 0 代替“未运行”。

SemanticReviewEvidenceLedger 使用 entry hash、完整 Gate result hash、review sheet hash 和 previous-entry hash 构成追加链。自动审查修复了原实现使用 model_copy(update=...) 时不重新执行 Pydantic validator 的问题；append 现在通过 model_validate 重建并验证完整 ledger。

测试证明以下操作都会失败：

- 重复 entry ID；
- 修改旧 entry 的 Gate result；
- 修改旧 entry 的其他内容；
- 修改 previous hash 后重算当前 entry hash；
- 传入内存中通过 model_copy 构造的无效 ledger 再追加；
- packet hash 与 ledger entry 不一致。

因此新结果可以追加，但旧失败证据不能被静默覆盖。

## GateConfig 样本闭包

冻结数据集满足四个 report-quality 离线语义指标的最小样本要求：

| 指标 | Gate 最小样本 | 当前可用样本 |
|---|---:|---:|
| adversarial experience fabrication | 20 critical | 20 critical |
| cross-question summary coverage | 20 | 24 |
| technical correctness blind review | 20 | 24 |
| actionability blind review | 20 | 24 |

“满足样本下限”只说明可以开展盲审，不说明人工指标已经达到阈值。

## 自动审查发现和修复

### 1. ledger append 绕过完整模型校验

原实现通过 model_copy(update=...) 返回追加后的 ledger。Pydantic 的该路径默认不重新运行 ledger validator，内存中被篡改的旧链可能在 append 时继续传播。现在 append 将已有 entry 和新 entry 序列化后交给 SemanticReviewEvidenceLedger.model_validate()，每次追加都重新验证 entry hash、Gate result hash、packet hash、唯一 ID 和链关系。

### 2. Provider failure disposition 只做单向校验

原实现只拒绝“无 Provider failure 却标记 degraded”，没有拒绝“有 Provider failure 却标记 publish”。现在校验器双向关闭该缺口。

### 3. 数据集与 coordinator 预期缺少完整闭包

新增 manifest 后，验证器同时检查 dataset ID/version、raw/canonical hash、pair set、critical flag、source classification、场景覆盖和 GateConfig 最小样本；任何漂移都产生明确 issue code。

### 4. reviewer 与 assignment key 需要可审计隔离

实际冻结交付物按 reviewer/coordinator-only 分目录，回归测试反序列化并重放全部文件，证明 reviewer packet 没有版本映射或 seed，assignment key 则能验证 packet commitment。

### 5. 未运行状态容易被误写为零结果

首条 ledger entry 保存真实空白 sheet 和完整 Gate result。人工派生字段全部为 null，只有确定性的样本数、缺失 reviewer 列表和真实 Provider 调用数为数值。

### 6. Windows checkout 会使 raw dataset hash 漂移

仓库启用了 core.autocrlf=true，而 T49 新增 JSON 最初没有显式 eol 属性。冻结 manifest 同时验证原始字节 hash；重新 checkout 后的 CRLF 转换会造成无业务变更的 GATE_CONFIG_OR_DATASET_DRIFT。现已在 .gitattributes 中将 T49 dataset、manifest 和全部盲审交付 JSON 固定为 LF。

## 验证结果

    T49 dataset/ledger focused: 10 passed
    semantic protocol + T49 focused: 27 passed
    full report regression with PostgreSQL 16: 419 passed
    PostgreSQL: 16.14
    pgvector: 0.8.6
    compileall app/tests/scripts: PASS
    online runtime semantic-review import scan: PASS_ZERO_MATCHES
    JSON/Pydantic/hash replay: PASS
    frozen JSON eol attributes: PASS_LF
    diff check: PASS
    provider_calls: 0

既有非阻塞警告：FastAPI TestClient 仍发出 StarletteDeprecationWarning。

## 真实性边界

- 没有独立人工 reviewer 完成该 packet；
- 没有发布人工 fabrication observed_count=0；
- 没有发布 v2 相对 v1 的 preference、non-inferiority 或 superiority；
- 没有调用离线 Judge 或任何 Provider；
- 冻结 synthetic cohort 不代表真实候选人总体质量或“系统绝不会编造”；
- T49 Engineering PASS 不等于 Gate 4 Quality PASS。
