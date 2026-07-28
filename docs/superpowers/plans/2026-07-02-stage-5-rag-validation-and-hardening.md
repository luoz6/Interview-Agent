# 阶段五：真实 RAG 运行验证与质量固化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在真实 PostgreSQL + pgvector 环境下打通知识导入、检索、评估与前端证据展示，并用 20+ Golden Dataset 固化评分与解释质量。

**Architecture:** 本阶段不扩展新的系统边界，只把当前阶段四已经接入的 `PgVectorKnowledgeStore + ExpertShadowEvaluator + BackgroundTasks` 链路做成“真实可运行、可回归、可解释”的稳定闭环。后端继续保留内存 `InterviewSessionStore`，重点增强 `scripts/load_knowledge.py` 的可运行性、`pgvector` 的真实 round-trip 验证、前端失败态/证据不足提示，以及参数化 Golden Dataset 回归测试。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、PostgreSQL、pgvector、psycopg2-binary、sentence-transformers、pytest、原生 HTML/CSS/JavaScript。

---

## 文件结构

- 修改：`scripts/load_knowledge.py`
  - 让导入脚本支持注入 store、返回结构化 summary、输出明确运行结果
- 新增：`tests/test_load_knowledge.py`
  - 覆盖知识切片构建、领域识别、导入脚本调用链
- 新增：`tests/test_vector_store_pgvector.py`
  - 真实 PostgreSQL + pgvector round-trip 集成测试
- 修改：`app/static/app.js`
  - 渲染 retrieval unavailable 与 evidence insufficient
- 修改：`app/static/styles.css`
  - 增加 references 空态和 retrieval failure 样式
- 修改：`tests/test_static_report_ui.py`
  - 锁定前端空引用/检索失败文本
- 修改：`tests/test_report_tasks.py`
  - 增加 retrieval infrastructure failure 断言
- 视情况修改：`tests/test_report_api.py`
  - 锁定 API 在 retrieval unavailable 场景下的 500 行为
- 修改：`tests/test_golden_dataset.py`
  - 改成读取多个 JSON 文件、参数化运行 20+ case、校验 score band 和解释质量
- 新增：`tests/golden/redis_cases.json`
- 新增：`tests/golden/mysql_cases.json`
- 新增：`tests/golden/kafka_cases.json`
- 新增：`tests/golden/system_design_cases.json`
- 删除：`tests/golden/redis_strong_answer.json`
- 删除：`tests/golden/redis_weak_answer.json`
- 新增：`app/data/knowledge/benchmarks/fastapi_backend.md`
- 新增：`app/data/knowledge/benchmarks/mysql_backend.md`
- 新增：`app/data/knowledge/benchmarks/kafka_backend.md`
- 新增：`app/data/knowledge/benchmarks/system_design_backend.md`
- 新增：`app/data/knowledge/theory/cache_breakdown.md`
- 新增：`app/data/knowledge/theory/mysql_indexing.md`
- 新增：`app/data/knowledge/theory/kafka_delivery.md`
- 新增：`app/data/knowledge/theory/service_scaling.md`
- 视情况修改：`app/services/vector_store.py`
  - 若 Task 2 实施时确认 `_ensure_schema()` 在每次 `search()` / `upsert_chunks()` 都重复执行，可把 schema 初始化前移到 `from_env()` / `get_knowledge_store()` / 带 guard 的构造路径；这是非阻塞优化，不强制纳入主线 diff
- 视情况修改：`requirements.txt`
  - 仅在依赖版本缺口暴露时补齐，不主动引入新的外部系统依赖

统一测试命令：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

带数据库标记的可选集成测试命令：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q -m pgvector
```

真实导入验证命令：

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

---

### Task 1: 加固知识导入脚本的可测试性与运行输出

**Files:**
- Modify: `scripts/load_knowledge.py`
- Create: `tests/test_load_knowledge.py`

- [ ] **Step 1: 先写失败测试，锁定导入脚本 summary 与 upsert 调用**

创建 `tests/test_load_knowledge.py`：

```python
import scripts.load_knowledge as load_knowledge


class FakeStore:
    def __init__(self):
        self.received = None

    def upsert_chunks(self, chunks):
        self.received = list(chunks)


def test_build_chunks_marks_sources_and_general_tag():
    chunks = load_knowledge.build_chunks()
    by_id = {chunk.chunk_id: chunk for chunk in chunks}

    assert by_id["redis_backend"].source_type == "expert_benchmark"
    assert by_id["redis_consistency"].source_type == "theory"
    assert "general" in by_id["redis_backend"].tags


