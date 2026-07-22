# Stage 44B1 中文语料基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 Stage 42/44A 英文 v1 语料和评估合约的前提下，建立独立的 25 单元中文 manifest v2 语料、中文运行时查询、12 条中文 pilot 评估和隔离 SiliconFlow RC 验收。

**Architecture:** 保留 `app/data/knowledge/` 作为冻结 v1 根目录，新建 `app/data/knowledge_v2/` 并使用独立 schema、manifest builder、loader、评估模型和指标模块。44B1 在持久隔离前缀 `knowledge_chunks_stage44b_rc` 中重新嵌入全部 25 个中文单元，不自动切换生产前缀。

**Tech Stack:** Python 3.11、Pydantic v2、PyYAML SafeLoader、SiliconFlow `BAAI/bge-m3`、PostgreSQL/pgvector、pytest、现有 Stage 44A artifact auditor。

---

## 范围与文件边界

冻结不改：

- `app/data/knowledge/**`
- `app/services/knowledge_eval_dataset.py`
- `app/services/knowledge_eval_metrics.py`
- `tests/golden/knowledge_retrieval_v1.json`
- Stage 42/44A 已封存产物和 manifest

44B1 新增的主要所有权：

- `app/services/knowledge_corpus_schema.py`：v2 front matter、中文 content-lint、引用规则。
- `scripts/build_knowledge_manifest_v2.py`：v2 manifest 和稳定哈希。
- `scripts/load_knowledge_v2.py`：仅扫描 v2 根目录并构造 `KnowledgeChunk`。
- `app/services/knowledge_eval_dataset_v2.py`：独立 v2 pilot/final 数据模型。
- `app/services/knowledge_eval_metrics_v2.py`：Recall@5、MRR@5、nDCG@5 和新门禁。
- `scripts/evaluate_knowledge_retrieval_v2.py`：独立 v2 检索观察和安全结果。
- `scripts/run_stage44b1_acceptance.py`：25 单元、pilot 和冻结 v1 的隔离验收。
- `scripts/audit_stage44b1_artifacts.py`：复用 Stage 44A 审计并增加 v2 禁止键。

### Task 1: 锁定 v2 Corpus Schema 与安全 YAML 解析

**Files:**
- Create: `app/services/knowledge_corpus_schema.py`
- Create: `tests/test_knowledge_corpus_schema.py`
- Modify: `requirements.txt`
- Generate: `requirements.lock.txt`

- [ ] **Step 1: 添加失败的 schema、中文 lint 和来源规则测试**

创建 `tests/test_knowledge_corpus_schema.py`，至少包含以下合约：

```python
from pathlib import Path

import pytest

from app.services.knowledge_corpus_schema import (
    DuplicateFrontMatterKeyError,
    load_knowledge_document_v2,
)


def valid_document() -> str:
    body = """# Redis 缓存一致性的边界

## 核心结论
更新数据库后删除缓存是常见基线，但仍需处理并发读写窗口和删除失败。

## 机制与边界
这里补足足够的中文机制说明、适用条件和并发边界，使正文达到三百个中文字符。

## 常见错误
不能把延迟双删当成绝对一致性，也不能忽略重试任务的幂等性。

## 工程权衡
方案需要在一致性、可用性、实现复杂度和数据库压力之间取舍。

## 可观察评分信号
回答应说明更新顺序、失败补偿、并发窗口、监控指标和降级策略。
"""
    body += "中文技术说明" * 30
    return f"""---
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
  - title: Redis 中文资料
    url: https://example.cn/redis-consistency
    source_kind: official_cn
    publisher: Redis 中文站
---
{body}
"""


def test_v2_document_accepts_complete_chinese_metadata(tmp_path: Path):
    path = tmp_path / "redis_consistency.md"
    path.write_text(valid_document(), encoding="utf-8")

    document = load_knowledge_document_v2(path)

    assert document.metadata.id == "redis_consistency"
    assert document.metadata.domain == "redis"
    assert document.metadata.references[0].source_kind == "official_cn"
    assert document.chinese_character_count >= 300


def test_v2_document_rejects_duplicate_yaml_keys(tmp_path: Path):
    path = tmp_path / "duplicate.md"
    path.write_text(valid_document().replace("domain: redis", "domain: redis\ndomain: mysql"), encoding="utf-8")

    with pytest.raises(DuplicateFrontMatterKeyError):
        load_knowledge_document_v2(path)


def test_secondary_sources_require_two_independent_publishers(tmp_path: Path):
    path = tmp_path / "secondary.md"
    text = valid_document().replace("source_kind: official_cn", "source_kind: secondary_cn")
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="two independent Chinese sources"):
        load_knowledge_document_v2(path)


@pytest.mark.parametrize("mutation", ["english sentence words are not allowed", "短正文"])
def test_v2_document_rejects_english_prose_and_short_body(tmp_path: Path, mutation: str):
    path = tmp_path / "invalid.md"
    text = valid_document()
    body = text.split("\n---\n", 1)[1]
    path.write_text(text.replace(body, mutation), encoding="utf-8")

    with pytest.raises(ValueError):
        load_knowledge_document_v2(path)
```

再覆盖未知字段、非法 domain/source_type/content_kind/difficulty、缺少领域 tag、非 HTTPS URL、重复 URL、相同 publisher/hostname 的两个二手来源、代码块和 URL 不计入中文字符数。

- [ ] **Step 2: 运行测试并确认模块缺失**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_corpus_schema.py -q
```

Expected: FAIL because `app.services.knowledge_corpus_schema` does not exist.

- [ ] **Step 3: 实现安全 loader 和 Pydantic schema**

在 `app/services/knowledge_corpus_schema.py` 定义以下公开接口：

```python
class KnowledgeReferenceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    url: AnyHttpUrl
    source_kind: Literal["official_cn", "secondary_cn"]
    publisher: str

