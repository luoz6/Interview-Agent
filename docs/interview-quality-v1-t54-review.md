# Interview Quality V1 — T54 自动审查

## 结论

T54 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为
**NOT_REQUIRED**，真实外部 Provider 调用数为 **0**。

本任务在 T11 已发布的 Plan Revision 编辑领域与唯一 revision store
之上补齐两类约束：

- 每次 revision 变更都有结构化、仅含 ID/hash/reason code/字段级 hash
  diff 的审计记录；
- 每一道题都有结构化 Knowledge Binding，并在编辑、换题、恢复和全量
  regenerate 后按冻结规则保留、失效或重建。

T54 没有增加第二套 revision 创建、问题 ID 分配、session 启动或 Provider
调用链路。所有编辑仍通过 InterviewPlanEditor、
ProviderPlanRegenerator 与 InterviewPlanRevisionStore 完成。

## Revision 审计契约

新增 plan-revision-audit-v1。revision 级记录包含：

- created_reason；
- source_sha256、parent_plan_sha256、result_plan_sha256；
- configuration_diff；
- operations。

每个 operation 只记录：

- operation 与 actor；
- source/result question ID、target revision ID；
- reason_code 与 changed_fields；
- before_sha256/after_sha256；
- knowledge binding action、status 与 reason code。

审计 schema 对 UUID、SHA-256、枚举和值域进行校验。审计记录不保存简历、
JD、问题原文、focus 原值、Provider 原文或凭据。字段变化只保存前后
canonical SHA-256；configuration_diff 当前为空，因为 T11 editor 不允许在编辑
操作中改变冻结配置。

内存与 PostgreSQL store 均持久化同一 audit JSON。PostgreSQL 增加
audit_json JSONB NOT NULL 列；旧空 JSON 行读取时合成明确的兼容审计，
不会伪造用户或 Provider 操作。

## Knowledge Binding 生命周期

新增 plan-question-knowledge-binding-v1，状态为 valid、unbound 或
invalidated。valid 必须同时具有唯一 evidence IDs、每条 evidence 的 canonical
内容 SHA-256 和 corpus manifest SHA-256；unbound/invalidated 不允许继续声称
evidence 或 manifest。

冻结规则如下：

| 操作 | Binding 结果 |
| --- | --- |
| grounded initial generation | 建立并校验 valid binding |
| 无 grounded evidence | unbound / no_grounded_evidence |
| 编辑问题文本或 focus | invalidated / question_content_changed |
| move | 保留 |
| delete | 删除被删题 binding，其他题保持 |
| add custom | 强制 unbound / custom_question |
| regenerate question | 重建并重新校验 |
| restore revision | 恢复后重新校验 |
| regenerate all | 全部重建并重新校验 |

客户端为 custom question 传入 knowledge_binding 会被拒绝，不能伪造
grounding。Provider regenerate 返回的 evidence hash 与当前 evidence reference
不一致时，结果为 invalidated / evidence_hash_mismatch，不会继续声称 valid。

## 自动审查发现与修复

### Provider 临时 ID 与运行时 UUID 不一致

原 configured generation 的 Provider 边界使用 q1..qN，而 V2 revision 会分配
UUID；prep question hints 仍保留临时 ID，导致运行时
KnowledgeBindingResolver 无法按 V2 question ID 找到 grounding。

修复后 legacy_plan_to_v2 一次性分配 UUID，并把 question、prep hint 与
binding 全部映射为同一 UUID。prepared_plan_revision 精确校验已绑定 legacy/V2
identity，错配返回 prepared_plan_identity_mismatch。

### 已绑定 revision regenerate 的 ID 约束冲突

Provider enforcement 仍要求边界临时 ID 为 q1..qN，但已绑定 V2 revision 的
稳定 ID 必须保持 UUID。修复通过只在 Provider 校验边界创建位置投影，校验后
继续复用原 V2 identity/hash，不重分配 persisted question ID。

### 公开 API 泄露内部 binding hash

首轮审查发现完整 V2 plan 与 session plan_snapshot 会把
evidence_content_sha256 和 corpus_manifest_sha256 暴露到公开响应。