def test_load_knowledge_upserts_all_discovered_chunks_and_returns_summary():
    fake_store = FakeStore()

    summary = load_knowledge.load_knowledge(store=fake_store)

    assert summary["discovered"] == len(fake_store.received)
    assert summary["upserted"] == len(fake_store.received)
    assert {"redis_backend", "redis_consistency"}.issubset(
        {chunk.chunk_id for chunk in fake_store.received}
    )
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_load_knowledge.py -q
```

Expected: FAIL，提示 `load_knowledge()` 不接受 `store` 参数，或返回值不是 summary dict。

- [ ] **Step 3: 实现最小重构，让脚本可注入 store 并返回 summary**

把 `scripts/load_knowledge.py` 改成下面内容：

```python
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore, get_knowledge_store


KNOWLEDGE_ROOT = Path("app/data/knowledge")


def iter_markdown_files() -> list[Path]:
    return sorted(KNOWLEDGE_ROOT.rglob("*.md"))


def build_chunks() -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in iter_markdown_files():
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        domain = infer_domain(path, content)
        source_type = "expert_benchmark" if "benchmarks" in path.parts else "theory"
        tags = [domain] if domain == "general" else [domain, "general"]
        chunks.append(
            KnowledgeChunk(
                chunk_id=path.stem,
                title=path.stem.replace("_", " ").title(),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=tags,
                metadata={"source_path": str(path)},
            )
        )
    return chunks


def infer_domain(path: Path, content: str) -> str:
    text = f"{path.stem}\n{content}".lower()
    if "redis" in text:
        return "redis"
    if "fastapi" in text:
        return "fastapi"
    if "mysql" in text:
        return "mysql"
    if "kafka" in text:
        return "kafka"
    if "system design" in text or "service scaling" in text or "scaling" in text:
        return "system-design"
    return "general"


def load_knowledge(
    store: PgVectorKnowledgeStore | None = None,
) -> dict[str, int]:
    chunks = build_chunks()
    active_store = store or _resolve_store()
    active_store.upsert_chunks(chunks)
    return {"discovered": len(chunks), "upserted": len(chunks)}


def _resolve_store() -> PgVectorKnowledgeStore:
    try:
        return get_knowledge_store()
    except KeyError as exc:
        raise RuntimeError("POSTGRES_DSN is required to load knowledge into pgvector") from exc


def main() -> int:
    summary = load_knowledge()
    print(
        f"Discovered {summary['discovered']} knowledge chunks and upserted "
        f"{summary['upserted']} rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行聚焦测试，确认通过**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_load_knowledge.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add scripts/load_knowledge.py tests/test_load_knowledge.py
git commit -m "feat: harden knowledge loader summary and injection"
```

---

### Task 2: 增加真实 pgvector round-trip 集成测试

**Files:**
- Create: `tests/test_vector_store_pgvector.py`

实现注意：
- round-trip 断言只锁定首个召回结果的 `chunk_id`，不要把完整结果列表形状写死
- 如果在实现或验测中确认 `_ensure_schema()` 每次调用 `search()` / `upsert_chunks()` 都触发一轮 DDL，可在这个任务收尾时顺手把初始化收敛到 store 初始化路径；这不是本任务的阻塞项

- [ ] **Step 1: 先写真实 pgvector 集成测试**

创建 `tests/test_vector_store_pgvector.py`：

```python
import os
import uuid

import pytest

from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore
from tests.test_vector_store import FakeEmbeddingModel


def make_chunk(
    chunk_id: str,
    *,
    title: str,
    content: str,
    source_type: str,
    domain: str,
    tags: list[str],
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        source_type=source_type,
        domain=domain,
        tags=tags,
        metadata={"source": "pgvector-test"},
    )


@pytest.mark.pgvector
def test_pgvector_roundtrip_filters_by_tag_and_source_type():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is not configured")

    table_name = f"knowledge_chunks_{uuid.uuid4().hex[:8]}"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name=table_name,
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=3,
        embedding_model=FakeEmbeddingModel(),
    )

    redis_chunk = make_chunk(
        "redis-1",
        title="Redis cache consistency",
        content="Delete cache after database writes and handle race conditions.",
        source_type="theory",
        domain="redis",
        tags=["redis", "general"],
    )
    mysql_chunk = make_chunk(
        "mysql-1",
        title="MySQL indexing",
        content="Use covering indexes for read-heavy queries.",
        source_type="theory",
        domain="mysql",
        tags=["mysql", "general"],
    )

    try:
        store.upsert_chunks([redis_chunk, mysql_chunk])
        store.upsert_chunks([redis_chunk])

        results = store.search(
            "Redis cache invalidation",
            job_tags=["redis"],
            source_types=["theory"],
            limit=5,
        )

        assert results
        assert results[0].chunk_id == "redis-1"
        assert results[0].score is not None
    finally:
        psycopg2, _ = PgVectorKnowledgeStore._import_psycopg2()
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
```

- [ ] **Step 2: 运行 pgvector 集成测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector
```

