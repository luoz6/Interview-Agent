import argparse
import hashlib
import json
from pathlib import Path

from app.services.report_calibration_dataset import CalibrationDataset


SCENARIOS = [
    ("redis-consistency", "technical", "如何处理 Redis 与数据库一致性？"),
    ("mysql-index", "technical", "如何诊断 MySQL 联合索引未生效？"),
    ("kafka-reliability", "technical", "如何降低 Kafka 消息丢失和重复消费风险？"),
    ("api-idempotency", "technical", "如何设计支付 API 的幂等处理？"),
    ("transaction-boundary", "technical", "如何确定事务边界并处理失败补偿？"),
    ("queue-capacity", "system_design", "如何设计可扩展任务队列并估算容量？"),
    ("service-isolation", "system_design", "如何隔离服务故障并控制级联风险？"),
    ("hot-key", "system_design", "如何设计热点 Key 的保护方案？"),
    ("audit-log", "system_design", "如何设计可追溯审计日志系统？"),
    ("rate-limit", "system_design", "如何设计多租户限流系统？"),
    ("incident-review", "project_review", "请复盘一次线上故障和改进闭环。"),
    ("latency-optimization", "project_review", "请说明一次延迟优化的过程和度量。"),
    ("migration", "project_review", "请复盘一次数据库迁移及回退设计。"),
    ("cost-reduction", "project_review", "请说明一次成本优化及其取舍。"),
    ("delivery-risk", "project_review", "请复盘一次延期风险的处理。"),
    ("technical-conflict", "behavioral", "如何处理团队中的技术方案分歧？"),
    ("priority-change", "behavioral", "需求优先级突然变化时如何处理？"),
    ("ownership", "behavioral", "请说明一次主动承担责任的经历。"),
    ("feedback", "behavioral", "收到负面反馈后你如何改进？"),
    ("cross-team", "behavioral", "如何推动跨团队依赖按时交付？"),
]

LANGUAGE_BY_GROUP = ["zh"] * 8 + ["en"] * 6 + ["mixed"] * 6
RANGES = {
    "strong": [80, 95],
    "medium": [45, 75],
    "incorrect": [0, 40],
    "off_topic": [0, 10],
    "empty": [0, 10],
}


def _answers(language: str, terminal: str) -> dict[str, str]:
    if language == "en":
        return {
            "strong": "First define the boundary, then execute the change. Because timeout and duplicate effects are risks, I compare consistency with latency, retry with idempotency, roll back or degrade on failure, and monitor p95, error rate, alerts, and a production runbook.",
            "medium": "First execute the main path, then retry a failed step and use a timeout. This covers the basic mechanism but does not include production metrics or a complete tradeoff.",
            "incorrect": "The in-memory state is always reliable and never loses data, so we do not need monitoring, rollback, leases, or idempotency.",
            "off_topic": "I would change the page colors and button shadows. This does not answer the technical or experience question.",
            "empty": "I do not know.",
        }
    if language == "mixed":
        return {
            "strong": "首先定义 boundary，然后执行 change；因为 timeout 和 duplicate effect 有风险，需要权衡 consistency 与 latency。失败时 retry + idempotency，并准备 rollback、degrade；线上监控 p95、error rate、告警和 runbook。",
            "medium": "先走 main path，然后对失败步骤 retry 并设置 timeout。覆盖了基本机制，但没有 production metrics 和完整 tradeoff。",
            "incorrect": "内存状态 always reliable，never 丢数据，所以不需要 monitoring、rollback、lease 或 idempotency。",
            "off_topic": "我会调整 CSS color 和 button shadow，但这没有回答当前问题。",
            "empty": "不知道。",
        }
    return {
        "strong": "首先明确边界，然后执行变更；因为超时、重复和不一致存在风险，需要权衡一致性与延迟。失败时采用幂等重试、回滚或降级，线上监控 p95、错误率和告警，并准备值班手册和演练。",
        "medium": "先执行主流程，然后对失败步骤重试并设置超时。回答覆盖基本机制，但没有生产指标和完整取舍。",
        "incorrect": "内存状态天然可靠并且永远不会丢失，所以不需要监控、回滚、租约或幂等。",
        "off_topic": "我会调整页面颜色和按钮阴影，但这没有回答当前问题。",
        "empty": "不知道。",
    }


