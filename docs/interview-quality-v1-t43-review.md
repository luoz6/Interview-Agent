# Interview Quality V1 — T43 自动审查

## 结论

- Engineering：PASS
- 自动审查：PASS
- 独立人工事实边界审查：NOT_RUN
- T43 Quality：BLOCKED_HUMAN_REVIEW_NOT_RUN

该状态不阻塞后续无外部依赖的 Engineering 任务。不能用自动测试结果替代计划要求的人工判断，也不能将人工 `observed_count` 伪造为 0。

## 已实现边界

- 报告不再直接发布 Provider 的 `better_answer`；Provider Prompt 明确禁止返回候选人经历改写。
- `answer_structure_suggestion` 由后端固定模板生成，未知事实使用 `[真实背景]`、`[实际动作]`、`[实际取舍]`、`[实际指标值]`。
- `missing_technical_points` 只由结构化 gap/risk observations 生成，并绑定 observation/evidence refs。
- `example_rewrite` 仅在文本与当前题候选人回答、已发布 candidate answer evidence 完全一致时保留；Memory 标记即使出现在候选人文本中也不会进入改写。
- reference evidence 只能支撑通用技术点，不能成为 candidate experience rewrite 的证据。
- v2 Artifact 记录 guidance 版本、发布改写数和被省略的不安全改写数。

## 自动审查修复

自动审查额外发现并修复两项会影响恢复一致性的历史问题：

1. PostgreSQL 会话重建未恢复 `current_followup_count`，导致流式完成 KeyError 与幂等重放状态不一致；现从当前题 interviewer 消息确定性恢复。
2. 最后一题的 `next_question` 在 prepare 阶段未归一为 `finish`；现由 brain 阶段在边界处转换。

## 冻结对抗集

- Fixture：`tests/fixtures/report_fact_boundary_v1.json`
- 样本数：6
- 覆盖类型：company、scale、responsibility、metric、money、latency、result、knowledge_boundary、memory_boundary
- 自动发布的新增经历事实：0
- 限制：样本量有限，只证明列出的事实类型与规则路径；不证明开放域语义无编造。

## 验证

- T43 聚焦及相关契约：52 passed
- 完整 report 回归：364 passed
- 所有引用 `better_answer` 的测试文件：342 passed
- PostgreSQL 流式恢复/重放复测：5 passed

## 待外部完成

计划要求的独立人工判断尚未执行。后续 T45 盲审协议应纳入本冻结集，记录审查人、样本顺序、逐例判定、`observed_count` 和有限样本说明。
