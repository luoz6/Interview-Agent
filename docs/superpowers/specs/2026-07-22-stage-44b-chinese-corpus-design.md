# Stage 44B 中文知识语料与检索质量设计

## 1. 目标与边界

Stage 44A 已完成远程 `BAAI/bge-m3` 提供商、版本化 pgvector 存储、原子激活、历史证据回放和 v1 检索门禁。Stage 44B 只扩展中文知识内容和检索质量，不改变公开面试 API、Prep 到 Reviewer 的证据契约、用户体系、语音能力或前端页面。

Stage 44B 分为两批：

- **44B1：合约与中文基线。** 引入 manifest v2、中文内容校验、中文资料规则，重写现有 25 个稳定 ID，并在隔离 RC 表中激活一个中间版本。
- **44B2：规模与最终质量。** 按领域批次新增约 115 个单元，形成约 140 个单元的最终语料，创建 72 条中文 v2 查询，执行完整质量和隐私验收。

所有自然语言内容均为中文。`FastAPI`、`Redis`、`MySQL`、`PostgreSQL`、`Kafka`、SQL、代码标识符和 URL 属于技术标识，按官方写法保留。Stage 42 的英文 v1 数据集冻结为内部兼容性回归，不进入用户面试流程，也不被翻译或覆盖。

## 2. 已确认的方案

### 2.1 合约优先

44B1 先锁定结构、枚举、来源和指标，再写规模化内容。44B2 只增加通过合约的领域批次，避免在 115 个单元完成后才发现字段、领域或评分定义需要返工。

### 2.2 领域分布

| 领域 | 最终数量 | 44B2 新增 |
| --- | ---: | ---: |
| Python | 8 | 8 |
| FastAPI | 12 | 7 |
| Redis | 20 | 15 |
| MySQL | 13 | 8 |
| PostgreSQL | 12 | 12 |
| Kafka | 20 | 15 |
| 分布式系统 | 30 | 25 |
| 可靠性、可观测性与容量规划 | 25 | 25 |
| 合计 | 140 | 115 |

评估组与领域不是一一对应关系：Python 与 FastAPI 属于 `fastapi` 评估组，MySQL 与 PostgreSQL 属于 `relational-database` 评估组，可靠性相关的系统设计单元可进入 `reliability` 评估组。旧的 `mysql`、`system-design` 等标识继续可读，新增 `python`、`postgresql`、`reliability` 标识通过显式枚举加入。

## 3. Manifest v2 与内容模型

### 3.1 Front matter

每个 Markdown 单元保留稳定 ASCII `id`，并新增完整 v2 元数据：

```yaml
id: redis_consistency
title: Redis 缓存一致性的边界
domain: redis
source_type: theory
content_kind: mechanism
tags: [redis, 缓存, 一致性]
aliases: [缓存一致性, Cache-Aside]
difficulty: intermediate
question_patterns:
  - 缓存与数据库如何保持一致？
  - 为什么通常先更新数据库再删除缓存？
references:
  - title: 中文资料标题
    url: https://example.cn/reference
    source_kind: official_cn
    publisher: 发布方
```

必需字段和约束：

- `id` 使用稳定的小写 ASCII 标识，现有 25 个 ID 不得改变。
- `title`、`question_patterns`、引用 `title` 和正文为中文；技术专名、代码、SQL、URL 除外。
- `domain` 只能取 `python`、`fastapi`、`redis`、`mysql`、`postgresql`、`kafka`、`system-design`、`reliability`。
- `source_type` 只能取 `theory`、`engineering_guide`、`expert_benchmark`。
- `content_kind` 只能取 `mechanism`、`failure_mode`、`engineering_practice`、`benchmark`、`hard_negative`。
- `tags` 至少包含规范领域标识和一个中文语义标签；技术规范标签可保留官方写法。
- `aliases` 为 1 至 8 个中文同义表达或技术规范名称。
- `difficulty` 只能取 `beginner`、`intermediate`、`advanced`，它是内部枚举，不作为英文界面文案。
- `question_patterns` 为 2 至 5 条中文面试问法。
- 正文去除代码块和 URL 后包含 300 至 1200 个中文字符，并按“核心结论、机制与边界、常见错误、工程权衡、可观察评分信号”组织。