def _evidence(language: str, quality: str) -> list[str]:
    if quality == "strong":
        if language == "en":
            return ["failure", "retry", "p95"]
        if language == "mixed":
            return ["失败", "retry", "p95"]
        return ["失败", "重试", "p95"]
    if quality == "medium":
        if language in {"en", "mixed"}:
            return ["retry", "timeout"]
        return ["重试", "超时"]
    if quality == "incorrect":
        return ["always reliable"] if language in {"en", "mixed"} else ["天然可靠"]
    return []


def _missing_points(language: str, quality: str) -> list[str]:
    if quality != "medium":
        return []
    if language == "zh":
        return [
            "metric_gap: 缺少可验证度量",
            "production_gap: 缺少生产闭环",
            "tradeoff_gap: 取舍不完整",
            "recovery_gap: 缺少补偿或恢复闭环",
        ]
    if language == "mixed":
        return [
            "metric_gap: 缺少 measurable metrics",
            "production_gap: 缺少 production feedback loop",
            "tradeoff_gap: tradeoff 不完整",
            "recovery_gap: 缺少 compensation or recovery loop",
        ]
    return [
        "metric_gap: missing measurable metrics",
        "production_gap: missing production feedback loop",
        "tradeoff_gap: incomplete tradeoff",
        "recovery_gap: missing compensation or recovery loop",
    ]


def build_dataset() -> CalibrationDataset:
    cases = []
    for index, ((group_id, question_type, question), language) in enumerate(
        zip(SCENARIOS, LANGUAGE_BY_GROUP)
    ):
        terminal = "off_topic" if index % 2 == 0 else "empty"
        partition = "blind" if index >= 15 else "dev"
        answers = _answers(language, terminal)
        for quality in ("strong", "medium", "incorrect", terminal):
            evidence = _evidence(language, quality)
            cases.append(
                {
                    "case_id": f"cal-{group_id}-{quality}",
                    "group_id": group_id,
                    "partition": partition,
                    "language": language,
                    "question_type": question_type,
                    "quality_label": quality,
                    "question": question,
                    "answer": answers[quality],
                    "expected_score_range": RANGES[quality],
                    "required_evidence": evidence,
                    "required_missing_points": _missing_points(language, quality),
                    "forbidden_claims": ["天然可靠", "always reliable"] if quality == "incorrect" else [],
                    "error_tags": [
                        {
                            "strong": "strong_underestimate",
                            "medium": "medium_overestimate",
                            "incorrect": "technical_error_cap",
                            "off_topic": "off_topic_detection",
                            "empty": "empty_semantics",
                        }[quality]
                    ],
                    "annotation": {
                        "author_id": "synthetic-calibration-author-v1",
                        "reviewer_id": None,
                        "review_status": "pending",
                        "rationale": f"Synthetic {quality} calibration example for {question_type}; independent technical review is pending.",
                        "dispute_resolution": None,
                    },
                }
            )
    return CalibrationDataset(
        schema_version="report-score-calibration-v1",
        dataset_id="report-score-calibration-v1",
        dataset_version="2026-08-05.2",
        description="Synthetic 80-case report scoring calibration set; no real candidate, employer-confidential, or Principal Memory data.",
        blind_policy="The 20 blind cases may be loaded by the blind evaluator only after a rubric version and hash are frozen; tuning tools expose aggregate results only.",
        cases=cases,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic report calibration dataset")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/golden/interview_quality_v1/report-score-calibration-v1.json"),
    )
    args = parser.parse_args()
    dataset = build_dataset()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    args.out.write_text(
        content,
        encoding="utf-8",
        newline="\n",
    )
    manifest_path = args.out.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "report-score-calibration-manifest-v1",
                "dataset_file": args.out.name,
                "dataset_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                "case_count": len(dataset.cases),
                "dev_case_count": sum(case.partition == "dev" for case in dataset.cases),
                "blind_case_count": sum(case.partition == "blind" for case in dataset.cases),
                "review_status": dataset.review_status,
                "gate_eligible": dataset.gate_eligible,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
