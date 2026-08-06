# Interview Quality V1 — T50 自动审查

## 结论

T50 Engineering 为 PASS，自动审查为 PASS，Quality 为 NOT_REQUIRED。

Schema v2 的八个既有 configuration 字段、允许值、预算含义、generator/policy 版本语义、snapshot/hash 关系、评分隔离和 v1 parser 默认值已经冻结为机器可读 policy。T50 没有创建第二套 Plan Schema、editor、revision store 或 start API，也没有提前修改 3–5 题校验。

T51 仍负责具体时长到题数/追问预算的映射与统一估算公式；T52 仍负责生成、fallback、Provider 超预算处理和 launch validation；T54 仍负责最终用户提示交互。

## 冻结 Policy

文件：

    config/interview_plan_generation_policy_v1.json

身份：

    schema_version=interview-plan-generation-policy-v1
    policy_version=interview-plan-config-strategy-v1
    plan_schema_version=interview-plan-v2
    canonical_sha256=d37b1172bba95aa1623882fa630e6817955570e708af6b830e0c028bdcc1cbb7

loader 不只验证 Pydantic shape，还校验完整 canonical SHA-256。任何允许值、effect mapping、user-visible semantics 或预算含义变化都会产生 hash drift，必须发布新的 policy version，不能静默原地修改 v1。

## Configuration 契约

    difficulty:
      foundation | intermediate | advanced

    target_duration_minutes:
      15 | 30 | 45 | 60

    focus_preset:
      technical_depth | system_design | project_review | balanced

    question_type_budget:
      project | technical | system-design | behavioral

    max_followups_per_question:
      2

    followup_policy_version:
      fixed_v1 | adaptive_v1

question_type_budget 是按题型的主问题生成目标。它采用 sparse 表达，缺失题型计为 0；允许某一题型为 0，但总目标必须至少为 1。Boolean、字符串、负数和未知题型都被拒绝，避免 Pydantic 宽松转换把 true 或 "1" 当成合法预算。

expected_followup_budget 是整个计划的预计追问总数，只用于生成提示和时长估算；它不是运行时 quota。adaptive policy 可以使用更少追问，每个主问题最多 2 次仍是硬安全上限。

generator_version 必须是稳定的小写版本标识。InterviewPlanRevision 顶层 generator_version 必须和 configuration snapshot 内的值完全一致，防止一个 revision 同时声称两个 generator 事实。

## 配置效果和评分隔离

Policy 对八个字段逐一冻结 effect：

| 字段 | 可以影响 | 不可以影响 |
|---|---|---|
| difficulty | 问题复杂度、配置摘要 | 评分 rubric/通过阈值 |
| target_duration_minutes | T51 budget profile、估算、warning | 精确时间 SLA、评分宽严 |
| focus_preset | 题型分配、prompt emphasis | 评分 rubric |
| question_type_budget | 生成题型目标、deterministic enforcement | 评分 rubric |
| expected_followup_budget | 追问估算、时长估算 | runtime hard cap |
| max_followups_per_question | runtime hard cap=2 | 评分 rubric |
| generator_version | prompt/enforcement 版本 | 评分 rubric |
| followup_policy_version | runtime decision policy | 评分 rubric |

report_rule_score、evaluator 和 report runtime Gate 对 T50 configuration/policy 的导入匹配数为 0。Policy 同时冻结 configuration_may_change_rubric=false 和 configuration_may_change_passing_threshold=false。

## Snapshot 和 Hash

八个 configuration 字段全部进入 InterviewPlanV2.configuration_snapshot，plan canonical hash 因任一合法字段变化而变化。question_type_budget 的 key order 经 canonical JSON 归一化，不会制造假漂移。

自动审查发现原 hash helper 对已构造的 BaseModel 直接 dump。Pydantic model_copy 默认不重新执行 validator，因此调用方可能先构造非法 configuration，再把它写进一个看似可信的 plan hash。现在：

- plan_configuration_sha256 会 dump 后重新 PlanConfigurationSnapshot.model_validate；
- plan_payload_sha256 会 dump 后重新 InterviewPlanV2.model_validate；
- evaluate_plan_configuration_policy 会重新校验 configuration 和 policy；
- policy 自身必须匹配完整冻结 canonical hash。

绕过测试证明非法 followup_policy_version 通过 model_copy 注入后，configuration hash、plan hash 和 policy evaluation 都会失败。

## Revision 和兼容边界

revision store 继续保留 generator_version 参数作为既有调用契约，但 store 构造的 InterviewPlanRevision 会校验它与 snapshot 一致。不一致时 fail closed，不写 revision。

API 测试 helper 原先先用默认 plan-generator-v2 转换 legacy plan，随后向 store 传 plan-generator-v2-test，制造双版本事实。修复后 legacy_to_v2 转换边界一次性写入 plan-generator-v2-test；产品 /api/prep 本来就统一使用 plan-generator-v2。

v1 parser 保留且不调用 Provider。默认值保持：

    difficulty=intermediate
    target_duration_minutes=30
    focus_preset=balanced
    followup_policy_version=fixed_v1
    max_followups_per_question=2

转换仍为既有 Schema v2 boundary，分配稳定 opaque UUID；不会重新启用 legacy q1..qN 身份契约。

## T50/T51/T52 边界

T50 故意没有修改以下内容：

- InterviewPlanV2 当前 3–5 题 validator；
- editor 当前 minimum/maximum question count；
- target duration 到问题范围的 15/30/45/60 映射；
- estimated_minutes 公式；
- Provider 返回题数过少/过多的裁剪或拒绝；
- duration 偏离 warning 和 launch blocking；
- fallback plan 的配置化题量。

这些是 T51/T52 的显式验收范围。如果 T50 提前修改，会把策略冻结与预算模型实施混在一个不可审计阶段。

## 自动审查发现与修复

1. generator version 存在双事实风险：新增 revision/snapshot 一致性 Gate。
2. budget 字段允许宽松数值转换：改为严格 integer/Boolean 拒绝。
3. followup policy 接受任意字符串：冻结为 fixed_v1/adaptive_v1。
4. question_type_budget 可以为空或总数为 0：要求至少一个主问题目标。
5. model_copy 可以绕过 hash 前校验：hash/evaluation 全部重新 model_validate。
6. policy 非结构语义可以原地漂移：冻结完整 canonical SHA-256。
7. API legacy 测试 boundary 传入两个 generator 版本：在转换时统一版本。

## 验证

    T50 policy focused: 17 passed
    Plan Revision/Editor/API/PostgreSQL store: 45 passed, 0 skipped
    Session serialization/PostgreSQL store: 39 passed, 0 skipped
    API/Prep/PrepContext: 60 passed, 0 skipped
    PostgreSQL: 16.14
    pgvector: 0.8.6
    policy JSON/Pydantic/canonical hash closure: PASS
    scoring configuration import scan: PASS_ZERO_MATCHES
    compileall app/tests: PASS
    diff check: PASS
    secret scan: PASS_ZERO_MATCHES
    provider_calls: 0

既有非阻塞警告：FastAPI TestClient 发出 StarletteDeprecationWarning。

## 真实性边界

- T50 没有 Provider 调用；
- T50 没有声称四种时长已经生成不同题量，那是 T51/T52；
- T50 没有声称 fallback 已经配置化，那是 T52；
- T50 没有声称前端时长提示已经完成，那是 T54；
- T50 没有改变评分 rubric 或 passing threshold。
