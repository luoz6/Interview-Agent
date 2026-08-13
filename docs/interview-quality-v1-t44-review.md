# Interview Quality V1 — T44 自动审查

## 结论

- Engineering：PASS
- 自动审查：PASS
- Provider 调用：0

运行时报告 Gate 已从文案/格式检查升级为确定性发布前 Gate。只有 Gate 无 blocking issue 时，Durable Review Graph 才会进入 `commit_report`；失败时 job 进入 `report_quality_failed`，不创建新 Artifact、不切换 active report pointer。

## 确定性校验范围

- 原始 payload schema、必填状态和 metadata；在 Pydantic 丢弃 extra 字段前检查禁止字段。
- 使用冻结 question kind、当前 session candidate answers 和 `dimension_evidence` 重算逐题分、五轴分、evaluation status/reason/evidence count。
- 使用统一 coverage 聚合器重算 overall、五轴、question evaluations、dimension evaluations、coverage 计数和结构化 coverage。
- `unscored`/非 answered 状态的 null 数字不变量。
- feedback question id/order、prompt hash、answer state 和 candidate answer 与冻结 review input 一致。
- observed excerpt 必须是当前题候选人回答的连续内容。
- candidate/reference evidence 的 canonical ID、namespace、question/source/excerpt 闭包。
- summary claim、strength、priority action 和 technical point 必须同时绑定 observation/evidence refs；关键输出至少有 candidate answer evidence。
- `Principal Memory`、raw Provider、prompt、messages、reasoning、JD/简历等禁止字段不能进入 report input/Artifact。
- 空文本、占位垃圾、在 `[实际…]`/`[真实…]` 中渲染真实数字的越界。
- report/artifact schema、presentation、rubric version/hash、summary Prompt lineage、action/guidance version。
- effect payload 原始 hash 与 Pydantic 规范化 payload hash必须同时匹配 checkpointed `report_sha256`。

## 自动审查修复

1. schema/validator 异常此前可能让 validation node 异常退出；现转换为不包含候选人原文的结构化 issue。
2. reference evidence 此前只检查 `source_id`；现同时检查 `reference:{source_id}`、当前题和 feedback excerpt。
3. `dimension_evidence` 非法结构、候选人答案替换、非回答原文 observed excerpt 现均有稳定 issue code。
4. 每个 answered feedback 必须发布 canonical candidate answer evidence；T43 guidance point 同样进入 Gate 引用闭包。

## 失败行为证据

- Quality Gate 失败时 `commit_report` 调用次数为 0。
- Review run 以 `report_quality_failed` 结束。
- 旧 active report id 保持不变。
- Artifact 历史数量不增加。
- warning 不参与 PASS；当前 Gate 不生成 warning-only 成功路径。

## 验证

- T44 聚焦与失败路径：27 passed
- Durable Review/Artifact/Job 相关 15 个测试文件：155 passed
- 完整 report 回归：376 passed
- 编译、差异检查和敏感信息扫描：PASS
