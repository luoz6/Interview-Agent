# Interview Quality V1 报告离线盲审协议

协议版本：`report-semantic-blind-review-v1`

适用范围：T45 离线语义 Gate；T49 冻结数据集验收

目标读者：独立技术审查人、评测协调人和质量验收负责人

## 1. 目标与边界

本协议用于比较同一合成候选人回答对应的两份报告内容。审查人只看到随机排列的 A/B，不知道哪一份来自 v1 或 v2。审查覆盖：

1. 技术正确性；
2. 评价是否由候选人回答支持；
3. 是否编造候选人的公司、规模、职责、指标、金额、延迟、动作或结果；
4. 跨题总结是否覆盖主要强弱项；
5. 建议是否可执行；
6. 措辞是否过严、过松或绝对化；
7. 哪个版本更有帮助。

本协议不是在线报告发布 Gate。在线 API、报告 Worker 和 Runtime 不得调用语义 Judge。在线发布只使用 T44 已冻结的确定性规则；语义判断在离线环境完成。

自动校验只能证明协议结构、随机化、hash、记录完整性和阈值计算正确，不能替代独立人工判断。

## 2. 角色与职责

### 2.1 评测协调人

协调人负责：

- 冻结数据集和数据集 hash；
- 选择不少于 16 个字符的随机种子；
- 分别生成审查包和版本映射密钥；
- 只把审查包交给审查人；
- 在审查记录冻结后解盲；
- 保存拒绝原因、争议、false positive 和 false negative；
- 发布样本规模、覆盖类型和限制。

协调人不得在审查完成前向审查人透露随机种子、版本映射或任何 v1/v2 标识。

### 2.2 独立技术审查人

每个 case 至少需要一名独立技术审查人。审查记录必须声明：

```text
reviewer_role=independent_technical_reviewer
independence_attested=true
assignment_was_hidden=true
```

关键经历编造 case 需要两名不同的独立技术审查人。相同审查人的重复记录不构成双审。

### 2.3 可选离线 Judge

离线 Judge 只提供辅助对照。启用前必须冻结：

- Provider；
- 精确模型标识；
- Prompt 原文；
- Prompt 版本；
- Prompt SHA-256；
- 数据集 SHA-256；
- 配置 SHA-256；
- 实际 Provider 调用数。

Judge 不能补齐缺失的人工审查，不能确认关键禁止项，也不能使 `NOT_RUN` 或 `BLOCKED` 变成 `PASS`。当前 T45 未获 Judge Provider 调用授权，正式证据中的调用数必须保持为 `0`。

## 3. 冻结 Artifact

### 3.1 Source Dataset

`SemanticReviewDataset` 保存协调人可见的原始 pair：候选人回答、v1 展示内容、v2 展示内容、覆盖类型和关键 case 标记。当前协议冻结 fixture：

```text
tests/fixtures/report_semantic_review_pairs_v1.json
```

该 fixture 含 6 个合成关键 case，并复用 T43 的事实边界类别。它不包含真实候选人数据或 Principal Memory。

### 3.2 Blinded Review Packet

审查包只含：

- 合成候选人回答及其 hash；
- coverage types；
- `critical_fabrication_case`；
- Variant A；
- Variant B；
- 数据集、随机种子和 assignment commitment；
- 审查说明。

审查包不含随机种子、A/B 到 v1/v2 的映射或 source version 字段。

### 3.3 Assignment Key

版本映射密钥单独保存，仅协调人可读。它包含：

- 随机种子；
- 每个 pair 的 A/B 到 v1/v2 映射；
- seed commitment；
- assignment commitment；
- 审查包 hash。

审查包、版本密钥和人工审查表通过 hash 闭包绑定。任一不匹配都返回 `FAIL_PROTOCOL_INTEGRITY`。

### 3.4 Human Review Sheet

人工审查表只引用审查包 hash。每条完成记录包括审查人身份或稳定化名、独立性声明、五个 A/B 评分、A/B 经历编造判断、偏好、理由、关键禁止项确认以及 false positive/false negative 字段。

不得把 assignment key 嵌入人工审查表。

## 4. 随机化与隐藏版本

随机化使用以下确定性输入：

```text
SHA-256(randomization_seed + NUL + pair_id)
```

摘要首字节的最低位决定 A 是 v1 还是 v2；B 使用另一版本。同一数据集和种子必须生成完全相同的审查包和密钥。不同种子可以改变排列。

随机种子只出现在 coordinator-only assignment key 中。审查包只保存 seed commitment，允许完成后验证随机化，没有足够信息在审查前解盲。

## 5. 人工审查步骤

### 第一步：确认独立性和盲法

审查人确认自己没有参与待比较报告的实现或标注，并且没有看到 assignment key、随机种子或版本标签。如果盲法被破坏，停止该条审查并通知协调人重新随机化。