Expected:
- 未配置 `POSTGRES_DSN`：SKIPPED
- 已配置 `POSTGRES_DSN` 且链路有问题：FAIL
- 已配置且链路正确：PASS

- [ ] **Step 3: 用真实环境运行导入脚本**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

Expected: 输出 `Discovered X knowledge chunks and upserted X rows.`

- [ ] **Step 4: 提交**

```powershell
git add tests/test_vector_store_pgvector.py
git commit -m "test: add pgvector roundtrip integration coverage"
```

---

### Task 3: 补齐 retrieval unavailable 与 evidence insufficient 的后端/前端行为

**Files:**
- Modify: `tests/test_report_tasks.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_static_report_ui.py`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`

- [ ] **Step 1: 先写失败测试，锁定 retrieval failure 与空引用提示**

把 `tests/test_report_tasks.py` 追加为：

```python
def test_generate_report_for_session_marks_failed_when_vector_store_is_unavailable():
    class FailingVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            raise RuntimeError("db down")

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FailingVectorStore()
    store = InterviewSessionStore(llm=ReportLLM(report_score=81))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "pgvector knowledge store is unavailable"
```

把 `tests/test_report_api.py` 追加为：

```python
def test_report_endpoint_returns_retrieval_error_when_knowledge_store_is_unavailable():
    class FailingVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            raise RuntimeError("db down")

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FailingVectorStore()
    client, _, _ = make_client()
    session_id = start_interview(client)

    client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I built a Redis-backed service."},
    )
    client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used cache-aside and database fallback."},
    )

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "pgvector knowledge store is unavailable"
```

这里继续直接调用 `generate_report_for_session()`，只验证任务函数本身，不在单元测试里模拟 `BackgroundTasks` 生命周期；真实 `/answer -> BackgroundTasks -> /report` 链路放到 Task 6 Step 5 人工验收。

在 `tests/test_static_report_ui.py` 里追加一个独立测试函数，不改现有 `test_app_js_reads_progress_fields`：

```python
def test_app_js_surfaces_reference_and_retrieval_states():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "feedback.references" in js
    assert "No strong reference found for this answer." in js
    assert "Knowledge retrieval unavailable" in js
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py tests/test_report_api.py tests/test_static_report_ui.py -q
```

Expected: 至少前端静态测试 FAIL，因为当前还没有 retrieval unavailable 和空引用用户文案。

- [ ] **Step 3: 实现前端失败态与空引用提示**

把 `app/static/app.js` 的相关部分改成：

```javascript
function toUserFacingReportError(message) {
  if (message && message.includes("pgvector knowledge store is unavailable")) {
    return "Knowledge retrieval unavailable";
  }
  return message || "Report generation failed";
}

function renderReportError(message) {
  reportSection.hidden = false;
  reportSection.className = "report-section failed";
  reportStatus.textContent = "Report generation failed";
  reportContent.innerHTML = "";
  reportContent.appendChild(
    createEl("p", "report-note", toUserFacingReportError(message))
  );
}
```

并把 `renderFeedback(feedback)` 的尾部改成：

```javascript
  if (Array.isArray(feedback.references) && feedback.references.length > 0) {
    const references = createEl("div", "feedback-references");
    references.appendChild(createEl("p", "report-label", "References"));
    feedback.references.forEach((reference) => {
      const refItem = createEl("div", "reference-item");
      refItem.appendChild(
        createEl(
          "p",
          "reference-title",
          `${reference.title} (${reference.source_type})`
        )
      );
      refItem.appendChild(createEl("p", "reference-excerpt", reference.excerpt));
      references.appendChild(refItem);
    });
    item.appendChild(references);
  } else {
    item.appendChild(
      createEl(
        "p",
        "reference-empty",
        "No strong reference found for this answer."
      )
    );
  }
