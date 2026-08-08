# Provider 授权修订自动审查（2026-08-07）

## 结论

用户已明确允许使用 DeepSeek `deepseek-v4-pro`。本次修订创建新的统一无限额授权 `interview-quality-v1-20260807-unlimited-02`，取代旧授权 `interview-quality-v1-20260805-unlimited-01`。授权仍只覆盖 T27、T36、T57、T65，只允许合成、公开或脱敏数据，仍禁止真实候选人数据、自动模型替换和任何 fallback；`deepseek-v4-flash` 没有获得授权。

授权修订本身通过自动契约审查，但不构成 Provider Quality PASS。当前准确状态是 `BLOCKED_REVALIDATION_PENDING`：必须先冻结包含新授权 Manifest、类型约束和执行绑定的新候选，再完成 Provider 预检、受影响工程回归和 T65 六个质量维度。

## 不可变历史

- 旧授权已原样归档到 `config/provider_authorizations/interview-quality-v1-20260805-unlimited-01.json`；其 SHA-256 仍为 `ca6c10bcdd9d95d213d56b553bd889dc261cbe678a0fc405027c072899dbf5ac`。
- 历史 T36、T57、T65 的 `deepseek-chat` 阻断证据不修改、不删除，也不解释成新模型结果。
- 旧 T64/T65 Provider candidate `214a70646df9f23f38874a2854b9a334a10c269f` / tree `83d01ae72a483fb181b435dea4628843aa40c809` 不包含新授权，不能用于新的 Provider 推理。

## 当前冻结边界

```text
authorization_id=interview-quality-v1-20260807-unlimited-02
provider=DeepSeek_OPENAI_COMPATIBLE
base_url_host=api.deepseek.com
model=deepseek-v4-pro
allowed_fallback_models=[]
automatic_model_substitution=false
budget=UNLIMITED
requests=UNLIMITED
input_tokens=UNLIMITED
output_tokens=UNLIMITED
data_scope=SYNTHETIC_PUBLIC_OR_REDACTED_ONLY
real_candidate_use=PROHIBITED
context_window_tokens=128000
authorization_sha256=0a0d7576cc26b94da7abe4c880408358a29cd2a0472e54f5814f1d2fec28670a
gate_config_sha256=2b650efab1242c00d8e501046fba985ad6a6db191d6043058be0671f9f851535
```

GateConfig 阈值未修改。Plan 已修订为 v0.2.3，SHA-256 为 `446bb28746aee10fe9b79932cc585b6f734cc054b5be0fa52f409e5942f08f29`，140979 bytes。

## 自动审查

首轮测试在新 Manifest 写入后得到 `21 passed / 7 failed`。七个失败均来自测试目录仍构造旧授权模型、执行 Manifest 仍绑定旧授权哈希。修复冻结契约后，同一范围得到：

```text
29 passed / 0 failed
JSON validation=PASS
git diff --check=PASS
Provider inference calls=0
Provider data requests=0
screenshots=0
traces=0
```

扩展回归首轮得到 `159 passed / 3 failed / 1 skipped`。三处失败均为 CLI 测试仍构造旧模型目录、旧响应元数据或旧漂移场景；修复后同一完整范围得到：

```text
162 passed / 0 failed / 1 skipped / 1 warning
credential files scanned=14
credential value leaks=0
credential pattern leaks=0
media/archive changes=0
```

唯一 skip 来自 PostgreSQL runner 未获得本次专项命令的 `POSTGRES_DSN`。它不阻断授权契约修订，但在新的 T64 candidate 冻结前必须使用可达 PostgreSQL 重新运行并消除；不能把该 skip 计为新候选的 Engineering PASS。唯一 warning 是 Starlette/httpx 兼容层弃用告警，未改变本阶段行为。

正向预检只接受精确的 `deepseek-v4-pro`；目录缺少该模型、只有 `deepseek-chat` 或 `deepseek-v4-flash` 时仍会 `MODEL_VERSION_DRIFT`。模型存在但无法计价时仍会 `USAGE_METERING_UNAVAILABLE`。

## 后续门禁

1. 提交并冻结新的 Provider candidate revision/tree。
2. 对新候选执行授权、数据、定价、凭据和证据持久化预检。
3. 预检通过后，运行 T65 的六个真实 Provider 质量维度，逐项记录调用、token、延迟和成本。
4. 重跑受换模影响的工程与跨平台验证，不沿用旧候选结论。
5. 独立评审和正式盲测必须由符合协议的独立审查者完成；主 Agent 自动审查不能替代独立性。
6. Gate 6 Quality 未 PASS 前不启动 T68。
