# 重构架构决策参考

> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。

## DEC-001：收敛现有 Ports 而非重建目录树

- 状态：`accepted`
- 关联 Requirement：REQ-ARCH-003
- 理由：项目已经具有稳定 Port；新建平行目录会形成两套架构和迁移漂移。
- 结果：

  - 先审查现有 Port 的职责，再保留、重命名或分组。
  - 具体实现逐个迁入 Adapter，兼容出口只能是薄包装。

## DEC-002：Runtime 不属于业务 Domain

- 状态：`accepted`
- 关联 Requirement：REQ-ARCH-002
- 理由：Lease、Retry、Outbox、Container、Lifecycle 和 Provider Wiring 是运行基础设施，不是 Interview 业务实体。
- 结果：

  - 不新增 app/domain/runtime。
  - 核心 Reliability Contract 位于 app/runtime，业务 Adapter 复用它。

## DEC-003：Evidence 使用 Envelope 与领域 Payload 组合

- 状态：`accepted`
- 关联 Requirement：REQ-REDUNDANCY-001, REQ-SAFETY-004
- 理由：通用完整性字段可以共享，但 Capacity、Shadow、Release 和 Cleanup 的业务字段不能压入万能 Schema。
- 结果：

  - Common Envelope 负责身份、范围、输入摘要、隐私和 Receipt。
  - Domain Payload 与 Domain Policy 保持严格且独立。

## DEC-004：验证状态与晋升决策分开

- 状态：`accepted`
- 关联 Requirement：REQ-SAFETY-004
- 理由：PASS、BLOCKED、NOT_RUN 描述验证事实，HOLD、CONTINUE_OBSERVATION、READY 描述晋升决策，两者不可混用。
- 结果：

  - NOT_RUN 不能通过 PromotionDecision 绕过。
  - Synthetic Evidence 即使验证通过也不能自动晋升生产。

## DEC-005：OwnedPostgresScope 先于 Acceptance 迁移

- 状态：`accepted`
- 关联 Requirement：REQ-REDUNDANCY-002, REQ-SAFETY-002
- 理由：先迁移 Control Plane 再统一数据库所有权会重复搬迁不安全的 Cleanup 逻辑。
- 结果：

  - 所有测试和 Acceptance 数据库操作从第一天开始只通过 Owned Scope。
  - 未获批准或目标身份不匹配时 fail closed。

## DEC-006：Reliability 核心 Contract 先于 Report 拆分

- 状态：`accepted`
- 关联 Requirement：REQ-ARCH-002
- 理由：Report、Review、Generation 与 Runtime 都需要 Lease、Retry、Fencing 和 Idempotency；晚提取会造成二次重构。
- 结果：

  - Phase 4 冻结最小 Reliability Contract。
  - 后续业务 Wave 只迁移 Adapter，不建立万能 Framework。

## DEC-007：UnitOfWork 拥有事务边界

- 状态：`accepted`
- 关联 Requirement：REQ-DATA-001
- 理由：Repository 文件拆分不能把原有 business mutation 与 transactional outbox 拆成多个 Commit。
- 结果：

  - caller-owned Cursor 在同一 connection 和 transaction 中组合写入。
  - 失败路径 Rollback，成功路径只有一次 Commit。

## DEC-008：Context Artifact 只做边界抽取

- 状态：`accepted`
- 关联 Requirement：REQ-DATA-002
- 理由：Context Artifact 已具有成熟 Identity、Privacy、Lease、Replay 和错误协议，不应借目录迁移重新设计。
- 结果：

  - 先冻结兼容性 Matrix，再移动实现。
  - Filesystem 与 PostgreSQL Adapter 运行同一 Store Contract。

## DEC-009：Browser 使用十类权威 Suite

- 状态：`accepted`
- 关联 Requirement：REQ-REDUNDANCY-003, REQ-TEST-004
- 理由：desktop/mobile Project 全量复制和跨领域 Reference Spec 会重复执行相同行为，模糊测试所有权。
- 结果：

  - 只保留一个 Chromium Project。
  - 移动端与 Accessibility 使用显式 Matrix 和唯一 Owner。
  - Real-model Smoke 保持显式 opt-in。

## DEC-010：当前 Gate 不冻结历史仓库状态

- 状态：`accepted`
- 关联 Requirement：REQ-TEST-001, REQ-COMPLETE-001
- 理由：固定 Commit、Tree、ahead/behind、测试数量和旧文件清单会把历史快照误当成当前正确性。
- 结果：

  - 历史文档可以保留为非执行档案。
  - 当前 Gate 只验证稳定语义、结构、资源终态和最新运行证据。
