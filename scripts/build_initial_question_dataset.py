from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.interview_quality_dataset import (
    InterviewQualityCase,
    expected_case_hashes,
    load_interview_quality_dataset,
)


DATASET_PATH = ROOT / "tests/golden/interview_quality_v1/initial-question-quality-v2.json"
MANIFEST_PATH = ROOT / "tests/golden/interview_quality_v1/manifest.json"

QUESTION_MIXES = {
    "balanced": (
        "project", "technical", "system-design", "behavioral", "technical",
        "project", "system-design", "behavioral", "technical",
    ),
    "technical": (
        "technical", "project", "system-design", "technical", "behavioral",
        "technical", "project", "system-design", "technical",
    ),
    "architecture": (
        "system-design", "technical", "project", "system-design", "behavioral",
        "technical", "system-design", "project", "system-design",
    ),
    "project": (
        "project", "technical", "behavioral", "project", "system-design",
        "technical", "project", "behavioral", "project",
    ),
}
QUESTION_COUNTS = {15: 3, 30: 5, 45: 7, 60: 9}

SPECS = (
    {
        "id": "backend-api-foundation-zh-train-001",
        "language": "zh-Hans", "partition": "train", "domain": "backend",
        "difficulty": "foundation", "focus": "technical_depth", "duration": 15,
        "mix": "balanced",
        "jd": "负责 Python API 的接口实现、数据库访问、日志排障和基础单元测试，能够清晰解释请求处理链路与常见错误。",
        "resume": "合成候选人完成过 FastAPI 订单接口练习，使用 PostgreSQL 保存数据，并为异常响应和核心服务编写过测试。",
        "keywords": ["FastAPI", "PostgreSQL", "请求链路"],
        "focus_evidence": ["实现细节", "错误处理"],
        "knowledge": "FastAPI dependency lifecycle and safe database transaction boundaries.",
    },
    {
        "id": "frontend-performance-intermediate-zh-train-002",
        "language": "zh-Hans", "partition": "train", "domain": "frontend",
        "difficulty": "intermediate", "focus": "balanced", "duration": 30,
        "mix": "technical",
        "jd": "负责 React 业务页面、状态管理、性能优化和可访问性改进，需要与接口团队协作并能定位线上交互问题。",
        "resume": "合成候选人重构过数据看板，减少重复渲染并补充键盘操作，曾通过性能指标和用户反馈验证改动效果。",
        "keywords": ["React", "性能优化", "可访问性"],
        "focus_evidence": ["项目证据", "技术取舍", "协作"],
        "knowledge": "React rendering diagnostics, accessibility semantics, and measurable web performance.",
    },
    {
        "id": "data-pipeline-advanced-zh-train-003",
        "language": "zh-Hans", "partition": "train", "domain": "data",
        "difficulty": "advanced", "focus": "system_design", "duration": 45,
        "mix": "architecture",
        "jd": "设计批流一体数据平台，处理迟到数据、幂等写入、血缘追踪和质量告警，并对容量、成本与恢复目标负责。",
        "resume": "合成候选人设计过 Kafka 到湖仓的处理链路，处理事件乱序和回放，记录了吞吐、延迟及失败恢复方案。",
        "keywords": ["Kafka", "迟到数据", "幂等写入"],
        "focus_evidence": ["容量估算", "故障恢复", "架构取舍"],
        "knowledge": "Event-time processing, idempotent sinks, lineage, and replay-safe pipeline design.",
    },
    {
        "id": "platform-multiregion-advanced-zh-train-004",
        "language": "zh-Hans", "partition": "train", "domain": "platform",
        "difficulty": "advanced", "focus": "system_design", "duration": 60,
        "mix": "architecture",
        "jd": "负责多区域平台的发布、可观测性、容量治理和故障隔离，推动服务等级目标、演练与自动化恢复机制落地。",
        "resume": "合成候选人维护过 Kubernetes 平台，主导过区域故障演练和发布回滚改造，并用指标衡量恢复时间变化。",
        "keywords": ["Kubernetes", "多区域", "故障隔离"],
        "focus_evidence": ["可靠性", "容量治理", "演进成本"],
        "knowledge": "Multi-region failure isolation, SLO-based operations, and controlled deployment rollback.",
    },
    {
        "id": "project-delivery-foundation-zh-dev-005",
        "language": "zh-Hans", "partition": "dev", "domain": "general_project",
        "difficulty": "foundation", "focus": "project_review", "duration": 15,
        "mix": "project",
        "jd": "参与通用业务项目交付，能够说明任务拆解、沟通协作、风险处理和结果验证，并对个人职责边界保持清晰。",
        "resume": "合成候选人参与过内部审批工具开发，负责表单和接口联调，处理过延期风险并根据使用反馈完成迭代。",
        "keywords": ["任务拆解", "接口联调", "结果验证"],
        "focus_evidence": ["职责边界", "项目结果"],
        "knowledge": "Evidence-based project review with clear ownership, constraints, decisions, and outcomes.",
    },
    {
        "id": "system-jobqueue-intermediate-zh-dev-006",
        "language": "zh-Hans", "partition": "dev", "domain": "system_design",
        "difficulty": "intermediate", "focus": "system_design", "duration": 30,
        "mix": "architecture",
        "jd": "设计异步任务系统，覆盖任务提交、优先级、重试、幂等、积压处理与监控，需要解释关键一致性和可用性取舍。",
        "resume": "合成候选人实现过基于 Redis 和消息队列的任务处理服务，处理重复消费、失败重试及工作节点扩缩容。",
        "keywords": ["异步任务", "幂等", "积压处理"],
        "focus_evidence": ["架构边界", "可靠性取舍"],
        "knowledge": "Durable job queues, idempotency keys, backpressure, retries, and worker scaling.",
    },
    {
        "id": "backend-consistency-intermediate-zh-dev-007",
        "language": "zh-Hans", "partition": "dev", "domain": "backend",
        "difficulty": "intermediate", "focus": "technical_depth", "duration": 45,
        "mix": "technical",
        "jd": "负责高并发交易服务的缓存一致性、数据库事务、接口幂等和故障诊断，要求能用证据解释技术选择。",
        "resume": "合成候选人优化过 Redis 缓存与 MySQL 查询，处理过超时重试造成的重复写入，并补充监控和压测。",
        "keywords": ["Redis", "事务", "接口幂等"],
        "focus_evidence": ["实现边界", "诊断证据", "失败模式"],
        "knowledge": "Cache consistency, transaction boundaries, idempotent writes, and timeout diagnosis.",
    },
    {
        "id": "frontend-migration-advanced-zh-dev-008",
        "language": "zh-Hans", "partition": "dev", "domain": "frontend",
        "difficulty": "advanced", "focus": "project_review", "duration": 60,
        "mix": "project",
        "jd": "主导大型前端迁移，兼顾模块边界、渐进发布、性能预算、可访问性和跨团队协作，并量化风险与收益。",
        "resume": "合成候选人推动过旧管理端迁移到 React，设计兼容层和灰度方案，跟踪包体、错误率与交付周期变化。",
        "keywords": ["渐进迁移", "性能预算", "灰度发布"],
        "focus_evidence": ["所有权", "迁移决策", "量化结果"],
        "knowledge": "Incremental frontend migration, compatibility boundaries, release safety, and performance budgets.",
    },
    {
        "id": "data-quality-foundation-zh-blind-009",
        "language": "zh-Hans", "partition": "blind-test", "domain": "data",
        "difficulty": "foundation", "focus": "balanced", "duration": 30,
        "mix": "balanced",
        "jd": "维护分析数据任务，完成字段校验、失败重跑、基础 SQL 优化和数据质量记录，并能与需求方确认口径。",
        "resume": "合成候选人编写过定时 ETL 和质量检查，定位过空值与重复记录问题，并为报表口径建立说明文档。",
        "keywords": ["ETL", "数据质量", "SQL"],
        "focus_evidence": ["基础实现", "问题定位", "需求沟通"],
        "knowledge": "Data validation, safe reruns, SQL diagnostics, and metric definition alignment.",
    },
    {
        "id": "platform-observability-intermediate-en-blind-010",
        "language": "en", "partition": "blind-test", "domain": "platform",
        "difficulty": "intermediate", "focus": "technical_depth", "duration": 15,
        "mix": "technical",
        "jd": "Build observability tooling for container services, including actionable metrics, tracing, alert design, incident diagnosis, and safe operational automation.",
        "resume": "The synthetic candidate instrumented Kubernetes services, reduced noisy alerts, and documented an incident investigation using traces and service-level indicators.",
        "keywords": ["Kubernetes", "tracing", "alert design"],
        "focus_evidence": ["diagnostic depth", "operational boundaries"],
        "knowledge": "Telemetry cardinality, trace-based diagnosis, actionable alerts, and safe automation.",
    },
    {
        "id": "project-incident-advanced-zh-blind-011",
        "language": "zh-Hans", "partition": "blind-test", "domain": "general_project",
        "difficulty": "advanced", "focus": "project_review", "duration": 45,
        "mix": "project",
        "jd": "复盘关键线上事故与改进项目，区分事实、假设和责任边界，推动跨团队修复并用长期指标验证治理效果。",
        "resume": "合成候选人负责过支付链路事故复盘，协调多个团队完成限流和降级改造，并追踪后续告警与恢复指标。",
        "keywords": ["事故复盘", "降级", "恢复指标"],
        "focus_evidence": ["因果边界", "跨团队推动", "长期效果"],
        "knowledge": "Blameless incident analysis, causal evidence, remediation ownership, and durable outcome metrics.",
    },
    {
        "id": "system-collaboration-foundation-en-blind-012",
        "language": "en", "partition": "blind-test", "domain": "system_design",
        "difficulty": "foundation", "focus": "balanced", "duration": 60,
        "mix": "balanced",
        "jd": "Design a small collaboration service while explaining core API, storage, reliability, project delivery, and teamwork choices at a foundation-friendly level.",
        "resume": "The synthetic candidate built a team notes application with authentication, PostgreSQL storage, basic caching, tests, and a documented deployment process.",
        "keywords": ["collaboration service", "PostgreSQL", "deployment"],
        "focus_evidence": ["fundamentals", "project evidence", "design choices"],
        "knowledge": "Foundational API design, relational storage, caching, testing, and deployment trade-offs.",
    },
)


