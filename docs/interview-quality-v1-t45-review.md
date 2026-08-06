# Interview Quality V1 — T45 自动审查

## 结论

T45 Engineering 为 `PASS`。离线语义 Gate、v1/v2 隐藏随机化、独立人工盲审记录、关键经历编造双审、可选离线 Judge 冻结配置以及在线 Judge 禁止边界均已实现并自动验证。

T45 Quality 为 `BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN`。当前没有独立技术审查人的真实记录，因此不能声称人工判断的经历编造 `observed_count=0`、v2 优于 v1，或第 5.5 节离线语义阈值已经通过。该阻塞不撤销 Engineering PASS，也不阻止继续无外部依赖的任务。

## 实现范围

核心实现位于 `app/services/report_semantic_review.py`，包括：

- 冻结 source dataset 模型和合成数据边界；
- 确定性、可复现的 A/B 随机化；
- reviewer packet 与 coordinator-only assignment key 分离；
- dataset、packet、key、review sheet 和 Judge 配置的 SHA-256 闭包；
- 五项 A/B 语义评分、经历编造判断和版本偏好；
- false positive/false negative 记录；
- 关键 case 两名不同独立技术审查人的 fail-closed 要求；
- v2 技术正确性、回答支撑、总结覆盖、可执行性和措辞校准指标；
- 技术正确性、回答支撑、总结覆盖和可执行性 `>= 90%` 的冻结阈值；
- 可选离线 Judge 的 Provider、模型、Prompt、版本、Prompt hash、数据集 hash 和配置 hash 校验；
- 在线 execution context 中启用 Judge 时返回 `FAIL_PROTOCOL_INTEGRITY`；
- Judge 结果不能满足缺失的人工审查。

协议文档位于 `docs/interview-quality-v1-report-blind-review-protocol-v1.md`。冻结工程 fixture 位于 `tests/fixtures/report_semantic_review_pairs_v1.json`，覆盖 T43 的 6 个事实边界 case。

## Plan 条款映射

| T45 条款 | 当前证据 |
|---|---|
| 技术正确性 | A/B 1–5 分；解盲后计算 v2 `score >= 4` 通过率 |
| 评价由回答支持 | reviewer packet 同时提供候选人回答；独立 `answer_support` 指标 |
| 候选人经历编造 | A/B 分别记录 `not_observed/observed/uncertain`；observed 必须提供片段 |
| 跨题总结覆盖 | 独立 `summary_coverage` 指标和 90% Gate |
| 建议可执行性 | 独立 `actionability` 指标和 90% Gate |
| 措辞校准 | 独立 `tone_calibration` 指标 |
| v2 相对 v1 更有帮助 | 审查时只记录 A/B/tie，冻结审查表后解盲统计 |
| v1/v2 随机顺序、隐藏版本 | packet 只含 A/B；seed 和版本映射仅存在 assignment key |
| 至少一名独立技术审查人 | 完整 judgment 强制角色、独立性和盲法声明 |
| 关键经历编造 case 双审 | 需要两个不同 reviewer ID；重复行不能满足且被视为协议错误 |
| 冻结可选离线 Judge | 启用时所有 Provider/model/Prompt/version/hash 字段必填且互相校验 |
| Judge 不替代关键人工确认 | 没有人工记录时，即使注入完整 Judge bundle 仍为 BLOCKED |
| false positive/false negative | 每条人工记录均含布尔字段；为 true 时必须填写 error notes |
| 在线不调用语义 Judge | 生产 API、Runtime、Worker 和 Graph 无模块导入；online context fail-closed |

## 自动审查发现和修复

### 1. 收集了语义评分但未正式计算第 5.5 节阈值

首轮实现只记录五项 A/B 分数。自动审查后增加了解盲后的 v2 通过率，并将技术正确性、回答支撑、总结覆盖和可执行性低于 90% 定义为 `FAIL_SEMANTIC_THRESHOLDS`。

### 2. Packet/key hash 闭包未重新验证 source dataset 内容

首轮 Gate 能发现 packet、key 和 sheet 漂移，但没有逐 pair 证明 A/B 内容仍来自冻结 source dataset。修复后 Gate 必须接收 source dataset，重算 canonical dataset hash，并重新验证候选人回答、coverage、critical 标记、A/B presentation 和 content hash。

### 3. 同一审查人的重复行可能影响聚合指标

重复 reviewer/pair 记录现在返回 `DUPLICATE_REVIEWER_PAIR_JUDGMENT`，不能作为双审，也不能静默进入指标聚合。

### 4. `NOT_RUN` 时发布数字 0 会制造人工结论错觉

首轮结果在没有人工记录时输出 `v2_fabrication_observed_count=0`。现已改为 `null`；false positive、false negative、偏好和语义通过率在 `NOT_RUN` 时同样为 `null`。只有真实人工记录存在后才发布这些计数。

## 安全与运行时边界

- fixture 仅包含合成回答；`contains_real_candidate_data=false`；
- fixture 不含 Principal Memory；
- T45 没有真实 Provider 调用；
- 没有新增网络 client 或 Provider adapter；
- 在线 API、报告 Runtime、报告 Worker 和 Graph 不导入语义审查模块；
- 在线发布不会等待人工审查或离线 Judge；
- Judge bundle 是显式离线注入数据，不包含实际网络调用实现；
- assignment key 不应进入 reviewer packet，也不应与真实敏感数据一起提交。

## 验证结果

最终验证范围：

```text
T45 semantic review focused: 17 passed
T45 + answer guidance + runtime gate + schema v2: 41 passed
full report regression with PostgreSQL 16: 393 passed, 0 skipped
compileall app/tests: PASS
online Judge import scan: 0 matches
diff check: PASS
secret scan: PASS
provider_calls: 0
```

非阻塞警告：FastAPI TestClient 依赖发出一条既有的 `StarletteDeprecationWarning`，本任务未修改该依赖边界。

## 剩余质量工作

T49 仍需：

1. 冻结完整 semantic/adversarial/blind-test 数据集，而不是只使用 6-case 协议 fixture；
2. 邀请至少一名独立技术审查人；关键经历编造 case 使用双审；
3. 在版本隐藏状态下完成并冻结评分；
4. 解盲后发布样本规模、覆盖类型、限制、拒绝原因和 false positive/false negative；
5. 证明冻结数据集上的人工经历编造 `observed_count=0`；
6. 证明 v2 在准确性、可解释性和可执行性上至少不劣于 v1。

这些外部质量证据未完成前，不得把 T45/T49 Quality 写为 PASS。
