# 重构 Release Contract

> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。

## RELEASE-REFACTORING-V1：Interview Agent 全量重构完成契约

- 当前状态：`ready`
- 审查报告：`docs/refactoring-audit.md`
- Readiness Rule：所有 required task 达到最终完成状态、所有 required gate 使用当前基线通过、所有 requirement 在审查报告中无未解释 BLOCKED 后，state 才能变为 ready。
- Required Tasks：TASK-A, TASK-B, TASK-C, TASK-D, TASK-E-ROUTER, TASK-E-UOW, TASK-E-SERIALIZATION, TASK-F, TASK-G, TASK-H-BROWSER, TASK-H-CLEANUP, TASK-FINAL-AUDIT
- Required Gates：GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-FRONTEND, GATE-BROWSER, GATE-RESIDUE, GATE-AUDIT
- Required Runbooks：RUNBOOK-POSTGRES-GATE, RUNBOOK-BROWSER-GATE, RUNBOOK-FINAL-AUDIT
- Required Requirements：REQ-ARCH-001, REQ-ARCH-002, REQ-ARCH-003, REQ-ARCH-004, REQ-DATA-001, REQ-DATA-002, REQ-REDUNDANCY-001, REQ-REDUNDANCY-002, REQ-REDUNDANCY-003, REQ-SAFETY-001, REQ-SAFETY-002, REQ-SAFETY-003, REQ-SAFETY-004, REQ-TEST-001, REQ-TEST-002, REQ-TEST-003, REQ-TEST-004, REQ-COMPLETE-001, REQ-COMPLETE-002, REQ-COMPLETE-003

| Gate | Mode | Evidence |
| --- | --- | --- |
| GATE-STATIC | automated | 依赖边界、结构化 Contract 完整性、无尾随空格和变更清单可审查。 |
| GATE-PYTHON | automated | Python 行为、领域 Contract、Adapter Contract 和 Architecture 规则在当前代码上通过。 |
| GATE-POSTGRES | approval_required | SQL、CAS、Fencing、Atomicity、Migration、Cleanup 与 Residue 在真实 PostgreSQL 上通过且 Critical Contract 零 skip。 |
| GATE-FRONTEND | automated | Frontend lint、production build、bundle budget 和 lazy route 约束通过。 |
| GATE-BROWSER | automated | Python 身份、Browser 支持服务、十类权威 Suite、显式 Viewport 和退出清理通过；Real-model Smoke 单独分类。 |
| GATE-RESIDUE | procedural | 本次运行创建的外部资源全部安全清理，没有越权删除。 |
| GATE-AUDIT | procedural | 全量重构完成声明具有逐项、当前且可复核的证据。 |