新增 public_interview_plan_v2_payload 作为公开投影。公开 question binding
只保留 schema_version、status、evidence_ids 与 reason_code；同时继续移除
binding_snapshot、question_bindings、resume_signals 和 evidence 内容 hash。
内部 revision/session snapshot 仍保留完整 binding，用于重校验与 canonical
plan hash。

## T54 验收

- edit 后 question ID 保持，binding 失效，position/replaces 语义保持正确；
- move 后所有 question ID 保持且 position 连续；
- delete 不重分配其他题 ID；
- custom question 无法声称 grounding；
- single regenerate 分配 replacement ID 并记录 replaces_question_id；
- regenerate evidence hash 不匹配时 binding 失效；
- regenerate 失败不会创建 revision；
- restore 后历史 revision 可读并重新校验 binding；
- regenerate all 重建全部 binding；
- 已启动 session 的内部 immutable plan snapshot 不随后续 revision 编辑变化；
- API 公开投影不泄露内部 evidence/corpus hash；
- PostgreSQL audit 与 binding 完整 round-trip。

## 测试与证据

T54 专用审计/binding 文件包含 9 个测试；与 plan revision API 合并执行：

    21 passed, 0 failed, 0 skipped

最新宽邻接回归覆盖 audit/binding、revision/editor/API、PostgreSQL revision
store、configured generation/start、budget/policy、grounded knowledge、
KnowledgeBindingResolver、Agent Runtime、prep/context、session serialization、
PostgreSQL session store、API 与 runtime migrations：

    345 passed, 0 failed, 0 skipped

全仓回归：

    2703 passed, 9 failed, 3 skipped

T53 基线为 2696 passed、7 failed、3 skipped；T54 新增 9 个测试，因此本轮总
用例数恰好增加 9。新增的两个失败均位于未修改的
test_context_artifact_store_postgres.py，且与既有 PostgreSQL 跨时钟清理失败
同类。本轮测得 PostgreSQL clock_timestamp() 比 Python UTC 快
3.203104 秒，而这些测试使用 Python now + 1 second 作为数据库清理阈值。

九个全仓失败均被如实保留：

1. agent runtime 旧 perf_counter mock ticks 耗尽；
2. context artifact failed cleanup 跨时钟；
3. context artifact concurrent cleanup 跨时钟；
4. interview generation chunk cleanup 跨时钟；
5. interview graph 旧期望 next_question，当前最终态为 finish；
6. interview workflow payload cleanup 跨时钟；
7. historical publication allowlist 与 quality branch 差异不兼容；
8. 旧 latest migration 断言仍期望 followup_decision_v1；
9. 既有 dependency lock hash 漂移。

这些失败不涉及 T54 修改的模块，也没有被标成 PASS 或作为 T54 acceptance
gate。其他验证：

- compileall app/tests：PASS；
- git diff --check：PASS（仅 Windows LF/CRLF 提示）；
- T54 import contract：PASS；
- frontend Vitest：28 passed；
- frontend Vite production build：PASS；
- Python ruff：TOOLING_MISSING；
- npm run check：TOOLING_MISSING（package script 引用 eslint，但仓库未安装）；
- real Provider calls：0。

## 真实性与阶段边界

- T54 不需要真实 Provider 或人工质量评审，所有 Provider 边界测试均为确定性
  double；
- T54 不声明真实问题质量、费用、token 用量或 Provider PASS；
- 统一授权仍限定 DeepSeek deepseek-chat，不允许自动替换模型；
- 当前 Provider Quality 任务的模型版本漂移继续如实保持
  BLOCKED_MODEL_VERSION_DRIFT，且 provider_called=false；
- 该 Provider 阻塞不暂停无关 Engineering；
- T55、T56 与 T57 尚未完成，T55–T72 仍继续执行；
- Goal 在 T72 真正完成前保持 active。

机器证据：docs/interview-quality-v1-t54-evidence.json。

## 回滚

回滚 T54 提交会移除 audit_json、结构化 Knowledge Binding、公开隐私投影和
相关测试。回滚不得删除既有 plan families/revisions/sessions，也不得通过恢复
旧实现重新暴露 binding hash、允许 custom question 伪造 grounding，或引入
第二套 revision/start 实现。
