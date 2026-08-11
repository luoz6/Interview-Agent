# 重构运行手册参考

> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。

## RUNBOOK-POSTGRES-GATE：执行真实 PostgreSQL Gate

- 受众：`release_operator`
- Gates：GATE-POSTGRES, GATE-RESIDUE
- 前置条件：

  - Scope Approval、Receipt、Target Fingerprint、Allowlist 和 Expiry 已在任务环境配置。
  - 操作者不得在聊天、日志或 Evidence 中粘贴 DSN 和秘密。

- 步骤：

  1. 只输出所需环境变量的布尔可用性。
  2. 通过 OwnedPostgresScope 验证 Permission、Target Identity 和 Ownership。
  3. 运行 SQL、Migration、CAS、Fencing、Atomicity、Outbox、Receipt 与 Recovery Contract。
  4. 执行 Cleanup，并审计 relation、role、container 和临时 Evidence 残留。
  5. 将 Critical skip 与可选 Provider skip 分开记录。

- 失败策略：

  - Approval 缺失、过期或目标不匹配时不执行任何数据库 Mutation。
  - Cleanup 失败时保留阻断状态和安全 Receipt，不扩大删除范围。

## RUNBOOK-BROWSER-GATE：执行 Browser 权威 Gate

- 受众：`developer`
- Gates：GATE-BROWSER, GATE-RESIDUE
- 前置条件：

  - Node 和 Playwright 依赖已安装。
  - Browser 测试端口未被未授权服务占用。

- 步骤：

  1. 运行 Browser Preflight，验证 Python 3.11、解释器身份、FastAPI/Uvicorn 与 Chromium。
  2. 收集十类权威 Suite，确认只有一个 Chromium Project。
  3. 串行运行完整 Browser Gate，并单独记录 Real-model opt-in skip。
  4. 检查支持服务端口和 test-results 临时产物，清理本次运行残留。

- 失败策略：

  - 解释器身份冲突或能力缺失时 fail closed。
  - 页面失败时保留失败证据，修复后重新运行受影响 Suite 和完整 Gate。

## RUNBOOK-FINAL-AUDIT：执行最终自动审查

- 受众：`maintainer`
- Gates：GATE-STATIC, GATE-PYTHON, GATE-POSTGRES, GATE-FRONTEND, GATE-BROWSER, GATE-RESIDUE, GATE-AUDIT
- 前置条件：

  - 所有 Wave 的实现任务已经达到 implemented_pending_final_audit。
  - 最后一次生产代码修改时间已经冻结。

- 步骤：

  1. 记录分支、解释器身份、外部服务可用性和 Git 变更清单。
  2. 执行静态结构、敏感信息、重复实现、历史 Gate 与越层依赖扫描。
  3. 依次运行定向测试、真实基础设施、完整 Python、Frontend 和 Browser Gate。
  4. 按 Requirement ID 建立 Status、Evidence、Finding、Remediation 和 Reverification 表。
  5. 修复所有可修复 Finding，并重新运行因生产修改失效的 Gate。
  6. 写入 docs/refactoring-audit.md；只有无未解释 BLOCKED 时才允许完成。

- 失败策略：

  - 不用旧测试结果替代当前基线。
  - 不因耗时或环境配置麻烦跳过 Critical Contract。
  - 确需新权限或用户决策时保留明确 BLOCKED 和所需动作。
