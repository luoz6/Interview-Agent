# Interview Quality V1 — Gate 4 自动审查

## 结论

    engineering_status=PASS
    quality_status=BLOCKED
    quality_reason=BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN
    human_review_status=NOT_RUN
    automatic_review=PASS
    provider_calls=0

Gate 4 Engineering 达标：报告 schema、观察聚合、跨题总结、行动证据绑定、候选人事实边界、确定性发布 Gate、degraded/unscored/failed 真值表、候选人一级信息层级，以及 Web/PDF/history revision 核心语义均已形成可复现的当前 HEAD 证据。

Gate 4 Quality 未达成。冻结的 24-case reviewer packet 已准备完成，但没有独立技术 reviewer 的真实 judgment。不能声明人工判断的候选人经历编造 observed_count=0、v2 相对 v1 不劣或更优、语义通过率达到 90%，也不能声明 Gate 4 Quality PASS。

按照 Plan 的 Engineering/Quality 正交状态规则，该 Quality 阻塞不撤销 Engineering PASS，也不阻止继续 T50 及后续无外部依赖工程任务。

## 任务链闭包

| Task | 提交 | Engineering 结果 |
|---|---|---|
| T39 | e8ab7db153fdd379c485dc3baa4320d3f7dd4cb7 | report semantic schema v2 |
| T40 | c59d02f43d034a7939ea0dff2ebe47d05b733dd2 | grounded observation aggregation |
| T41 | 21fc9a3f2e9b6cd73e920535914bff4e9ea32d79 | grounded cross-question summary |
| T42 | 4c8e9a6d5ba46153720dc0fcc9a62b53af6d33f7 | evidence-grounded priority actions |
| T43 | 4561ad3880fbe7e68a40798d98bc825fbdc80191 | candidate fact boundaries |
| T44 | 7f0b764da0460b6adbe1a963352638358c1e0520 | deterministic publication Gate |
| T45 | 5c9576ee12946307c282ac31e1671d486175b363 | blinded semantic review protocol |
| T46 | aa8a8fe5bd9001b694b2dd4201059a81aa1107fb | safe degraded report publication |
| T47 | 751cacc1636dcfb4679c7808ec5c146f12806f13 | candidate report hierarchy |
| T48 | e777b85d36ebd825dd9cdd2c9b050aed4d53d35c | Web/PDF/history revision parity |
| T49 | 73750a8ef8143e11bd9cfa5977fa61e73357f3b3 | frozen semantic blind-test cohort |

Gate 4 Engineering 的 Plan 依赖是 T39–T48；T49 提供独立 Quality 路径所需的冻结 cohort 和真实 NOT_RUN 证据。

## Engineering 验收映射

### Runtime Gate 只包含确定性规则

T44 的发布 Gate 校验 raw payload schema、禁止字段、question rule 分数重算、overall/dimension 聚合、unscored/null 不变量、冻结问答身份、candidate/reference namespace、claim/action/guidance reference 闭包、Principal Memory 排除、空值/数字占位以及 Artifact lineage。失败时不创建 Artifact、不 commit、不移动 active pointer。

离线 semantic review 模块在 app 的在线 API、worker 和 graph 路径中导入匹配数为 0；在线语义 Judge 调用数为 0。

### 关键 claim/action evidence refs 合法

T42 将 Top 1–3 行动绑定 question/evidence refs；T44 在发布前重新验证 claim、action、guidance 的 reference closure、目标存在性和当前 session namespace。当前完整 report 回归 419 项通过。

### degraded/unscored/failed 真值表

T46 冻结以下状态：

- 文案 Provider 失败且有效分数存在：degraded + scored/partial；
- 证据不足：degraded + unscored，数字分保持 null；
- schema/reference/fact Gate 失败：failed，不发布 Artifact；
- 新 job 失败时保留旧 active；
- 无 active 的失败可见并支持 retry；
- deterministic degraded fallback 不调用 Provider。

### 技术诊断不占据候选人一级页面

T47 固定六段候选人主层级：结论与评分状态、覆盖度和限制、主要优势、Top 行动、逐题证据与回答建议、评估限制。agent run、runtime event、retrieval path、reason code、revision lineage 等技术字段默认进入折叠附录。

### Web/PDF/history 核心语义一致

T48 让 Web 和 PDF 消费相同的 summary、priority actions、evidence、better answer 和 limitations 字段。PDF 绑定具体 report ID、revision 和 created_at；历史版本下载使用 immutable report ID，不跟随 active pointer 漂移；export failure 不修改 Artifact、job 或 active head。

本次 Gate 4 当前 HEAD 复测：

    backend report regression: 419 passed
    frontend full Vitest: 28 passed
    frontend production build: PASS
    compileall app/tests/scripts: PASS
    online semantic runtime import scan: PASS_ZERO_MATCHES

没有执行 PNG 截图或像素级 PDF 视觉 QA，因此不声明视觉像素审查 PASS。

## Quality 状态

T49 当前冻结数据：

    sample_size=24
    critical_case_count=20
    required_scenarios=17
    human_review_status=NOT_RUN
    completed_judgment_count=0
    independent_reviewer_count=0
    provider_calls=0

人工派生的 fabrication observed/uncertain、false positive/negative、v1/v2 preference、tie、technical correctness、answer support、summary coverage、actionability、tone calibration 和 non-inferiority 均为 null。

24 个 synthetic case 满足开展人工盲审的最小样本条件；这不等于任何人工阈值已经通过。首条 NOT_RUN 结果已经进入 append-only hash ledger，后续 review 必须追加新 sheet 和新 ledger entry，不得改写现有失败证据。

## 自动审查边界

- Engineering PASS 只依据确定性代码、fixture、hash closure、回归测试和构建结果；
- 没有把自动测试替代为人工语义结论；
- 没有调用 Provider 或可选离线 Judge；
- 没有模型 fallback；
- 没有真实候选人数据或 Principal Memory 数据进入 T49 cohort；
- 没有声称 synthetic cohort 代表总体概率；
- Gate 4 Quality 保持 BLOCKED，后续 Engineering 继续。