### 第二步：逐 pair 阅读候选人回答

先阅读候选人回答，再分别阅读 A 和 B。禁止根据文字风格猜测版本，也禁止使用未出现在当前 case 中的候选人历史、简历、JD 或记忆。

### 第三步：独立评分 A 和 B

每个维度使用 1–5 分：

| 分数 | 含义 |
|---:|---|
| 1 | 明显错误、无依据或不可用 |
| 2 | 存在重大问题，主要结论不可靠 |
| 3 | 部分可用，但有实质性遗漏或校准问题 |
| 4 | 通过，整体正确、有依据且可执行 |
| 5 | 强通过，准确、清楚、完整且边界明确 |

评分维度：

- `technical_correctness`；
- `answer_support`；
- `summary_coverage`；
- `actionability`；
- `tone_calibration`。

自动指标以 `score >= 4` 计为通过。v2 技术正确性、回答支撑、总结覆盖和可执行性通过率阈值均为 `>= 90%`。措辞校准率必须发布，但当前协议不单独用它覆盖其他禁止项或阈值。

### 第四步：检查经历编造

对 A 和 B 分别选择：

- `not_observed`：冻结 case 中未观察到经历编造；
- `observed`：观察到至少一项无回答证据的候选人经历事实；
- `uncertain`：无法可靠判断，需要争议处理。

选择 `observed` 时必须记录报告中的具体片段。`not_observed` 只描述该冻结样本的观察结果，不得扩写为总体概率为零或“系统绝不会编造”。

关键 case 必须设置 `critical_forbidden_item_checked=true`。任一关键 case 未检查、仅单审或仍为 `uncertain`，Quality Gate 都不能通过。

### 第五步：记录偏好和错误类型

审查人选择 A、B 或 tie，并解释哪份报告更准确、更可解释或更可执行。发现审查规则或可选 Judge 的误报、漏报时，分别设置 `false_positive`、`false_negative` 并填写 `error_notes`。

### 第六步：冻结审查表后解盲

协调人先计算并保存人工审查表 hash，再使用 assignment key 解盲。禁止根据解盲结果回改原始评分。争议解决应新增记录，不覆盖失败证据。

## 6. Gate 状态

| 状态 | 条件 |
|---|---|
| `BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN` | 没有任何人工记录 |
| `BLOCKED_INCOMPLETE_HUMAN_REVIEW` | 至少一个 pair 没有独立人工记录 |
| `BLOCKED_CRITICAL_DOUBLE_REVIEW_NOT_RUN` | 关键 case 少于两名不同审查人 |
| `BLOCKED_CRITICAL_FORBIDDEN_ITEM_UNRESOLVED` | v2 关键禁止项仍为 uncertain |
| `FAIL_CANDIDATE_EXPERIENCE_FABRICATION` | 解盲后 v2 存在人工判断的经历编造 |
| `FAIL_SEMANTIC_THRESHOLDS` | 完整人工审查低于冻结语义阈值 |
| `FAIL_PROTOCOL_INTEGRITY` | 数据集、packet、key、sheet 或 Judge hash/边界不一致 |
| `PASS` | 人工审查完整、关键 case 双审、v2 编造 observed count 为 0、无 uncertain 且阈值通过 |

状态优先级是协议完整性、人工完整性、关键禁止项、语义阈值。Judge 结果不参与人工完整性的满足条件。

## 7. 使用参考

下面的 Python 片段生成内存中的审查包和 coordinator-only key；调用方必须将二者保存到不同的访问边界：

```python
from pathlib import Path

from app.services.report_semantic_review import (
    build_blinded_review_artifacts,
    load_semantic_review_dataset,
)

dataset = load_semantic_review_dataset(
    Path("tests/fixtures/report_semantic_review_pairs_v1.json")
)
artifacts = build_blinded_review_artifacts(
    dataset,
    randomization_seed="replace-with-a-frozen-secret-seed",
)

reviewer_packet = artifacts.packet
coordinator_only_key = artifacts.assignment_key
```

生成正式验收 Artifact 时，应在受控离线目录保存 seed 和 assignment key；不得提交含真实敏感数据、Provider 凭证或未脱敏响应的文件。

## 8. 当前状态和限制

T45 当前只完成工程协议和自动验证。尚无独立技术审查人记录，因此正式状态必须是：

```text
engineering_status=PASS
human_review_status=NOT_RUN
quality_status=BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN
provider_calls=0
```

当前 6-case fixture 只验证协议与事实边界。T49 才负责冻结完整的 semantic/adversarial/blind-test 数据集，运行独立盲审并判断第 5.5 节 Gate；在此之前不能宣称 v2 相对 v1 更好，也不能宣称人工观察到的经历编造数为 0。
