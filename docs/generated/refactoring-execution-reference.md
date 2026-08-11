# 重构执行参考

> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。

| Task | Wave | Status | Dependencies | Deliverables | Gates |
| --- | --- | --- | --- | --- | --- |
| TASK-A | A | completed | None | Stage 38、Redis、Staging、Release Diff 与 PostgreSQL Cleanup 安全修复 | GATE-PYTHON, GATE-AUDIT |
| TASK-B | B | completed | TASK-A | Common Evidence Envelope、Domain Payload/Policy、Receipt 与 Atomic Writer; OwnedPostgresScope、Migration Harness 与共享 Fixture | GATE-PYTHON, GATE-POSTGRES, GATE-AUDIT |
| TASK-C | C | completed | TASK-B | 受保护 Evidence 链、Receipt、Owned Scope 与 Stage Acceptance | GATE-PYTHON, GATE-POSTGRES, GATE-RESIDUE, GATE-AUDIT |
| TASK-D | D | completed | TASK-B | Effective Runtime Config; 单一 Runtime Container 和显式 Lifecycle; 最小 Reliability Contract | GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-AUDIT |
| TASK-E-ROUTER | E | completed | TASK-D | 领域 Router、共享依赖与错误映射; Session Command、State、Snapshot 与 Application Service | GATE-STATIC, GATE-PYTHON, GATE-AUDIT |
| TASK-E-UOW | E | completed | TASK-E-ROUTER | 六个独立 Repository、Schema Adapter 和 caller-owned Cursor; 真实 PostgreSQL SQL、Atomicity、Cleanup 与 Residue 证据 | GATE-PYTHON, GATE-POSTGRES, GATE-RESIDUE, GATE-AUDIT |
| TASK-E-SERIALIZATION | E | completed | TASK-E-UOW | Session、Message、Outbox 与 Receipt 字节兼容和原子性 Gate | GATE-PYTHON, GATE-POSTGRES, GATE-AUDIT |
| TASK-F | F | completed | TASK-E-SERIALIZATION | Report Job、Worker、Progress、Evaluation、Assembly、Quality 与 Provider Adapter 收敛 | GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-AUDIT |
| TASK-G | G | completed | TASK-E-SERIALIZATION, TASK-D | Principal Memory Lifecycle、Rights、Ledger 与 Shadow Matrix; Knowledge/Vector Adapter 和 Context Artifact Compatibility Matrix | GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-RESIDUE, GATE-AUDIT |
| TASK-H-BROWSER | H | completed | TASK-D | 单一 Python Runtime Resolver; 单一 Chromium Project 和十类权威 Browser Suite | GATE-BROWSER, GATE-RESIDUE, GATE-AUDIT |
| TASK-H-CLEANUP | H | completed | TASK-H-BROWSER, TASK-B | 结构化 Requirements、Decisions、Tasks、Gates、Runbooks 与 Releases; 历史 Baseline、源码字符串 Gate、Static/Legacy Compatibility 和已替代 Stage 删除 | GATE-STATIC, GATE-PYTHON, GATE-FRONTEND, GATE-BROWSER, GATE-AUDIT |
| TASK-FINAL-AUDIT | FINAL | completed | TASK-F, TASK-G, TASK-H-CLEANUP | docs/refactoring-audit.md; 第 21、23、24 节逐项证据和修复闭环 | GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-FRONTEND, GATE-BROWSER, GATE-RESIDUE, GATE-AUDIT |
