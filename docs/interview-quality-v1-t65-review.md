# Interview Quality V1 — T65 自动审查

## 结论

```text
execution_control_status=PASS_FAIL_CLOSED
automatic_review=PASS
quality_status=BLOCKED
overall_status=BLOCKED
primary_blocker=MODEL_VERSION_DRIFT
quality_dimensions_run=0/6
offline_control_tests=233 passed / 0 failed
provider_inference_calls=0
provider_data_requests=0
formal_discovery_http_requests=2
first_data_request_sent=false
fallback_models_used=0
screenshots=0
traces=0
secret_leaks=0
open_engineering_findings=0
```

T65 已在 T64 唯一冻结候选 `214a70646df9f23f38874a2854b9a334a10c269f`、tree
`83d01ae72a483fb181b435dea4628843aa40c809` 上执行正式 Provider preflight。统一授权、冻结数据、
GateConfig、授权 Manifest、凭据、脱敏和本地证据持久化均通过；官方 DeepSeek `/models` 与定价页也均可达。
但是两处当前只列出 `deepseek-v4-flash` 和 `deepseek-v4-pro`，不包含授权的 `deepseek-chat`。

统一授权禁止自动换模，因而 runner 在首个评测数据请求前以 `MODEL_VERSION_DRIFT` 硬停止。没有生成、Decision、
追问、评分或报告请求，没有 token/cost，未使用 v4 fallback。该行为证明 fail-closed 执行控制有效，但绝不等于
T65 Quality PASS。

## 六个维度分别报告

| 维度 | 状态 | 原因 |
|---|---|---|
| 初始问题质量 | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | smoke 在发送首条合成 JD/简历前停止 |
| Followup Decision | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | 未构造真实 Decision 请求 |
| Follow-up Question 文本 | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | 未构造真实 Generation 请求 |
| 评分 | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | 未调用真实报告评分模型 |
| 报告语义 | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | 没有真实报告输出可进入语义/专家评审 |
| 延迟、调用和成本 | NOT_RUN_BLOCKED_MODEL_VERSION_DRIFT | 仅记录 preflight discovery；真实推理 cohort、token 和金额均不存在 |

任一维度都没有借用其他维度、fixture replay 或历史 Provider 结果宣称 PASS。六项完整基准仍是 Gate 6 Quality
的开放阻塞项。

## 正式 Provider preflight

```text
authorization_id=interview-quality-v1-20260805-unlimited-01
provider=DeepSeek
protocol=openai-compatible
base_url=https://api.deepseek.com
authorized_model=deepseek-chat
allowed_fallback_models=[]
automatic_model_substitution=false
credential_present=true
redaction_preflight_passed=true
dataset_manifest_match=true
gate_config_manifest_match=true
authorization_manifest_match=true
evidence_persistence_available=true
models_endpoint_ok=true
pricing_page_ok=true
model_request_attempts=1
pricing_request_attempts=1
model_ids=deepseek-v4-flash,deepseek-v4-pro
priced_model_ids=deepseek-v4-flash,deepseek-v4-pro
authorized_model_available=false
authorized_model_priced=false
hard_stop_conditions=MODEL_VERSION_DRIFT
provider_inference_calls=0
first_data_request_sent=false
```

宿主环境的 `OPENAI_MODEL` 指向 v4，但 runner 将其明确标记为 `environment_model_ignored=true`，实际请求身份仍
由授权 Manifest 固定为 `deepseek-chat`。环境变量不能覆盖授权，也没有把可用 v4 模型当成授权 fallback。

正式 preflight 复用了已冻结的 T57 初始问题 Provider runner 作为 T65 的首个 smoke 子步骤；该子 runner 的
Artifact `task=T57`，本 T65 聚合证据额外绑定唯一 candidate SHA/tree 和六维执行状态。复用不扩大 T57/T65
之外的授权范围，也没有发送候选数据。

## 冻结输入