class KnowledgeMetadataV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,127}$")
    title: str
    domain: Literal["python", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"]
    source_type: Literal["theory", "engineering_guide", "expert_benchmark"]
    content_kind: Literal["mechanism", "failure_mode", "engineering_practice", "benchmark", "hard_negative"]
    tags: list[str] = Field(min_length=2)
    aliases: list[str] = Field(min_length=1, max_length=8)
    difficulty: Literal["beginner", "intermediate", "advanced"]
    question_patterns: list[str] = Field(min_length=2, max_length=5)
    references: list[KnowledgeReferenceV2] = Field(min_length=1)

class KnowledgeDocumentV2(BaseModel):
    metadata: KnowledgeMetadataV2
    body: str
    chinese_character_count: int
```

同一模块定义 `DuplicateFrontMatterKeyError(ValueError)`，并实现两个公开函数：`load_knowledge_document_v2(path: Path) -> KnowledgeDocumentV2` 和 `strip_non_prose_markdown(text: str) -> str`。

实现要求：

- 用 PyYAML `SafeLoader` 子类检查 duplicate mapping keys。
- front matter 必须由首尾 `---` 明确包围。
- `strip_non_prose_markdown()` 删除 fenced code、inline code 和 `https://` URL 后再用 `[㐀-䶿一-鿿]` 计数。
- 对非白名单连续四个以上英文单词报错；技术标识白名单包含 Python/FastAPI/Redis/MySQL/PostgreSQL/Kafka/SQL/HTTP/HTTPS/ASGI/Cache-Aside。
- official 中文来源至少一个；没有 official 时，secondary 至少两个，publisher casefold 后不同且 URL hostname 不同。
- metadata title、question patterns、reference title 必须包含 CJK 字符。

- [ ] **Step 4: 添加 PyYAML 直接依赖并重建锁文件**

在 `requirements.txt` 添加：

```text
PyYAML>=6.0.2
```

Run:

```powershell
& 'F:\python3.11\python.exe' -m piptools compile --allow-unsafe --generate-hashes --output-file=requirements.lock.txt requirements.txt
& 'F:\python3.11\python.exe' -m pip check
```

Expected: lock regenerated; `pip check` reports no broken requirements.

- [ ] **Step 5: 运行测试并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_corpus_schema.py -q
git diff --check
git add app/services/knowledge_corpus_schema.py tests/test_knowledge_corpus_schema.py requirements.txt requirements.lock.txt
git commit -m "feat: define stage 44b corpus v2 schema"
```

### Task 2: 构建独立 v2 Manifest 与 Loader

**Files:**
- Create: `scripts/build_knowledge_manifest_v2.py`
- Create: `scripts/load_knowledge_v2.py`
- Create: `tests/test_knowledge_manifest_v2.py`
- Create: `tests/test_load_knowledge_v2.py`

- [ ] **Step 1: 写 manifest 和 loader 失败测试**

测试必须证明：

```python
def test_v2_manifest_is_stable_and_separate_from_v1(tmp_path):
    root = make_two_valid_v2_documents(tmp_path)
    manifest = build_manifest_v2(root, corpus_version="stage44b1-test")

    assert manifest["manifest_schema_version"] == 2
    assert manifest["chunk_count"] == 2
    assert manifest["corpus_version"] == "stage44b1-test"
    assert all("content_sha256" in item for item in manifest["chunks"])
    assert all("metadata_sha256" in item for item in manifest["chunks"])
    assert all("references" not in item for item in manifest["chunks"])


def test_v2_content_hash_covers_embedding_title_and_body(tmp_path):
    root = make_one_valid_v2_document(tmp_path)
    first = build_manifest_v2(root)
    replace_document_title(root, "新的中文标题")
    second = build_manifest_v2(root)

    assert first["chunks"][0]["content_sha256"] != second["chunks"][0]["content_sha256"]


def test_v2_loader_never_scans_v1_root(tmp_path):
    v1_root, v2_root = make_distinct_roots_with_same_id(tmp_path)
    manifest = build_manifest_v2(v2_root)
    chunks = build_chunks_v2(v2_root, manifest=manifest)

    assert len(chunks) == 1
    assert "中文正文" in chunks[0].content
    assert "English body" not in chunks[0].content
    assert "references" not in chunks[0].metadata
```

- [ ] **Step 2: 确认新模块缺失**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_manifest_v2.py tests/test_load_knowledge_v2.py -q
```

Expected: FAIL because v2 builder and loader do not exist.

- [ ] **Step 3: 实现 v2 manifest builder**

`scripts/build_knowledge_manifest_v2.py` 公开：

```python
KNOWLEDGE_V2_ROOT = Path("app/data/knowledge_v2")
DEFAULT_OUTPUT_PATH = KNOWLEDGE_V2_ROOT / "manifest.json"
DEFAULT_CORPUS_VERSION = "stage44b1-zh-v2"
```

实现 `build_manifest_v2(knowledge_root=KNOWLEDGE_V2_ROOT, *, corpus_version=DEFAULT_CORPUS_VERSION) -> dict` 和 `write_manifest_v2(*, output_path=DEFAULT_OUTPUT_PATH, knowledge_root=KNOWLEDGE_V2_ROOT, corpus_version=DEFAULT_CORPUS_VERSION) -> dict`。CLI 接受 `--knowledge-root`、`--output`、`--corpus-version`，成功时打印只含 corpus version、chunk count 和 manifest hash 的 JSON。

`content_sha256` 对规范化后的 `title + "\n" + body` 计算，确保实际嵌入输入变化时不会错误复用。`metadata_sha256` 对除 references 原文外的规范元数据及 references 的 title/url/source_kind/publisher 计算；manifest entry 只保存 `reference_count` 和 `references_sha256`，不复制 URL。corpus hash 对排序后的 manifest payload 计算。

- [ ] **Step 4: 实现只读 v2 根目录的 loader**

`scripts/load_knowledge_v2.py` 实现 `build_chunks_v2(knowledge_root=KNOWLEDGE_V2_ROOT, *, manifest: dict | None = None) -> list[KnowledgeChunk]` 和 `load_knowledge_v2(*, store=None, corpus_version: str, knowledge_root=KNOWLEDGE_V2_ROOT) -> IngestionSummary`。

runtime metadata 只包含 `source_path`、`content_kind`、`aliases`、`difficulty`、`question_patterns`、`content_sha256`、`metadata_sha256`、`corpus_manifest_sha256` 和 `corpus_version`；不得放入 references 或 URL。

- [ ] **Step 5: 运行 v1/v2 隔离测试并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_manifest.py tests/test_load_knowledge.py tests/test_knowledge_manifest_v2.py tests/test_load_knowledge_v2.py -q
git diff --check
git add scripts/build_knowledge_manifest_v2.py scripts/load_knowledge_v2.py tests/test_knowledge_manifest_v2.py tests/test_load_knowledge_v2.py
git commit -m "feat: build isolated stage 44b manifests"
```

### Task 3: 中文化运行时查询并扩展知识标签

**Files:**
- Modify: `app/services/job_tags.py`
- Modify: `app/services/knowledge_profile.py`
- Modify: `app/services/knowledge_query.py`
- Modify: `app/services/knowledge_grounding.py`
- Modify: `tests/test_job_tags.py`
- Modify: `tests/test_knowledge_profile.py`
- Modify: `tests/test_knowledge_query.py`
- Create: `tests/test_knowledge_grounding.py`

- [ ] **Step 1: 添加中文标签和查询失败测试**

新增断言：

```python
def test_chinese_jd_extracts_postgresql_and_reliability_tags():
    assert extract_job_tags("负责 PostgreSQL、可观测性、稳定性和容量规划") == [
        "postgresql",
        "reliability",
    ]


def test_query_text_uses_chinese_natural_language():
    profile = build_role_profile(
        "高级后端工程师，负责 PostgreSQL 和系统可靠性。",
        "参与 PostgreSQL 服务治理。",
    )
    queries = build_knowledge_queries(profile)

    assert [item.topic_id for item in queries] == [
        "topic-postgresql",
        "topic-reliability",
    ]
    assert queries[0].query_text == "后端工程师 | 高级 | PostgreSQL | 数据库 | 面试知识证据"
    assert "interview evidence" not in " ".join(item.query_text for item in queries)
```

更新既有 PostgreSQL uncovered 测试，使 PostgreSQL 在 v2 能产生查询。为 `knowledge_grounding.py` 增加断言，确保公开的 evidence summary 和 candidate summary 不再出现 `Retrieved`、`No trusted`、`provides evidence`。

- [ ] **Step 2: 运行测试并确认旧英文行为失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_job_tags.py tests/test_knowledge_profile.py tests/test_knowledge_query.py tests/test_knowledge_grounding.py -q
```

Expected: FAIL on missing reliability coverage and English query/summary text.

- [ ] **Step 3: 实现 canonical tag aliases 和中文查询模板**

`job_tags.py` 使用有序映射，把 `稳定性`、`可靠性`、`可观测性`、`容量规划` 映射为 `reliability`，把 PostgreSQL 技术名称映射为 `postgresql`。`knowledge_profile.py` 增加：

```python
CANONICAL_TAXONOMY["reliability"] = {"label": "可靠性", "domain": "可靠性"}
KNOWLEDGE_COVERED_TAGS.update({"postgresql", "reliability"})
```

`knowledge_query.py` 增加：

```python
QUERYABLE_TOPIC_TAGS = {
    "python", "fastapi", "redis", "mysql", "postgresql",
    "kafka", "system-design", "reliability",
}

QUERY_DOMAIN_LABELS = {
    "python": "后端开发",
    "fastapi": "后端开发",
    "redis": "缓存",
    "mysql": "数据库",
    "postgresql": "数据库",
    "kafka": "消息系统",
    "system-design": "系统设计",
    "reliability": "可靠性",
}
```

`_build_query_text()` 只组合中文角色、中文职级、taxonomy label、中文领域和“面试知识证据”。不得包含原始 JD、简历、邮箱、手机号或自由文本 role title。

- [ ] **Step 4: 中文化 grounding 中的用户可见摘要**

将 summary 模板改为：

```python
f"已为{label}找到 {len(evidence_ids)} 条可信知识证据。"
f"未找到可用于{label}的可信知识证据。"
f"{chunk.title} 提供了用于{chunk.domain}面试判断的{content_kind_label}证据。"
```

- [ ] **Step 5: 运行回归并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_job_tags.py tests/test_knowledge_profile.py tests/test_knowledge_query.py tests/test_knowledge_grounding.py tests/test_grounded_knowledge_agent.py -q
git diff --check
git add app/services/job_tags.py app/services/knowledge_profile.py app/services/knowledge_query.py app/services/knowledge_grounding.py tests/test_job_tags.py tests/test_knowledge_profile.py tests/test_knowledge_query.py tests/test_knowledge_grounding.py
git commit -m "feat: generate chinese knowledge queries"
```

### Task 4: 定义独立 v2 数据集模型与 12 条 Pilot

**Files:**
- Create: `app/services/knowledge_eval_dataset_v2.py`
- Create: `tests/test_knowledge_eval_dataset_v2.py`
- Create: `tests/golden/knowledge_retrieval_v2_pilot.json`

- [ ] **Step 1: 写独立模型失败测试**

覆盖：v1 模型字段不变；v2 无 `category`；`top_k` 固定为 5；六组 mapping；primary/accepted/excluded 互斥；所有 query 含中文；pilot 恰好 12 条且每组两条；ID 必须存在于 v2 manifest。

```python
def test_v2_case_has_independent_shape():
    case = KnowledgeRetrievalCaseV2(
        case_id="redis-consistency",
        evaluation_group="redis",
        query_text="缓存与数据库如何保持一致？",
        canonical_tags=["redis"],
        source_types=["theory"],
        allowed_domains=["redis"],
        primary_relevant_chunk_ids=["redis_consistency"],
        accepted_related_chunk_ids=["redis_operations"],
        excluded_chunk_ids=["redis_distributed_lock"],
    )
    assert case.top_k == 5
    assert "category" not in case.model_fields
```

- [ ] **Step 2: 运行测试并确认模块缺失**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_eval_dataset_v2.py -q
```

- [ ] **Step 3: 实现 v2 模型与 loader**

定义：

```python
EVALUATION_GROUP_DOMAIN_MAP = {
    "fastapi": {"python", "fastapi"},
    "redis": {"redis"},
    "relational-database": {"mysql", "postgresql"},
    "kafka": {"kafka"},
    "system-design": {"system-design"},
    "reliability": {"reliability", "system-design"},
}

class KnowledgeRetrievalCaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    evaluation_group: Literal["fastapi", "redis", "relational-database", "kafka", "system-design", "reliability"]
    query_text: str = Field(min_length=1, max_length=500)
    canonical_tags: list[str] = Field(min_length=1)
    source_types: list[Literal["theory", "engineering_guide", "expert_benchmark"]] = Field(min_length=1)
    allowed_domains: list[str] = Field(min_length=1)
    primary_relevant_chunk_ids: list[str] = Field(min_length=1)
    accepted_related_chunk_ids: list[str] = Field(default_factory=list)
    excluded_chunk_ids: list[str] = Field(default_factory=list)
    top_k: Literal[5] = 5

class KnowledgeRetrievalDatasetV2(BaseModel):
    version: str
    cases: list[KnowledgeRetrievalCaseV2]
```

实现 `load_knowledge_retrieval_dataset_v2(path, *, expected_case_count: int, manifest: dict) -> KnowledgeRetrievalDatasetV2`，在模型校验后检查数量、组分布和所有 referenced IDs。Task 4 先用冻结 v1 manifest 的 25 个稳定 ID 作为 fixture；Task 12 再用 committed v2 manifest 重跑同一校验。

- [ ] **Step 4: 写入 12 条中文 pilot**

使用以下 case/primary 组合，每组两条；accepted/excluded 必须从同一行指定的现有稳定 ID 中选择：

| group | case_id | 中文查询 | primary | accepted | excluded |
| --- | --- | --- | --- | --- | --- |
| fastapi | fastapi-request-lifecycle | FastAPI 请求从进入 ASGI 到响应返回经历哪些阶段？ | fastapi_request_lifecycle | fastapi_dependency_lifecycle | fastapi_blocking_io |
| fastapi | fastapi-blocking-io | 为什么异步接口中的同步阻塞调用会拖慢其他请求？ | fastapi_blocking_io | fastapi_production | fastapi_request_lifecycle |
| redis | redis-consistency | 缓存与数据库更新时怎样处理并发一致性窗口？ | redis_consistency | redis_operations | redis_distributed_lock |
| redis | redis-hot-key-breakdown | 热点缓存失效后怎样避免大量请求同时访问数据库？ | cache_breakdown | redis_operations | redis_consistency |
| relational-database | mysql-covering-index | 如何利用联合索引和覆盖索引减少回表与扫描行数？ | mysql_indexing | mysql_backend | mysql_isolation |
| relational-database | mysql-deadlock | InnoDB 死锁为什么发生，事务应如何重试？ | mysql_deadlocks | mysql_isolation | mysql_online_migration |
| kafka | kafka-delivery | Kafka 至少一次投递下如何避免重复副作用？ | kafka_delivery | kafka_backend | kafka_rebalancing |
| kafka | kafka-poison-message | 坏消息反复失败导致分区阻塞时应如何处理？ | kafka_poison_messages | kafka_operations | kafka_delivery |
| system-design | service-scaling | 无状态服务扩容时数据库、缓存和队列会出现哪些瓶颈？ | service_scaling | system_design_backend | capacity_planning |
| system-design | queue-backpressure | 消费速度低于生产速度时如何实施背压和准入控制？ | queue_backpressure | capacity_planning | cascading_failures |
| reliability | capacity-planning | 如何根据峰值流量、并发和增长量估算容量余量？ | capacity_planning | system_design_backend | service_scaling |
| reliability | cascading-failure | 下游变慢、重试放大和队列堆积如何形成级联故障？ | cascading_failures | queue_backpressure | service_scaling |

pilot JSON 顶层 `version` 固定为 `stage44b1-knowledge-retrieval-v2-pilot`，每条 `top_k` 固定为 5，六个组必须各有两条。

- [ ] **Step 5: 运行测试并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_eval_dataset.py tests/test_knowledge_eval_dataset_v2.py -q
git diff --check
git add app/services/knowledge_eval_dataset_v2.py tests/test_knowledge_eval_dataset_v2.py tests/golden/knowledge_retrieval_v2_pilot.json
git commit -m "test: define stage 44b pilot dataset"
```

### Task 5: 实现 v2 指标与独立评估器

**Files:**
- Create: `app/services/knowledge_eval_metrics_v2.py`
- Create: `scripts/evaluate_knowledge_retrieval_v2.py`
- Create: `tests/test_knowledge_eval_metrics_v2.py`
- Create: `tests/test_knowledge_eval_cli_v2.py`

- [ ] **Step 1: 写公式和失败闭合测试**

测试必须手工计算一个三用例 fixture 的 Recall@5、MRR@5 和 nDCG@5，并覆盖：missing observation、wrong domain、wrong source type、missing canonical tag、excluded ID、vector validity < 1、evidence replay missing、p95 > 1500。

```python
def test_v2_metrics_use_graded_relevance_and_top_five():
    metrics = calculate_knowledge_retrieval_metrics_v2(
        dataset=make_dataset(),
        observations=make_observations(),
        vector_validity_rate=1.0,
    )
    assert metrics.recall_at_5 == pytest.approx(expected_recall)
    assert metrics.mrr_at_5 == pytest.approx(expected_mrr)
    assert metrics.ndcg_at_5 == pytest.approx(expected_ndcg)
    assert metrics.filter_correctness_rate == 1.0
    assert metrics.excluded_chunk_violation_rate == 0.0
```

- [ ] **Step 2: 运行测试并确认模块缺失**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_eval_metrics_v2.py tests/test_knowledge_eval_cli_v2.py -q
```

- [ ] **Step 3: 实现 v2 observation 和指标模型**

公开模型：

```python
class RetrievedKnowledgeItemV2(BaseModel):
    chunk_id: str
    domain: str
    source_type: str
    tags: list[str]

class KnowledgeRetrievalObservationV2(BaseModel):
    case_id: str
    retrieved: list[RetrievedKnowledgeItemV2]
    bound_evidence_ids: list[str] = Field(default_factory=list)
    replayed_evidence_ids: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)

class KnowledgeRetrievalMetricsV2(BaseModel):
    passed: bool
    recall_at_5: float
    mrr_at_5: float
    ndcg_at_5: float
    filter_correctness_rate: float
    excluded_chunk_violation_rate: float
    vector_validity_rate: float
    evidence_replay_stability_rate: float
    observation_completeness_rate: float
    p95_latency_ms: float
    failed_gates: list[str]
```

DCG 使用 `sum(relevance / log2(rank + 1))`；IDCG 对 primary 的 3 分和 accepted 的 1 分降序排列取前 5。MRR 只把 primary 视为正确答案。filter correctness 要求每个返回项同时满足 allowed domain、source type 和至少一个 canonical tag。

- [ ] **Step 4: 实现独立 v2 evaluator 和安全输出**

`evaluate_knowledge_retrieval_v2()` 对每条 case 调用 `repository.search(case.query_text, job_tags=case.canonical_tags, source_types=case.source_types, limit=case.top_k)`，把返回 chunk 转成 `RetrievedKnowledgeItemV2`，绑定第一条返回证据并用 expected hash 回放。输出 case 仅包含 case_id、status、retrieved IDs、scores、bound/replayed IDs、latency；不序列化 query、chunk content、references 或 URL。

- [ ] **Step 5: 运行 v1/v2 回归并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_eval_metrics.py tests/test_knowledge_eval_cli.py tests/test_knowledge_eval_metrics_v2.py tests/test_knowledge_eval_cli_v2.py -q
git diff --check
git add app/services/knowledge_eval_metrics_v2.py scripts/evaluate_knowledge_retrieval_v2.py tests/test_knowledge_eval_metrics_v2.py tests/test_knowledge_eval_cli_v2.py
git commit -m "feat: evaluate stage 44b retrieval quality"
```

### Task 6: 建立 25 单元中文来源矩阵并人工批准

**Files:**
- Create: `docs/stage-44b1-chinese-source-matrix.md`

- [ ] **Step 1: 为 25 个稳定 ID 建立来源矩阵**

文档必须恰好包含 25 个 `chunk_id` 小节。每个小节包含一行 official source，或至少两行 secondary sources；每行字段为中文主题、来源标题、HTTPS URL、`official_cn|secondary_cn`、发布方、页面语言检查、论断一致性检查。不得填写根目录 URL，必须使用能直接支持该单元结论的具体页面。

优先来源池：

- FastAPI 中文官方文档：`https://fastapi.tiangolo.com/zh/`
- Python 中文官方文档：`https://docs.python.org/zh-cn/3/`
- 阿里云 Redis/MySQL/Kafka 中文产品文档：`https://help.aliyun.com/`
- 腾讯云 Redis/MySQL/Kafka 中文产品文档：`https://cloud.tencent.com/document/product/`
- 华为云 Redis/MySQL/Kafka 中文产品文档：`https://support.huaweicloud.com/`

FastAPI/Python 有中文官方页面时使用一个 official source；Redis、MySQL、Kafka、系统设计主题若无适用中文官方页面，必须选择两个不同发布方和不同 hostname 的中文二手来源。不得使用问答贴、无作者转载、SEO 聚合或仅有营销结论的页面。

- [ ] **Step 2: 人工检查来源页面**

逐行确认：页面主体是中文；标题与 URL 对应；页面能支持单元的核心结论和边界；两个 secondary 来源彼此独立；没有把机器翻译转载标为原创来源。检查结果写为 `PASS`，任何一项失败都先更换来源。

- [ ] **Step 3: 请求操作方审阅来源矩阵**

在开始 25 篇正文前暂停，请操作方审阅 `docs/stage-44b1-chinese-source-matrix.md`。只有明确批准后继续内容任务。

- [ ] **Step 4: 提交来源矩阵**

```powershell
git diff --check
git add docs/stage-44b1-chinese-source-matrix.md
git commit -m "docs: approve stage 44b1 chinese sources"
```

### Task 7: 编写 5 个 FastAPI 中文 v2 单元

**Files:**
- Create: `app/data/knowledge_v2/benchmarks/fastapi_backend.md`
- Create: `app/data/knowledge_v2/theory/fastapi_blocking_io.md`
- Create: `app/data/knowledge_v2/theory/fastapi_dependency_lifecycle.md`
- Create: `app/data/knowledge_v2/practices/fastapi_production.md`
- Create: `app/data/knowledge_v2/theory/fastapi_request_lifecycle.md`

- [ ] **Step 1: 按批准来源写完整 front matter 和中文正文**

| id | 中文标题 | difficulty | 核心边界 |
| --- | --- | --- | --- |
| fastapi_backend | FastAPI 后端项目评价基准 | advanced | 评价项目真实性、接口边界、测试、可观测性与稳定性，不用框架名代替工程证据 |
| fastapi_blocking_io | FastAPI 异步接口中的阻塞 I/O | intermediate | 同步调用阻塞 event loop 的条件、线程池边界、超时和测量方法 |
| fastapi_dependency_lifecycle | FastAPI 依赖项生命周期边界 | intermediate | request cache、yield cleanup、lifespan 资源与阻塞 I/O 归因的区别 |
| fastapi_production | FastAPI 生产工程实践 | intermediate | 进程模型、超时预算、健康检查、优雅关闭、压测和追踪 |
| fastapi_request_lifecycle | FastAPI 请求生命周期 | beginner | ASGI 入口、依赖解析、校验、handler、响应和清理顺序 |

每篇必须包含五个规定章节、2 至 5 条中文 question patterns、1 至 8 个 aliases 和来源矩阵中该 ID 的 references。正文不得复制来源原文。

- [ ] **Step 2: 验证 partial v2 corpus**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output tmp/stage44b1-fastapi-manifest.json --corpus-version stage44b1-draft
```

Expected: `chunk_count=5`; all five domain values are `fastapi`; schema/content/source checks PASS.

- [ ] **Step 3: 提交 FastAPI 批次**

```powershell
git diff --check
git add app/data/knowledge_v2/benchmarks/fastapi_backend.md app/data/knowledge_v2/theory/fastapi_blocking_io.md app/data/knowledge_v2/theory/fastapi_dependency_lifecycle.md app/data/knowledge_v2/practices/fastapi_production.md app/data/knowledge_v2/theory/fastapi_request_lifecycle.md
git commit -m "content: add chinese fastapi knowledge baseline"
```

### Task 8: 编写 5 个 Redis 中文 v2 单元

**Files:**
- Create: `app/data/knowledge_v2/theory/cache_breakdown.md`
- Create: `app/data/knowledge_v2/benchmarks/redis_backend.md`
- Create: `app/data/knowledge_v2/theory/redis_consistency.md`
- Create: `app/data/knowledge_v2/theory/redis_distributed_lock.md`
- Create: `app/data/knowledge_v2/practices/redis_operations.md`

- [ ] **Step 1: 编写 Redis 内容**

| id | 中文标题 | difficulty | 核心边界 |
| --- | --- | --- | --- |
| cache_breakdown | 热点缓存失效与缓存击穿 | intermediate | single-flight、互斥重建、逻辑过期、数据库保护与热点识别 |
| redis_backend | Redis 后端项目评价基准 | advanced | 数据结构选择、一致性、容量、故障演练和可观测证据 |
| redis_consistency | Redis 缓存一致性的边界 | beginner | cache-aside 更新顺序、并发窗口、删除失败补偿和降级 |
| redis_distributed_lock | Redis 分布式锁的安全边界 | intermediate | owner token、原子释放、租约、续期、fencing token 与业务幂等 |
| redis_operations | Redis 生产运维观察点 | intermediate | 命中率、淘汰、blocked clients、复制延迟、内存碎片和恢复 |

没有中文官方资料的单元必须写入来源矩阵批准的两个独立 secondary references。

- [ ] **Step 2: 验证 10 单元 partial corpus**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output tmp/stage44b1-redis-manifest.json --corpus-version stage44b1-draft
```

Expected: `chunk_count=10`; Redis 5 and FastAPI 5.

- [ ] **Step 3: 提交 Redis 批次**

```powershell
git diff --check
git add app/data/knowledge_v2/theory/cache_breakdown.md app/data/knowledge_v2/benchmarks/redis_backend.md app/data/knowledge_v2/theory/redis_consistency.md app/data/knowledge_v2/theory/redis_distributed_lock.md app/data/knowledge_v2/practices/redis_operations.md
git commit -m "content: add chinese redis knowledge baseline"
```

### Task 9: 编写 5 个 MySQL 中文 v2 单元

**Files:**
- Create: `app/data/knowledge_v2/benchmarks/mysql_backend.md`
- Create: `app/data/knowledge_v2/theory/mysql_deadlocks.md`
- Create: `app/data/knowledge_v2/theory/mysql_indexing.md`
- Create: `app/data/knowledge_v2/theory/mysql_isolation.md`
- Create: `app/data/knowledge_v2/practices/mysql_online_migration.md`

- [ ] **Step 1: 编写 MySQL 内容**

| id | 中文标题 | difficulty | 核心边界 |
| --- | --- | --- | --- |
| mysql_backend | MySQL 后端项目评价基准 | advanced | schema、索引、事务边界、迁移、备份恢复和量化证据 |
| mysql_deadlocks | InnoDB 死锁识别与恢复 | intermediate | 等待环、锁顺序、短事务、错误重试与幂等 |
| mysql_indexing | MySQL 联合索引与覆盖索引 | beginner | 最左前缀、选择性、回表、EXPLAIN 与写放大 |
| mysql_isolation | MySQL 事务隔离与锁边界 | intermediate | snapshot、当前读、next-key lock、RC/RR 差异和异常 |
| mysql_online_migration | MySQL 在线表结构迁移 | intermediate | 双向兼容、回填、复制延迟、节流、校验和回滚 |

- [ ] **Step 2: 验证 15 单元 partial corpus**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output tmp/stage44b1-mysql-manifest.json --corpus-version stage44b1-draft
```

Expected: `chunk_count=15`; MySQL 5.

- [ ] **Step 3: 提交 MySQL 批次**

```powershell
git diff --check
git add app/data/knowledge_v2/benchmarks/mysql_backend.md app/data/knowledge_v2/theory/mysql_deadlocks.md app/data/knowledge_v2/theory/mysql_indexing.md app/data/knowledge_v2/theory/mysql_isolation.md app/data/knowledge_v2/practices/mysql_online_migration.md
git commit -m "content: add chinese mysql knowledge baseline"
```

### Task 10: 编写 5 个 Kafka 中文 v2 单元

**Files:**
- Create: `app/data/knowledge_v2/benchmarks/kafka_backend.md`
- Create: `app/data/knowledge_v2/theory/kafka_delivery.md`
- Create: `app/data/knowledge_v2/practices/kafka_operations.md`
- Create: `app/data/knowledge_v2/theory/kafka_poison_messages.md`
- Create: `app/data/knowledge_v2/theory/kafka_rebalancing.md`

- [ ] **Step 1: 编写 Kafka 内容**

| id | 中文标题 | difficulty | 核心边界 |
| --- | --- | --- | --- |
| kafka_backend | Kafka 后端项目评价基准 | advanced | 分区设计、顺序、投递语义、消费治理、容量和故障演练 |
| kafka_delivery | Kafka 投递语义与幂等副作用 | beginner | at-least-once、offset 提交、幂等键、事务和重复处理 |
| kafka_operations | Kafka 消费运行指标 | intermediate | lag、drain time、rebalance、失败率、retry volume 和扩容 |
| kafka_poison_messages | Kafka 坏消息与重试隔离 | intermediate | 有界重试、DLQ、错误分类、回放审计和分区推进 |
| kafka_rebalancing | Kafka 消费组再平衡边界 | intermediate | poll、session、partition revoke/assign、offset 与 cooperative rebalance |

- [ ] **Step 2: 验证 20 单元 partial corpus**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output tmp/stage44b1-kafka-manifest.json --corpus-version stage44b1-draft
```

Expected: `chunk_count=20`; Kafka 5.

- [ ] **Step 3: 提交 Kafka 批次**

```powershell
git diff --check
git add app/data/knowledge_v2/benchmarks/kafka_backend.md app/data/knowledge_v2/theory/kafka_delivery.md app/data/knowledge_v2/practices/kafka_operations.md app/data/knowledge_v2/theory/kafka_poison_messages.md app/data/knowledge_v2/theory/kafka_rebalancing.md
git commit -m "content: add chinese kafka knowledge baseline"
```

### Task 11: 编写 5 个系统设计中文 v2 单元

**Files:**
- Create: `app/data/knowledge_v2/practices/capacity_planning.md`
- Create: `app/data/knowledge_v2/theory/cascading_failures.md`
- Create: `app/data/knowledge_v2/theory/queue_backpressure.md`
- Create: `app/data/knowledge_v2/theory/service_scaling.md`
- Create: `app/data/knowledge_v2/benchmarks/system_design_backend.md`

- [ ] **Step 1: 编写系统设计内容**

| id | 中文标题 | difficulty | 核心边界 |
| --- | --- | --- | --- |
| capacity_planning | 服务容量规划方法 | intermediate | 峰值 QPS、并发、服务时间、存储增长、余量和压测校准 |
| cascading_failures | 服务级联故障与放大链路 | intermediate | 慢依赖、重试风暴、队列饱和、熔断、隔离和负载保护 |
| queue_backpressure | 队列背压与准入控制 | intermediate | 到达率、服务率、backlog age、限流、丢弃和降级 |
| service_scaling | 无状态服务扩容边界 | beginner | 共享状态、数据库、缓存、队列、连接池和热点瓶颈 |
| system_design_backend | 后端系统设计评价基准 | advanced | 需求澄清、容量、数据流、故障模型、演进和验证证据 |

`capacity_planning`、`cascading_failures`、`queue_backpressure` 的 tags 同时包含 `system-design` 和 `reliability`，使 44B1 的 reliability 运行时查询能命中真实边界内容；它们的 domain 仍保持 `system-design`。

- [ ] **Step 2: 验证完整 25 单元 v2 corpus**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output tmp/stage44b1-system-manifest.json --corpus-version stage44b1-draft
```

Expected: `chunk_count=25`; five current domains each exactly 5; every body and reference passes.

- [ ] **Step 3: 提交系统设计批次**

```powershell
git diff --check
git add app/data/knowledge_v2/practices/capacity_planning.md app/data/knowledge_v2/theory/cascading_failures.md app/data/knowledge_v2/theory/queue_backpressure.md app/data/knowledge_v2/theory/service_scaling.md app/data/knowledge_v2/benchmarks/system_design_backend.md
git commit -m "content: add chinese system design knowledge baseline"
```

### Task 12: 封存 25 单元 v2 Manifest 与跨版本合约

**Files:**
- Create: `app/data/knowledge_v2/manifest.json`
- Create: `tests/test_stage44b1_corpus.py`

- [ ] **Step 1: 写 repository corpus 失败测试**

```python
import json
from pathlib import Path

from scripts.build_knowledge_manifest_v2 import build_manifest_v2
from scripts.load_knowledge_v2 import build_chunks_v2


def test_stage44b1_corpus_has_same_ids_and_new_content_hashes():
    v1 = json.loads(Path("app/data/knowledge/manifest.json").read_text(encoding="utf-8"))
    v2 = build_manifest_v2(corpus_version="stage44b1-zh-v2")

    v1_hashes = {item["chunk_id"]: item["content_sha256"] for item in v1["chunks"]}
    v2_hashes = {item["chunk_id"]: item["content_sha256"] for item in v2["chunks"]}
    assert v2["manifest_schema_version"] == 2
    assert v2["chunk_count"] == 25
    assert set(v2_hashes) == set(v1_hashes)
    assert all(v2_hashes[chunk_id] != v1_hashes[chunk_id] for chunk_id in v1_hashes)


def test_stage44b1_runtime_metadata_excludes_references_and_urls():
    chunks = build_chunks_v2()
    serialized = "\n".join(chunk.model_dump_json() for chunk in chunks)
    assert "references" not in serialized
    assert "https://" not in serialized
    assert all(chunk.metadata["aliases"] for chunk in chunks)


def test_frozen_v1_manifest_remains_reproducible():
    committed = json.loads(Path("app/data/knowledge/manifest.json").read_text(encoding="utf-8"))
    assert committed["corpus_manifest_sha256"] == "44f2eba0bfb87e99cfd4bfb4834d2ed8e5f97b79eb51e0a46782decb075a8beb"
```

再断言 difficulty 总体分布在批准范围内、每个当前 domain 各 5 个、五种 content_kind 各 5 个、pilot 的所有 ID 都存在。

- [ ] **Step 2: 运行测试并确认 manifest 尚未提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage44b1_corpus.py -q
```

Expected: FAIL because committed v2 manifest is missing or repository contract is not yet wired.

- [ ] **Step 3: 生成并审查 committed manifest**

```powershell
& 'F:\python3.11\python.exe' -m scripts.build_knowledge_manifest_v2 --knowledge-root app/data/knowledge_v2 --output app/data/knowledge_v2/manifest.json --corpus-version stage44b1-zh-v2
```

确认 manifest 不包含 reference URL、正文或来源页面内容，只包含安全元数据、hash 和 reference count/hash。

- [ ] **Step 4: 运行 v1/v2 完整 corpus tests 并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_manifest.py tests/test_load_knowledge.py tests/test_knowledge_manifest_v2.py tests/test_load_knowledge_v2.py tests/test_stage44b1_corpus.py tests/test_knowledge_eval_dataset_v2.py -q
git diff --check
git add app/data/knowledge_v2/manifest.json tests/test_stage44b1_corpus.py
git commit -m "content: seal stage 44b1 chinese corpus"
```

### Task 13: 添加 44B1 Acceptance Runner 与隐私审计

**Files:**
- Create: `scripts/run_stage44b1_acceptance.py`
- Create: `scripts/audit_stage44b1_artifacts.py`
- Create: `tests/test_stage44b1_acceptance.py`
- Create: `tests/test_stage44b1_artifact_audit.py`

- [ ] **Step 1: 写 dependency-injected runner 失败测试**

测试 fake provider/repository/ingestor，至少实现四个完整测试：`test_stage44b1_runner_requires_25_v2_chunks_and_12_pilot_cases`、`test_stage44b1_runner_records_first_run_as_25_embedded`、`test_stage44b1_runner_accepts_idempotent_25_reused`、`test_stage44b1_artifacts_never_include_queries_content_or_sources`。第二个测试的核心断言为：

```python
def test_stage44b1_runner_records_first_run_as_25_embedded(tmp_path):
    metrics = run_stage44b1_acceptance(
        repository=repository,
        ingestor=ingestor,
        chunks=chunks,
        manifest=manifest,
        pilot_dataset=pilot_dataset,
        v1_dataset=v1_dataset,
        run_id="stage44b1-test",
        run_dir=tmp_path / "stage44b1-test",
    )
    assert metrics["chunk_count"] == 25
    assert metrics["ingestion"]["embedded"] == 25
    assert metrics["ingestion"]["reused"] == 0
    assert metrics["pilot_metrics"]["observation_completeness_rate"] == 1.0
    assert metrics["v1_metrics"]["passed"] is True
```

idempotent 测试让 fake ingestor 返回 `embedded=0`、`reused=25`，仍应通过。runner 的数量门禁是 `embedded + reused == 25` 且 `activated == 25`；不得强制每次重跑都重新调用 provider。

runner 必须先 ingest，再运行 pilot v2，再运行 frozen v1；identity mismatch、任何 degraded/incomplete case、pilot gate 失败或 v1 gate 失败都使结果失败。

- [ ] **Step 2: 写 v2 专属隐私审计失败测试**

复用 Stage 44A whitelist、hash inventory、递归 blocked-key 和敏感模式。新增拒绝键：`url`、`references`、`source_url`、`question_patterns`、`query_text`、`content`。测试 unexpected file、changed file、nonpassing metrics、API key、DSN、absolute path、邮箱、手机号。

- [ ] **Step 3: 运行测试并确认模块缺失**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage44b1_acceptance.py tests/test_stage44b1_artifact_audit.py -q
```

- [ ] **Step 4: 实现安全 acceptance runner**

`scripts/run_stage44b1_acceptance.py` 固定：

```python
CORPUS_VERSION = "stage44b1-zh-v2"
PILOT_VERSION = "stage44b1-knowledge-retrieval-v2-pilot"
V1_VERSION = "stage42-knowledge-retrieval-v1"
```

实现 `run_stage44b1_acceptance(*, repository, ingestor, chunks, manifest, pilot_dataset, v1_dataset, run_id: str, run_dir: Path | str) -> dict`。

metrics.json 只保存 provider/model/revision/dimension、corpus/manifest/dataset hashes、25 count、ingestion counts、pilot aggregate、v1 aggregate、provider safe metrics、exact scan 标签和失败码。每个 retrieval case 文件只保存 safe case_id、hit IDs、scores、bound/replayed IDs、latency 和 status。

CLI 必须在构建 real dependency 前检查：

```python
RUN_SILICONFLOW_ACCEPTANCE == "1"
EMBEDDING_PROVIDER == "siliconflow"
EMBEDDING_MODEL_REVISION != "siliconflow-current"
PGVECTOR_TABLE == "knowledge_chunks_stage44b_rc"
```

- [ ] **Step 5: 实现 Stage 44B1 auditor**

先调用 Stage 44A 审计所使用的 inventory/sensitive/blocked-key 规则，再应用 v2 extra blocked keys。不要把查询或来源 URL 读入错误消息；只返回稳定的相对路径和 violation code。

- [ ] **Step 6: 运行 acceptance 单元测试和 Stage 44A 回归并提交**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage44b1_acceptance.py tests/test_stage44b1_artifact_audit.py tests/test_stage44a_acceptance.py tests/test_stage44a_artifact_audit.py -q
git diff --check
git add scripts/run_stage44b1_acceptance.py scripts/audit_stage44b1_artifacts.py tests/test_stage44b1_acceptance.py tests/test_stage44b1_artifact_audit.py
git commit -m "test: add stage 44b1 chinese corpus acceptance"
```

### Task 14: 文档、完整门禁与真实 44B1 RC 验收

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/stage-44b1-chinese-corpus-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`
- Modify only if a defect is found: files owned by Tasks 1-13

- [ ] **Step 1: 创建 PENDING 验收记录并更新运行文档**

文档说明 v1/v2 根目录隔离、中文运行时查询、仅中文来源、持久 RC 前缀、生产不自动切换。清洁 RC 首次运行预期 embedded=25/reused=0；重跑允许 embedded=0/reused=25。验收记录先写 `Status: PENDING`，不得预先标 PASS。

- [ ] **Step 2: 运行 deterministic 和 PostgreSQL gates**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_corpus_schema.py tests/test_knowledge_manifest_v2.py tests/test_load_knowledge_v2.py tests/test_knowledge_eval_dataset_v2.py tests/test_knowledge_eval_metrics_v2.py tests/test_knowledge_eval_cli_v2.py tests/test_stage44b1_corpus.py tests/test_stage44b1_acceptance.py tests/test_stage44b1_artifact_audit.py -q
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector
```

Expected: all focused and 9 pgvector tests PASS using isolated random test tables.

- [ ] **Step 3: 安全配置真实 RC 环境**

只在当前 PowerShell 进程通过安全机制设置新密钥，不写 `.env`、命令历史、日志或文档。非秘密配置：

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
$env:EMBEDDING_MODEL_NAME='BAAI/bge-m3'
$env:EMBEDDING_MODEL_REVISION='siliconflow-bge-m3-20260721'
$env:EMBEDDING_DIMENSION='1024'
$env:RUN_SILICONFLOW_ACCEPTANCE='1'
$env:PGVECTOR_TABLE='knowledge_chunks_stage44b_rc'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
```

- [ ] **Step 4: 运行真实 25 单元、pilot 和 v1 验收**

```powershell
$runId=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-stage44b1-zh'
& 'F:\python3.11\python.exe' -m scripts.run_stage44b1_acceptance --run-id $runId --run-dir "reports/stage44b1-acceptance/$runId"
& 'F:\python3.11\python.exe' -m scripts.audit_stage44b1_artifacts --run-id $runId --run-dir "reports/stage44b1-acceptance/$runId"
```

Expected: 25 discovered/activated 且 embedded+reused=25；清洁 RC 首次运行通常 embedded=25/reused=0，幂等重跑可 reused=25。12/12 pilot observations complete，pilot v2 metrics、冻结 30-case v1 metrics 和 privacy audit 全部 PASS。

- [ ] **Step 5: 运行完整回归**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -q
npm.cmd run build:prototype-css
Get-ChildItem app/static/*.js | ForEach-Object { node --check $_.FullName }
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
& 'F:\python3.11\python.exe' -m scripts.audit_stage42_artifacts --run-dir reports/stage42-acceptance/20260716T062331Z-real-model-rc --run-id 20260716T062331Z-real-model-rc
& 'F:\python3.11\python.exe' -m scripts.audit_stage44a_artifacts --run-dir reports/stage44a-acceptance/20260722T054127Z-stage44a-bge-m3 --run-id 20260722T054127Z-stage44a-bge-m3
git diff --check
```

若 Windows Playwright webServer 无法退出，创建 gitignored `tmp/playwright-stage44b1.config.js`：

```javascript
process.env.STAGE41_PYTHON = "F:\\python3.11\\python.exe";
const config = require("../playwright.config.js");
module.exports = Object.assign({}, config, {
  testDir: "../tests/browser",
  webServer: undefined,
});
```

然后用隐藏的受控 uvicorn 启动 `tests.browser_support_app:app` 到 8011，运行 `npm.cmd run test:browser -- --config=tmp/playwright-stage44b1.config.js`，并在 PowerShell `finally` 中停止服务器和删除临时 trace 目录。

- [ ] **Step 6: 清洁环境证明**

从新的 lock 安装清洁环境：

```powershell
& 'F:\python3.11\python.exe' -m venv tmp/stage44b1-clean-env
& 'tmp\stage44b1-clean-env\Scripts\python.exe' -m pip install --require-hashes -r requirements.lock.txt
& 'tmp\stage44b1-clean-env\Scripts\python.exe' -m pip check
& 'tmp\stage44b1-clean-env\Scripts\python.exe' -c "import importlib.util; assert importlib.util.find_spec('sentence_transformers') is None; assert importlib.util.find_spec('langchain_huggingface') is None"
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'tmp\stage44b1-clean-env\Scripts\python.exe' -m pytest tests/test_vector_store.py::test_runtime_has_no_local_embedding_dependency tests/test_vector_store.py::test_disabled_provider_constructs_without_adapter_import_or_model_cache tests/test_vector_store_pgvector.py::test_historical_lookup_uses_expected_hash_and_never_embeds -q
```

Expected: `pip check` clean，两个 local model 包不可导入，3 个 focused tests PASS。

- [ ] **Step 7: 更新 PASS 记录并提交**

记录 run ID、commit、v1/v2 manifest hashes、pilot dataset hash、provider/model revision、实际 embedded/reused 数量及两者合计 25、pilot/v1 指标、provider p50/p95、retrieval p95、artifact 相对路径、测试计数和零隐私违规。不得记录 API key、DSN、query、content、source URL 或绝对路径。

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py docs/stage-44b1-chinese-corpus-acceptance.md
git commit -m "docs: record stage 44b1 chinese corpus acceptance"
```

44B2 只有在此验收记录为 PASS 后才能开始实施。