解析使用作为直接依赖声明的 PyYAML 安全加载器，而不是继续扩展当前的逗号分隔解析。加载器增加重复映射键检查，并由 Pydantic 模型拒绝未知字段、错误类型和非法枚举。v1 manifest 读取和测试保持兼容，v2 manifest 固定写入 `app/data/knowledge/manifest_v2.json`，不覆盖历史 `manifest.json`。

### 3.2 中文资料准入

只允许中文资料：

1. 有可靠中文官方资料时，至少引用一个 `official_cn` 来源。
2. 没有中文官方资料时，至少引用两个 `secondary_cn` 来源，且发布方和规范化主机名必须独立。
3. 二手资料必须有明确发布方、稳定 URL 和可核对的技术论断，不接受匿名粘贴、SEO 聚合、营销软文或机器翻译转载。
4. 所有引用标题为中文，URL 使用 HTTPS、可解析且不得重复。URL 可包含技术标识或英文路径，但不构成英文正文。
5. 资料只用于核对结论、边界和反例；仓库保存原创中文技术总结，不复制来源正文。

自动检查 URL 语法、重复引用、发布方独立性和字段完整性；来源页面语言、论断一致性和原创性由每个批次的人工审查清单确认。外部站点的 403、限流或临时不可达不会被误判为内容正确性，但必须保留可复核的来源信息或替代来源。

### 3.3 质量分布

完整语料的难度目标为：基础 20% 至 30%、中等 45% 至 60%、高级 20% 至 30%。每个评估组至少包含机制、失败模式、工程实践三种内容类型，并包含边界或 hard-negative 单元。manifest 还拒绝空正文、重复 ID、规范化正文重复、超出大小边界的文件和重复引用。

## 4. 两批数据流

### 4.1 44B1：中文 25 单元基线

1. 将现有 25 个单元升级到 manifest v2，保持 `id` 不变，补充中文正文、难度、问题模板和中文来源。
2. 构建 `stage44b1-zh-v2` manifest，验证 25 个单元、领域兼容、引用规则和内容哈希。
3. 在隔离的 `knowledge_chunks_stage44b_rc` 前缀准备并激活完整中间版本，不切换生产配置。
4. 运行 12 条全中文冒烟查询，每个评估组两条，验证中文查询、过滤、证据回放和有限向量。
5. 继续运行冻结的 30 条英文 v1 查询，保留 Stage 42 原始阈值作为兼容性门禁。

44B1 通过后，25 个内容哈希可在 44B2 中复用。若某单元在 44B2 继续修改，只有哈希变化的单元重新请求向量。

### 4.2 44B2：领域批次与最终版本

115 个新增单元按以下批次提交，每批都先离线验证，不激活半成品版本：

| 批次 | 新增数量 |
| --- | ---: |
| Python / FastAPI | 15 |
| Redis | 15 |
| MySQL / PostgreSQL | 20 |
| Kafka | 15 |
| 分布式系统 | 25 |
| 可靠性、可观测性与容量规划 | 25 |

每批必须通过 schema、中文比例、来源、重复内容、难度和内容类型检查，并完成“结论与引用一致”的人工审查。全部批次合并后生成约 140 单元的 `stage44b-zh-v2` manifest，一次性准备向量并激活完整版本。

生产晋级不属于自动验收动作。只有 RC 全部通过并获得操作方明确批准后，才使用与 RC 完全相同的 manifest 和内容哈希，在已配置的版本表中通过 `activate_corpus()` 原子切换；失败时保留上一活动版本，失败版本不成为活动版本，也不删除历史版本。

## 5. v2 评估数据集

新增 `tests/golden/knowledge_retrieval_v2.json`，不替换 v1。数据集包含 72 条全中文查询，六个评估组各 12 条：`fastapi`、`redis`、`relational-database`、`kafka`、`system-design`、`reliability`。

每条用例包含稳定 ASCII `case_id`、中文 `query_text`、`evaluation_group`、`canonical_tags`、`source_types`、`allowed_domains`、`primary_relevant_chunk_ids`、`accepted_related_chunk_ids`、`excluded_chunk_ids` 和 `top_k=5`。每条用例至少有一个 primary 相关单元，所有 ID 必须存在于 v2 manifest。

