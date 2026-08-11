# 重构验收参考

> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。

| Requirement | Category | Statement | Verification | Gates |
| --- | --- | --- | --- | --- |
| REQ-ARCH-001 | architecture | API Router 只负责请求解析、依赖获取和响应映射，不承载业务流程。 | Architecture 扫描和 API Contract 共同证明领域 Router 不依赖旧 Facade 或具体 Store。 | GATE-STATIC, GATE-PYTHON |
| REQ-ARCH-002 | architecture | Runtime 由 Config、Container、Lifecycle 和 Reliability 组成，不新增 app/domain/runtime 或平行 Singleton。 | 静态依赖扫描和 Runtime Lifecycle Contract。 | GATE-STATIC, GATE-PYTHON |
| REQ-ARCH-003 | architecture | 现有 app/ports 是唯一 Port 边界，不建立第二套 repositories、providers 或 jobs Port 树。 | Port 清单、导入图和 Architecture Contract。 | GATE-STATIC |
| REQ-ARCH-004 | architecture | Domain 与 Application 只接收类型化配置和 Port，不直接读取进程环境变量。 | AST 静态扫描和 Effective Runtime Config Contract。 | GATE-STATIC, GATE-PYTHON |
| REQ-DATA-001 | data | Session、Message、Outbox、Receipt 与 Evaluation 的组合写入通过同一 UnitOfWork 共享 connection 和 transaction。 | Fault Injection、真实 PostgreSQL Atomicity 和 CAS/Fencing Contract。 | GATE-PYTHON, GATE-POSTGRES |
| REQ-DATA-002 | data | Context Artifact 的 Key、Identity、Owner、Privacy、Lease、Fencing、Immutability、Replay 和删除语义不得因边界迁移改变。 | Memory Reference/PostgreSQL Adapter Store Contract 和 Compatibility Matrix。 | GATE-PYTHON, GATE-POSTGRES |
| REQ-REDUNDANCY-001 | redundancy | Common Envelope、Canonical JSON、Receipt、Privacy Scanner 和 Atomic Writer 各自只有一个权威实现，领域 Payload 保持独立严格 Schema。 | 重复实现扫描和 Evidence Mutation Matrix。 | GATE-STATIC, GATE-PYTHON |
| REQ-REDUNDANCY-002 | redundancy | PostgreSQL 测试和 Acceptance 通过统一 OwnedPostgresScope、Migration Harness 和 Cleanup Audit 访问数据库。 | PostgreSQL Scope Contract、真实 Permission/Ownership/Cleanup/Residue Gate。 | GATE-POSTGRES, GATE-RESIDUE |
| REQ-REDUNDANCY-003 | redundancy | Browser 行为由 Prep、Interview、Report Center、Report Processing、Report Detail、Memory Center、Recovery、Accessibility、Critical-path E2E 和 Real-model Nightly Smoke 十类 Suite 唯一拥有。 | Playwright 收集清单和完整 Browser Gate。 | GATE-BROWSER |
| REQ-SAFETY-001 | safety | 代码、日志、Evidence 和错误响应不得输出明文 DSN、密码、Provider Key、HMAC Secret、私有 Payload 或完整连接串。 | 敏感信息扫描、Mutation Contract 和运行时错误测试。 | GATE-STATIC, GATE-PYTHON |
| REQ-SAFETY-002 | safety | PostgreSQL、Redis、Filesystem 和临时进程的 Mutation 与 Cleanup 必须绑定本次运行的 Ownership，不能删除未证明属于自己的资源。 | Ownership Contract、Cleanup Receipt 和 Residue Audit。 | GATE-PYTHON, GATE-POSTGRES, GATE-RESIDUE |
| REQ-SAFETY-003 | safety | Release Diff 对 Rename 和 Copy 的 source 与 destination 分别执行 ownership、sensitive-path 和 release-boundary policy。 | Git porcelain 双路径 Contract。 | GATE-PYTHON |
| REQ-SAFETY-004 | safety | Evidence 拒绝未知字段、字符串布尔值、非整数计数、NaN 和 Infinity；VerificationStatus 与 PromotionDecision 分离，Synthetic Result 不得晋升为 Production Acceptance。 | Evidence Mutation Matrix 和 Domain Policy Contract。 | GATE-PYTHON |
| REQ-TEST-001 | testing | Unit、Contract、Integration、Architecture、Acceptance 与 Browser 证据按风险分层，定向数量不能相加冒充完整 Suite。 | 测试清单和最终 Gate Receipt。 | GATE-PYTHON, GATE-FRONTEND, GATE-BROWSER |
| REQ-TEST-002 | testing | PostgreSQL Ownership、Permission、Atomicity、Cleanup 和 Residue 等 Critical Contract 必须实际运行且零 skip；可选 Provider 与 Real-model Smoke 单独分类。 | Skip Classification 和真实 PostgreSQL Gate。 | GATE-POSTGRES, GATE-AUDIT |
| REQ-TEST-003 | testing | 测试结束后不得保留任务创建的数据库对象、角色、容器、临时文件或后台监听进程。 | Residue Audit、端口检查和最终 Git 变更清单。 | GATE-RESIDUE, GATE-AUDIT |
| REQ-TEST-004 | testing | Browser Runner 使用单一 Resolver 验证 Python 3.11、realpath、executable identity、FastAPI/Uvicorn 能力和 Runtime Preflight 一致性，并在冲突时 fail closed。 | Browser Runtime Contract 和 Browser Preflight。 | GATE-BROWSER |
| REQ-COMPLETE-001 | completion | 第 21 节和第 23 节的每个条目都必须在最终审查中记录 PASS、BLOCKED 或有理由的 N/A，并附可复核证据。 | Requirement Traceability 和最终审查报告。 | GATE-AUDIT |
| REQ-COMPLETE-002 | completion | 自动审查发现的问题必须修复并重新执行受影响定向测试和全部失效的完整 Gate。 | 审查报告中的 Finding、Remediation 与 Reverification。 | GATE-AUDIT |
| REQ-COMPLETE-003 | completion | 所有 Wave 完成且最终 Gate 通过后，必须生成 docs/refactoring-audit.md，计划才可标记完成。 | Release Contract 和审查报告存在性、完整性校验。 | GATE-AUDIT |