```

把 `app/static/styles.css` 追加：

```css
.reference-empty {
  color: var(--muted);
  margin: 12px 0 0;
  font-style: italic;
}
```

- [ ] **Step 4: 运行聚焦测试，确认通过**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py tests/test_report_api.py tests/test_static_report_ui.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/static/app.js app/static/styles.css tests/test_report_tasks.py tests/test_report_api.py tests/test_static_report_ui.py
git commit -m "feat: surface retrieval failures and evidence gaps"
```

---

### Task 4: 扩充知识库资产，并让导入脚本识别核心领域

**Files:**
- Modify: `scripts/load_knowledge.py`
- Modify: `tests/test_load_knowledge.py`
- Create: `app/data/knowledge/benchmarks/fastapi_backend.md`
- Create: `app/data/knowledge/benchmarks/mysql_backend.md`
- Create: `app/data/knowledge/benchmarks/kafka_backend.md`
- Create: `app/data/knowledge/benchmarks/system_design_backend.md`
- Create: `app/data/knowledge/theory/cache_breakdown.md`
- Create: `app/data/knowledge/theory/mysql_indexing.md`
- Create: `app/data/knowledge/theory/kafka_delivery.md`
- Create: `app/data/knowledge/theory/service_scaling.md`

- [ ] **Step 1: 先写失败测试，锁定核心 domain 覆盖**

把 `tests/test_load_knowledge.py` 追加：

```python
def test_build_chunks_covers_core_domains_after_knowledge_expansion():
    chunks = load_knowledge.build_chunks()
    domains = {chunk.domain for chunk in chunks}

    assert {"redis", "fastapi", "mysql", "kafka", "system-design"}.issubset(domains)
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_load_knowledge.py::test_build_chunks_covers_core_domains_after_knowledge_expansion -q
```

Expected: FAIL，因为当前只有 `redis` 与 `general`。

- [ ] **Step 3: 创建 benchmark 文档**

创建 `app/data/knowledge/benchmarks/fastapi_backend.md`：

```md
# FastAPI Backend Project Benchmark

## High-score answer pattern

- Start from the user request path and service SLA.
- Explain async I/O boundaries and where Redis/PostgreSQL fit.
- Mention timeout budget, fallback, and measurable latency impact.

## Bonus points

- Mentions p95 latency before and after optimization.
- Mentions worker count, backpressure, and dependency injection boundaries.
```

创建 `app/data/knowledge/benchmarks/mysql_backend.md`：

```md
# MySQL Backend Project Benchmark

## High-score answer pattern

- Start from the slow query symptom.
- Explain index selection, filtering columns, and tradeoffs.
- Mention verification via EXPLAIN and production impact.

## Bonus points

- Mentions covering index or back-to-table tradeoff.
- Mentions write amplification and index maintenance cost.
```

创建 `app/data/knowledge/benchmarks/kafka_backend.md`：

```md
# Kafka Backend Project Benchmark

## High-score answer pattern

- Start from the event flow and consumer responsibility.
- Explain retry, idempotency, and delivery semantics.
- Mention partitioning strategy and lag monitoring.

## Bonus points

- Mentions dead-letter handling.
- Mentions ordering constraints inside a key partition.
```

创建 `app/data/knowledge/benchmarks/system_design_backend.md`：

```md
# Backend System Design Benchmark

## High-score answer pattern

- Start from scale assumptions and bottlenecks.
- Explain cache, queue, storage, and degradation boundaries.
- Mention observability, rollout, and fault containment.

## Bonus points

- Mentions stateless service scaling.
- Mentions hotspot protection and rate limiting.
```

- [ ] **Step 4: 创建 theory 文档**

创建 `app/data/knowledge/theory/cache_breakdown.md`：

```md
# Cache Breakdown

Cache breakdown usually means many concurrent requests miss the same hot key and hit the database together.

Useful mitigation patterns:

- Mutex or single-flight protection.
- Logical expiration with background rebuild.
- Rate limiting and degraded fallback.
```

创建 `app/data/knowledge/theory/mysql_indexing.md`：

```md
# MySQL Indexing

Important interview checkpoints:

- Whether the filtering column matches the left-most prefix.
- Whether the query can use a covering index.
- Whether EXPLAIN confirms reduced scanned rows.
```

创建 `app/data/knowledge/theory/kafka_delivery.md`：

```md
# Kafka Delivery Semantics

Key interview checkpoints:

- At-most-once, at-least-once, and effectively-once differences.
- Consumer retry and idempotent side effects.
- Partition ordering only holds inside a single partition key.
```

创建 `app/data/knowledge/theory/service_scaling.md`：