def _question_type_budget(duration: int, mix: str) -> dict[str, int]:
    values = QUESTION_MIXES[mix][: QUESTION_COUNTS[duration]]
    return {
        kind: values.count(kind)
        for kind in ("project", "technical", "system-design", "behavioral")
        if kind in values
    }


def _case_payload(spec: dict) -> dict:
    configuration = {
        "difficulty": spec["difficulty"],
        "target_duration_minutes": spec["duration"],
        "focus_preset": spec["focus"],
        "question_type_budget": _question_type_budget(spec["duration"], spec["mix"]),
        "expected_followup_budget": QUESTION_COUNTS[spec["duration"]],
        "max_followups_per_question": 2,
        "generator_version": "plan-generator-v2",
        "followup_policy_version": "fixed_v1",
    }
    payload = {
        "case_id": spec["id"],
        "case_version": 1,
        "language": spec["language"],
        "case_type": "initial_question",
        "question_type": "mixed",
        "difficulty": spec["difficulty"],
        "quality_label": "strong",
        "partition": spec["partition"],
        "source_boundary": {
            "classification": "synthetic",
            "description": "Synthetic JD and resume paired with a public technical-topic summary; no real person, employer, or confidential material.",
            "contains_real_candidate_data": False,
            "contains_employer_confidential_data": False,
            "contains_principal_memory": False,
        },
        "input": {
            "scenario_domain": spec["domain"],
            "job_description": spec["jd"],
            "resume_summary": spec["resume"],
            "configuration": configuration,
            "runs_per_case": 2,
            "role_keywords": spec["keywords"],
            "focus_evidence": spec["focus_evidence"],
            "forbidden_leak_markers": [
                "参考答案", "reference answer", "内部证据", "internal evidence",
                "knowledge_binding", "evidence_ref",
            ],
            "knowledge_context": [
                {
                    "source": "public_technical_material",
                    "title": f"{spec['domain']} evaluation topic",
                    "summary": spec["knowledge"],
                }
            ],
        },
        "expectation": {"action": "accept_plan", "score_range": None},
        "must_have_evidence": [
            "Questions connect to at least one declared role keyword or resume project.",
            "The configured focus and difficulty are observable without leaking an answer.",
            "Question count, type budget, follow-up budget, and duration estimate match the frozen configuration.",
        ],
        "forbidden_inference": [
            "Do not infer a real candidate identity or employer.",
            "Do not treat public technical material as candidate experience.",
            "Do not expose reference answers, evidence identifiers, or internal prompt text.",
        ],
        "annotation": {
            "annotator_id": "synthetic-initial-question-author-v1",
            "reviewer_id": None,
            "review_status": "pending",
            "dispute_status": "none",
            "resolution": None,
            "rationale": "Pending independent review before blocking quality use.",
            "review_notes": [],
        },
        "provider_allowed": True,
        "gate_eligible": False,
        "hashes": {
            "algorithm": "sha256-canonical-json-v1",
            "source_sha256": "0" * 64,
            "content_sha256": "0" * 64,
        },
    }
    case = InterviewQualityCase.model_validate(payload)
    source_sha256, content_sha256 = expected_case_hashes(case)
    payload["hashes"]["source_sha256"] = source_sha256
    payload["hashes"]["content_sha256"] = content_sha256
    return payload


def main() -> int:
    dataset = {
        "schema_version": "interview-quality-dataset-contract-v1",
        "dataset_id": "initial-question-quality-v2",
        "dataset_version": "initial-question-quality-v2",
        "description": "T57 synthetic, stratified JD/resume dataset for configured initial interview-plan quality and budget evaluation.",
        "fixture_only": False,
        "cases": [_case_payload(spec) for spec in SPECS],
    }
    DATASET_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    load_interview_quality_dataset(DATASET_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["files"][DATASET_PATH.name] = hashlib.sha256(
        DATASET_PATH.read_bytes()
    ).hexdigest()
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "dataset": str(DATASET_PATH.relative_to(ROOT)),
                "case_count": len(dataset["cases"]),
                "sha256": manifest["files"][DATASET_PATH.name],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