查询正文、评估说明和人工审查记录为中文；`case_id`、领域标识和语料 ID 是内部技术标识。数据集校验拒绝重复 ID、重复规范化查询、空相关集合、无效领域、无效来源过滤器和缺失语料 ID。

## 6. v2 指标与失败语义

### 6.1 确定性评分

- **Recall@5**：每条用例的 primary 与 accepted 相关集合中，被前 5 命中的比例，取 72 条宏平均。
- **MRR@5**：前 5 中第一个 primary 相关单元的倒数排名；没有命中记 0。
- **nDCG@5**：primary 相关度为 3，accepted related 为 1，其余为 0，按每条用例的理想 DCG 归一化后宏平均。
- **领域和过滤正确率**：所有返回单元必须满足 `allowed_domains`、规范标签和来源过滤器，72 条全部正确才通过。
- **排除违规率**：任何 `excluded_chunk_ids` 出现在前 5 即构成违规，违规数必须为 0。
- **向量有效率**：所有准备向量必须为 1024 维有限浮点数，比例必须为 1.0。
- **证据回放稳定率**：绑定的 ID 与内容哈希通过 `get_by_ids(expected_hashes=...)` 重放，比例必须为 1.0，且 Reviewer 路径不得调用嵌入。
- **观察完整率**：72 条必须各自产生观察；缺失观察使整次评估失败，不得缩小分母。

发布阈值为 Recall@5 >= 0.90、MRR@5 >= 0.80、nDCG@5 >= 0.85、领域和过滤正确率 1.0、排除违规率 0、向量有效率 1.0、证据回放稳定率 1.0、观察完整率 1.0，且检索 p95 <= 1500ms。冻结 v1 仍需满足 Stage 42 原始阈值。

### 6.2 失败与回滚

向量准备、manifest、指标和隐私审计在 RC 表前缀中执行。任一门禁失败时：

- 不更新生产活动版本；
- 不把缺失观察从分母删除；
- 保留失败版本的安全摘要以便诊断，但不保存查询正文或语料正文；
- 生产继续使用上一活动版本，历史证据版本不删除；
- 修复内容后使用新的 manifest 哈希重新准备，兼容向量按提供商身份和内容哈希复用。

## 7. 验收与隐私

Stage 44B 的真实验收必须显式设置 SiliconFlow provider、模型修订和隔离 RC 表前缀。验收运行 v2 全部 72 条、冻结 v1 全部 30 条、PostgreSQL 激活与回滚、历史证据回放、完整 Python 和浏览器回归。

验收产物只允许记录版本、数量、哈希、用例 ID、命中 ID、分数、状态、聚合指标、延迟、请求/重试/错误码和 provider/model 标签。禁止记录 API key、DSN、Authorization header、查询正文、语料正文、资料 URL、简历、职位描述、邮箱、手机号和绝对路径。隐私审计必须报告零违规。

## 8. 测试策略与发布门禁

44B1 先增加 schema v2、中文 lint、来源准入、12 条冒烟集、v2 指标公式和向量复用的无网络测试，再执行隔离真实 provider 验收。44B2 增加每个领域批次的 manifest fixture、72 条数据集结构测试、指标边界测试、排除 ID 测试、过滤正确性测试和完整证据回放测试。

Stage 44B 最终通过必须同时满足：

1. Stage 44A 验收记录为 PASS；
2. 活动 v2 manifest 包含 120 至 180 个有效单元，并满足上述 140 单元分布目标；
3. 72 条 v2 观察完整且所有新指标达标；
4. 30 条冻结 v1 继续达标；
5. PostgreSQL 激活、回滚和 Stage 42 历史证据回放通过；
6. SiliconFlow 真实运行与隐私审计通过；
7. Python、浏览器、JavaScript/CSS 及既有 Stage 40 至 Stage 44A 回归门禁保持绿色。

## 9. 非目标与后续条件

本阶段不引入新的 tokenizer、混合词法检索、ANN 索引、英文正文、多用户能力、语音能力或 UI 改造。只有当 140 单元规模下的失败案例证明纯 dense 检索无法达到 Recall@5 阈值时，才另行设计混合检索；只有在实测规模和 p95 证明必要时，才另行设计 ANN 索引。
