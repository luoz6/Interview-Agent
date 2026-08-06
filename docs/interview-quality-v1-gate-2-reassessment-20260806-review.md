# Interview Quality V1 — Gate 2 v0.2.2 追加式重新审查

## 结论

```text
reassessment_kind=APPEND_ONLY_CLASSIFICATION_CORRECTION
supersedes_status_for_current_execution=true
historical_evidence_preserved=true
engineering_status=PASS
quality_status=BLOCKED
overall_status=BLOCKED
automatic_review=PASS
provider_calls=0
engineering_may_continue=true
```

本次重新审查只修复 Engineering/Quality 的状态归类，不覆盖、删除或改写 2026-08-05 发布的 Gate 2 证据。旧证据准确记录了当时的运行事实：确定性实现和回归通过，独立技术审查尚未完成，blind partition 未解封，T27 未运行。其错误在于把后三项外部质量结论同时作为 Engineering blocker。

Plan v0.2.2 现在明确：T19–T24 以及 T25/T26 的确定性工程交付物属于 Gate 2 Engineering；独立技术审查、正式 blind-test 和 T27 真实 Provider 阈值属于 Gate 2 Quality。该修订与 Plan 的总规则一致：专家和 Provider 外部资源不能把已完成的确定性工程伪装成失败，但所有适用 Quality 状态必须在 Gate 6 Quality 和 Gate 7 总体 PASS 前汇合。

## 不可变历史证据

```text
historical_evidence=docs/interview-quality-v1-gate-2-evidence.json
historical_evidence_sha256=de471e6e62ce606bd1da61ebe158fd602d49cad0071ee78b693f8d7673f0100d
historical_review=docs/interview-quality-v1-gate-2-review.md
historical_review_sha256=887795fdd96c0b7c3cf7bec2126ee046ef6889b6d0fd05d6af318030c55365ff
historical_engineering_status=BLOCKED
historical_quality_status=NOT_RUN
historical_files_modified=false
```

旧文件继续作为当时分类和事实的不可变审计记录。本文件是新的权威追加记录；执行 Manifest 同时保留旧状态和当前 v0.2.2 reassessment，不产生“历史从未 BLOCKED”的虚假叙述。

## Engineering PASS 依据

### T19–T24

- scored、partial、unscored 与 not_evaluated 状态已正交；
- skipped/unanswered 不作为 0 分进入能力平均；
- 固定 60 分和 fallback 质量旁路已移除；
- API、Web、PDF 消费相同覆盖和 null 语义；
- Rubric v3.3 candidate、GateConfig 阈值、分层、Spearman、稳定性、fallback、完整性和 insufficient-sample 语义已冻结；
- 保存响应重放是只读、确定且 append-only。

### T25/T26 确定性工程交付物

- 80-case synthetic calibration dataset 已冻结为 60 dev + 20 blind；
- 数据集包含题型、质量层级、中文/英文/混合语言、expected interval、理由、证据、missing-point/error 和 review/dispute 元数据；
- blind partition 在独立审查完成前 fail closed，开发运行不能读取；
- 保存响应重放、dev 诊断、误差分类、Rubric 版本化和一次性 blind runner 均已实现；
- 每次输出进入新目录，禁止覆盖旧失败证据或手工修改单个结果；
- 重放 Provider 调用为 0，deterministic replay delta 为 0；
- 旧 40-attempt 70% 基线保持失败，新 v3.3 candidate 的保存响应与开发诊断达到冻结工程阈值；正式 blind 和真实 Provider 阈值没有被这些 fixture 结果替代。

### 当前 revision 复测

在 `e4c798343e8e281e610a4d08b8bd9043f50264ea` 上重新执行 Gate 2 评分、覆盖、view/PDF、校准数据集、runner、评测 artifact/dataset/metrics/replay、Provider score adapter、质量、合同、模型和 round review 相关测试：

```text
focused_gate_2_regression=135 passed / 0 failed
current_full_pytest_baseline=2668 passed / 5 failed / 218 skipped / 1 warning
gate_2_related_full_suite_failures=0
provider_calls=0
```

全仓 5 个既有失败与 T59 基线一致，分别属于旧 perf-counter mock、旧 interview terminal expectation、历史 publication allowlist、旧 latest migration expectation 和 dependency lock metadata drift；没有 Gate 2 评分/覆盖/校准相关失败。

## Quality 保持 BLOCKED

以下项目没有因为重新分类而被视为完成：

1. T25 独立技术审查人尚未完成校准集判断，争议未仲裁；
2. T26 blind partition 没有解封，也没有正式执行一次性 blind-test；
3. T27 没有运行真实 Provider 评分验收；
4. 授权模型 `deepseek-chat` 仍存在 Provider 模型版本漂移，授权禁止自动换模；
5. 没有真实 Provider token、成本、延迟、保存响应或评分质量结论。

因此当前状态必须是：

```text
engineering_status=PASS
quality_status=BLOCKED
quality_reasons=PENDING_INDEPENDENT_REVIEW,BLIND_TEST_NOT_RUN,T27_NOT_RUN,MODEL_VERSION_DRIFT
overall_status=BLOCKED
```

## 后续影响

- T60 的 Gate 2–5 Engineering PASS 前置条件现在满足；
- T60 可以开始确定性 pairwise/风险组合回归；
- Gate 2 Quality blocker 不阻塞 T60–T64 的工程任务；
- Gate 6 Quality 和 Gate 7 总体 PASS 仍必须等待独立审查、blind-test 和适用真实 Provider 证据；
- 本次修订不授权真实候选人数据、自动换模、Hosted 部署或招聘决策。