```md
# Service Scaling

Key interview checkpoints:

- Stateless service instances scale horizontally more easily.
- Shared bottlenecks usually move to cache, database, queue, or downstream APIs.
- Rate limiting, circuit breaking, and graceful degradation should be planned early.
```

- [ ] **Step 5: 扩展 `infer_domain()`**

把 `scripts/load_knowledge.py` 的 `infer_domain()` 保持为下面实现：

```python
def infer_domain(path: Path, content: str) -> str:
    text = f"{path.stem}\n{content}".lower()
    if "redis" in text:
        return "redis"
    if "fastapi" in text:
        return "fastapi"
    if "mysql" in text:
        return "mysql"
    if "kafka" in text:
        return "kafka"
    if "system design" in text or "service scaling" in text or "scaling" in text:
        return "system-design"
    return "general"
```

- [ ] **Step 6: 运行知识构建测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_load_knowledge.py -q
```

Expected: PASS

- [ ] **Step 7: 提交**

```powershell
git add scripts/load_knowledge.py tests/test_load_knowledge.py app/data/knowledge/benchmarks/fastapi_backend.md app/data/knowledge/benchmarks/mysql_backend.md app/data/knowledge/benchmarks/kafka_backend.md app/data/knowledge/benchmarks/system_design_backend.md app/data/knowledge/theory/cache_breakdown.md app/data/knowledge/theory/mysql_indexing.md app/data/knowledge/theory/kafka_delivery.md app/data/knowledge/theory/service_scaling.md
git commit -m "feat: expand knowledge assets for core backend domains"
```

---

### Task 5: 把 Golden Dataset 扩展到 20+ 案例，并参数化回归

**Files:**
- Modify: `tests/test_golden_dataset.py`
- Create: `tests/golden/redis_cases.json`
- Create: `tests/golden/mysql_cases.json`
- Create: `tests/golden/kafka_cases.json`
- Create: `tests/golden/system_design_cases.json`
- Delete: `tests/golden/redis_strong_answer.json`
- Delete: `tests/golden/redis_weak_answer.json`

- [ ] **Step 1: 先写失败测试，锁定 20+ case 与 score band 校验**

把 `tests/test_golden_dataset.py` 改成：

```python
import json
from pathlib import Path

import pytest

from app.graphs.interview_state import build_initial_state
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
)


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

DOMAIN_REFERENCES = {
    "redis": FeedbackReference(
        chunk_id="redis-1",
        title="Redis cache consistency",
        source_type="theory",
        excerpt="Delete cache after database writes and handle race conditions.",
    ),
    "mysql": FeedbackReference(
        chunk_id="mysql-1",
        title="MySQL indexing",
        source_type="theory",
        excerpt="Use EXPLAIN and covering indexes to reduce scanned rows.",
    ),
    "kafka": FeedbackReference(
        chunk_id="kafka-1",
        title="Kafka delivery semantics",
        source_type="theory",
        excerpt="Retry, idempotency, and partition ordering matter.",
    ),
    "system-design": FeedbackReference(
        chunk_id="system-design-1",
        title="Service scaling",
        source_type="theory",
        excerpt="Scale stateless services and isolate shared bottlenecks.",
    ),
}

DOMAIN_KEYWORDS = {
    "redis": ["race conditions", "fallback", "p95 latency", "cache-aside"],
    "mysql": ["explain", "covering index", "back to table", "slow query"],
    "kafka": ["idempotent", "retry", "partition", "at-least-once"],
    "system-design": ["bottleneck", "rate limiting", "degrade", "cache"],
}


def make_plan(question: str, domain: str) -> InterviewPlan:
    return InterviewPlan(
        title="Golden backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical" if domain != "system-design" else "system-design",
                prompt=question,
                focus=domain,
            )
        ],
    )