| 输入 | 规模 | SHA-256 |
|---|---:|---|
| GateConfig | 10426 bytes | `2b650efab1242c00d8e501046fba985ad6a6db191d6043058be0671f9f851535` |
| Provider Authorization | 2150 bytes | `ca6c10bcdd9d95d213d56b553bd889dc261cbe678a0fc405027c072899dbf5ac` |
| initial-question-quality-v2 | 12 cases | `f1d1b45fbdaa9ccf703e151c0998cb619c33de2b62ec752bb711dbc89a330877` |
| followup-decision-quality-v2 | 100 cases | `ba4d5882618970e6e780729f07055e82b186e81f5c5f805ba383d472c078e517` |
| report-quality-v1 | 20 cases | `31b5116500b990d2ec2f35c048a9ec221a8e76fcab0175123183d5e2a9991c12` |
| report-semantic-blind-test-v1 | 24 pairs | `39bde7b72e3cfbf381645965cae48fcb01c6935a0b45cfa55503cf3d4634a62c` |
| plan generation policy v1 | 4937 bytes | `ba32a024507d8cb21da293b6601ee03d0ef0d6947444aeb69cf66f90f066f6b0` |

关键 Prompt/rubric 也绑定到 candidate：

- Followup Decision `followup-decision-v1`，SHA-256
  `597b101710677eef34cf7912cdb6b4108807baac23d2b0c3c16ca23e116d1025`；
- Followup Generation `followup-generation-v1`，SHA-256
  `3ef238cf8c8cfabe5261b08ad7dc71e182685d1322c39af80e16e428c26f1e53`；
- Report evidence prompt `stage40-evidence-v1`；
- Report scoring rubric `interview-quality-rubric-v3.3-candidate`，SHA-256
  `913d673fad8bfbde134788e2a48d96acfafe92936abded090bb3b9d5de514c03`；
- Report summary prompt `report-cross-question-summary-v1`，SHA-256
  `686fc6ba0f04e5ceeba83ce80ab9a89295e3871a1801bb41fd7d6c8448ae4051`。

## 离线控制回归

在 exact candidate worktree 上运行的确定性控制测试：

```text
initial question + Decision + follow-up text: 109 passed / 0 failed
report scoring + report semantic:             93 passed / 0 failed
authorization + Gate + performance controls: 31 passed / 0 failed
total:                                       233 passed / 0 failed
```

这些测试覆盖授权边界、模型漂移、用量计量、数据集/Prompt/rubric 合同、saved replay、质量指标和性能门槛。
它们只证明 T65 工具可执行且会 fail closed，不能替代真实 `deepseek-chat` benchmark。

## 自动审查和诊断记录

1. 初次在旧候选 `820328c` 的 fresh Windows worktree 执行 preflight 时，发现两份冻结 config JSON 会被
   `core.autocrlf=true` 转成 CRLF，触发 `GATE_CONFIG_OR_DATASET_DRIFT`。该候选作废；补充 LF 属性和回归后，
   T64 在新候选 `214a706` 上完整重跑并重新冻结。
2. 新候选的 Ubuntu 容器 preflight 对 `/models` 三次返回 invalid response，准确停止为
   `REPEATED_PROVIDER_FAILURE`；它只保留为网络诊断，未作为模型漂移的权威证据。
3. 同一新候选的 Windows 正式 preflight 成功访问 `/models` 和定价页，各 1 次；两者均只列 v4，因而权威
   blocker 为 `MODEL_VERSION_DRIFT`。
4. 自动审查扫描两个 preflight Artifact：没有 API Key 值、完整 JD、简历、答案、Principal Memory、PNG 或
   trace.zip；正式 Artifact 只有聚合 preflight 元数据。

## 数据、调用和停止边界

- 正式 discovery 有 2 个无候选数据的 HTTP 请求：1 个模型目录、1 个公开定价页。
- Provider 推理/评测请求为 0；input/output token、真实模型延迟和金额均为 NOT_RUN，而不是写成 0 成本 PASS。
- 没有真实候选人数据；正式 smoke 的 2 个合成 case 只被选择，内容从未发送。
- Key 仅来自安全环境；扫描的两个本地 Artifact 都不含 Key 值。
- 未截图、未生成 trace、未执行图像操作、未启用外部评测平台。
- unlimited 授权继续有效；本次停止原因不是预算，而是明确的模型版本硬停止条件。

T65 仍为 `BLOCKED_MODEL_VERSION_DRIFT`，`t65_complete=false`。该外部质量阻塞不会阻止不依赖 Provider 的
T66 工程验收；下一任务为 T66。总体 Goal 保持 `active`，T72 尚未完成。