def make_state(case: dict):
    domain = case["job_tags"][0]
    state = build_initial_state(
        session_id="golden-s1",
        plan=make_plan(case["question"], domain),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=case["job_tags"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": case["answer"],
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


class GoldenVectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        domain = job_tags[0]
        reference = DOMAIN_REFERENCES[domain]
        return [reference.model_dump()]


class GoldenRuleBasedLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        item = evaluation_items[0]
        domain = item["focus"]
        answer = item["messages"][1]["content"].lower()
        keywords = DOMAIN_KEYWORDS[domain]
        hits = [keyword for keyword in keywords if keyword in answer]
        missing = [keyword for keyword in keywords if keyword not in answer]
        score = min(95, 55 + 10 * len(hits))
        reference = DOMAIN_REFERENCES[domain]

        rationale = (
            f"Based on {reference.title}, the answer covered "
            + (", ".join(hits) if hits else "too few core signals")
            + "."
        )
        critique = (
            "Missing: " + ", ".join(missing[:3])
            if missing
            else "Strong answer with only minor room for extra detail."
        )

        return InterviewReport(
            session_id=session_id,
            overall_score=score,
            overall_dimension_scores=DimensionScores(
                breadth=score,
                depth=score,
                architecture=score,
                engineering=score,
                communication=score,
            ),
            summary="Golden dataset evaluation.",
            highlights=["Grounded in retrieved domain guidance"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text=item["question_text"],
                    user_answer=item["messages"][1]["content"],
                    score=score,
                    dimension_scores=DimensionScores(
                        breadth=score,
                        depth=score,
                        architecture=score,
                        engineering=score,
                        communication=score,
                    ),
                    rationale=rationale,
                    critique=critique,
                    better_answer="Cover the domain-specific core signals in a concrete engineering story.",
                    references=[reference],
                )
            ],
        )


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(GOLDEN_DIR.glob("*_cases.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, list)
        cases.extend(payload)
    return cases


def test_golden_dataset_contains_at_least_twenty_cases():
    assert len(load_cases()) >= 20


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_golden_case_matches_score_band_and_explanations(case):
    evaluator = ExpertShadowEvaluator(
        llm=GoldenRuleBasedLLM(),
        vector_store=GoldenVectorStore(),
    )

    report = evaluator.evaluate(make_state(case))
    feedback = report.feedbacks[0]

    assert case["expected_score_min"] <= report.overall_score <= case["expected_score_max"]
    assert feedback.references
    for signal in case.get("expected_signals", []):
        assert signal.lower() in feedback.rationale.lower() or signal.lower() in feedback.user_answer.lower()
    for gap in case.get("expected_gaps", []):
        assert gap.lower() in feedback.critique.lower() or gap.lower() in feedback.rationale.lower()
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_golden_dataset.py -q
```

Expected: FAIL，因为当前还没有 `*_cases.json` 文件，也没有 20+ case。

- [ ] **Step 3: 创建 20+ Golden case 文件**

创建 `tests/golden/redis_cases.json`：

```json
[
  {"id":"redis-strong-1","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"I use cache-aside, delete cache after writes, handle race conditions, keep fallback to MySQL, and watch p95 latency.","expected_score_min":85,"expected_score_max":95,"expected_signals":["cache-aside","race conditions","fallback","p95 latency"],"expected_gaps":[]},
  {"id":"redis-strong-2","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"The key path is cache-aside, race conditions, fallback, and p95 latency tracking.","expected_score_min":85,"expected_score_max":95,"expected_signals":["cache-aside","race conditions","fallback","p95 latency"],"expected_gaps":[]},
  {"id":"redis-mid-1","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"I delete cache after writes and use fallback when needed.","expected_score_min":70,"expected_score_max":84,"expected_signals":["fallback"],"expected_gaps":["race conditions","p95 latency"]},
  {"id":"redis-weak-1","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"I clear the cache sometimes after updates.","expected_score_min":55,"expected_score_max":69,"expected_signals":[],"expected_gaps":["race conditions","fallback","cache-aside"]},
  {"id":"redis-wrong-1","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"I only restart Redis when cache is wrong.","expected_score_min":55,"expected_score_max":65,"expected_signals":[],"expected_gaps":["race conditions","fallback","cache-aside"]}
]
```

创建 `tests/golden/mysql_cases.json`：

```json
[
  {"id":"mysql-strong-1","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"I start from the slow query log, run EXPLAIN, design a covering index, and reduce back to table reads.","expected_score_min":85,"expected_score_max":95,"expected_signals":["slow query","explain","covering index","back to table"],"expected_gaps":[]},
  {"id":"mysql-strong-2","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"My process is slow query analysis, EXPLAIN, covering index validation, and checking back to table cost.","expected_score_min":85,"expected_score_max":95,"expected_signals":["slow query","explain","covering index","back to table"],"expected_gaps":[]},
  {"id":"mysql-mid-1","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"I add an index and run EXPLAIN on the query.","expected_score_min":70,"expected_score_max":84,"expected_signals":["explain"],"expected_gaps":["covering index","back to table"]},
  {"id":"mysql-weak-1","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"I usually increase the database machine size.","expected_score_min":55,"expected_score_max":69,"expected_signals":[],"expected_gaps":["slow query","explain","covering index"]},
  {"id":"mysql-wrong-1","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"I delete indexes because indexes always slow reads.","expected_score_min":55,"expected_score_max":65,"expected_signals":[],"expected_gaps":["explain","covering index","back to table"]}
]
```

创建 `tests/golden/kafka_cases.json`：

```json
[
  {"id":"kafka-strong-1","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"I use at-least-once delivery, idempotent writes, retry handling, and partition keys for ordering.","expected_score_min":85,"expected_score_max":95,"expected_signals":["at-least-once","idempotent","retry","partition"],"expected_gaps":[]},
  {"id":"kafka-strong-2","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"The core is retry, idempotent consumers, at-least-once semantics, and correct partition strategy.","expected_score_min":85,"expected_score_max":95,"expected_signals":["at-least-once","idempotent","retry","partition"],"expected_gaps":[]},
  {"id":"kafka-mid-1","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"I retry failed messages and care about partition ordering.","expected_score_min":70,"expected_score_max":84,"expected_signals":["retry","partition"],"expected_gaps":["idempotent","at-least-once"]},
  {"id":"kafka-weak-1","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"I read messages one by one and hope they succeed.","expected_score_min":55,"expected_score_max":69,"expected_signals":[],"expected_gaps":["retry","idempotent","partition"]},
  {"id":"kafka-wrong-1","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"Ordering is always global in Kafka, so I do not need partition keys.","expected_score_min":55,"expected_score_max":65,"expected_signals":[],"expected_gaps":["partition","idempotent","retry"]}
]
```

创建 `tests/golden/system_design_cases.json`：

```json
[
  {"id":"design-strong-1","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"I first find the bottleneck, add cache, use rate limiting, and design degrade paths for downstream failures.","expected_score_min":85,"expected_score_max":95,"expected_signals":["bottleneck","cache","rate limiting","degrade"],"expected_gaps":[]},
  {"id":"design-strong-2","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"The plan is bottleneck analysis, cache, degrade, and rate limiting before deeper storage changes.","expected_score_min":85,"expected_score_max":95,"expected_signals":["bottleneck","cache","rate limiting","degrade"],"expected_gaps":[]},
  {"id":"design-mid-1","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"I would add cache and then review bottlenecks.","expected_score_min":70,"expected_score_max":84,"expected_signals":["cache","bottleneck"],"expected_gaps":["rate limiting","degrade"]},
  {"id":"design-weak-1","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"I would buy a bigger server.","expected_score_min":55,"expected_score_max":69,"expected_signals":[],"expected_gaps":["bottleneck","cache","rate limiting"]},
  {"id":"design-wrong-1","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"I would remove rate limiting because it blocks traffic growth.","expected_score_min":55,"expected_score_max":65,"expected_signals":[],"expected_gaps":["bottleneck","cache","degrade"]}
]
```

- [ ] **Step 4: 分别向对应 domain 文件各补 1 个 mid case，使总数 >= 20**

向 `tests/golden/redis_cases.json` 末尾追加：

```json
,
{"id":"redis-mid-2","question":"Explain Redis cache invalidation.","job_tags":["redis"],"answer":"I use cache-aside and fallback, but I did not deeply handle race conditions.","expected_score_min":70,"expected_score_max":84,"expected_signals":["cache-aside","fallback"],"expected_gaps":["race conditions","p95 latency"]}
```

向 `tests/golden/mysql_cases.json` 末尾追加：

```json
,
{"id":"mysql-mid-2","question":"How do you optimize a slow MySQL query?","job_tags":["mysql"],"answer":"I check the slow query log and run EXPLAIN.","expected_score_min":70,"expected_score_max":84,"expected_signals":["slow query","explain"],"expected_gaps":["covering index","back to table"]}
```

向 `tests/golden/kafka_cases.json` 末尾追加：

```json
,
{"id":"kafka-mid-2","question":"How do you ensure reliable Kafka consumption?","job_tags":["kafka"],"answer":"I use idempotent writes and retry failed messages.","expected_score_min":70,"expected_score_max":84,"expected_signals":["idempotent","retry"],"expected_gaps":["partition","at-least-once"]}
```

向 `tests/golden/system_design_cases.json` 末尾追加：

```json
,
{"id":"design-mid-2","question":"How would you scale a hot API?","job_tags":["system-design"],"answer":"I start from the bottleneck and then add cache.","expected_score_min":70,"expected_score_max":84,"expected_signals":["bottleneck","cache"],"expected_gaps":["rate limiting","degrade"]}
```

补完后总数为 24，且每个 case 按 `job_tags[0]` 落在对应领域文件里。

- [ ] **Step 5: 删除阶段四遗留的死数据 Golden 文件**

Run:

```powershell
Remove-Item -LiteralPath 'tests/golden/redis_strong_answer.json'
Remove-Item -LiteralPath 'tests/golden/redis_weak_answer.json'
```

Expected: `tests/golden` 目录只保留 `*_cases.json` 形式的新数据文件。

- [ ] **Step 6: 运行 Golden 测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_golden_dataset.py -q
```

Expected: PASS

- [ ] **Step 7: 提交**

```powershell
git add tests/test_golden_dataset.py tests/golden/redis_cases.json tests/golden/mysql_cases.json tests/golden/kafka_cases.json tests/golden/system_design_cases.json
git add -u tests/golden
git commit -m "test: expand golden dataset coverage and score bands"
```

---

### Task 6: 全量回归、真实导入验证与人工闭环

**Files:**
- Review: `scripts/load_knowledge.py`
- Review: `app/services/vector_store.py`
- Review: `tests/test_vector_store_pgvector.py`
- Review: `tests/test_golden_dataset.py`
- Review: `app/static/app.js`
- Review: `app/static/styles.css`

- [ ] **Step 1: 安装依赖**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pip install -r requirements.txt
```

Expected: 成功安装 `psycopg2-binary`、`pgvector`、`sentence-transformers`

- [ ] **Step 2: 跑默认全量回归**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS

- [ ] **Step 3: 跑 pgvector 标记测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q -m pgvector
```

Expected:
- 未配置 `POSTGRES_DSN`：SKIPPED
- 已配置：PASS

- [ ] **Step 4: 真实知识导入**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

Expected: 输出 `Discovered X knowledge chunks and upserted X rows.`

- [ ] **Step 5: 人工验证前端空引用与失败态**

Run:

```powershell
uvicorn app.main:app --reload
```

Then verify manually:
- 所有验证都通过真实 HTTP 流程完成，不直接调用 `generate_report_for_session()`；至少走一遍 `/api/interviews` 创建会话、`/api/interviews/{session_id}/answer` 提交到 finished、再轮询 `/api/interviews/{session_id}/report`
- 正常报告：有 references 时显示 reference card
- 空引用报告：显示 `No strong reference found for this answer.`
- retrieval unavailable：通过无效 `POSTGRES_DSN` 或调试注入 failing store 触发真实 `BackgroundTasks` 检索失败，再确认 `/report` 失败态与前端都显示 `Knowledge retrieval unavailable`

- [ ] **Step 6: 检查变更范围**

Run:

```powershell
git diff -- app tests scripts requirements.txt pytest.ini
```

Expected:
- 没有引入 Redis / Celery / WebSocket
- 没有迁移 session/report 到 PostgreSQL
- 变更只集中在 loader、测试、前端展示、知识资产、Golden Dataset

- [ ] **Step 7: 如有收尾修复则提交**

```powershell
git add app tests scripts requirements.txt pytest.ini
git commit -m "test: verify rag validation and quality hardening"
```

若无额外收尾修复，则跳过。

---

## 自检清单

- [ ] `scripts/load_knowledge.py` 支持注入 store，并返回结构化 summary
- [ ] 真实 `pgvector` round-trip 测试存在，且 `POSTGRES_DSN` 缺失时会 skip
- [ ] `tests/test_vector_store_pgvector.py` 的 round-trip 断言只锁定首个结果 `chunk_id`
- [ ] retrieval unavailable 场景在 `report_tasks` 和 `report_api` 都有断言
- [ ] 前端能区分 retrieval failure 和 evidence insufficient
- [ ] 知识库 domain 不再只剩 `redis/general`
- [ ] 知识文档至少覆盖 `redis / fastapi / mysql / kafka / system-design`
- [ ] Golden Dataset 总案例数 `>= 20`
- [ ] mixed domain 新增 case 按领域分别落在各自 `*_cases.json` 文件
- [ ] 阶段四遗留 `redis_strong_answer.json` / `redis_weak_answer.json` 已删除
- [ ] Golden 测试校验 score band、references、rationale、critique
- [ ] 默认回归 `pytest -q` 不依赖 PostgreSQL
- [ ] `pytest -q -m pgvector` 在真实环境可运行
- [ ] 真实 `/answer -> BackgroundTasks -> /report` retrieval failure 链路做过人工验收

## 执行交接

Plan complete and saved to `docs/superpowers/plans/2026-07-02-stage-5-rag-validation-and-hardening.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
