from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from app.services.report import (
    ReportCoverageV2,
    ReportEvidenceRefV2,
    ReportObservationV2,
    ReportPriorityActionV2,
)


REPORT_ACTION_PLANNER_VERSION = "report-priority-action-planner-v1"
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RELEVANCE_ORDER = {"low": 0, "medium": 1, "high": 2}
EVIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class ActionSpec:
    title: str
    why: str
    practice: str
    completion: str
    actionability: int
    verifiability: int


ACTION_SPECS = {
    "technical_foundation": ActionSpec(
        title="补齐核心机制解释",
        why="缺少机制说明会让结论难以验证，也无法判断方案在哪些边界下成立。",
        practice="按触发条件、内部机制、结果和失效边界四步重答所引用题目。",
        completion="下一轮能连续说明四步，并为每一步给出可观察的验证点。",
        actionability=3,
        verifiability=3,
    ),
    "structured_execution": ActionSpec(
        title="把方案改成可验证的步骤链",
        why="只有结论而没有执行顺序，会掩盖依赖关系、失败点和验证缺口。",
        practice="把回答改写成背景、动作顺序、关键分支、验证结果四段。",
        completion="下一轮能明确说出至少三个有因果关系的步骤及其验证方式。",
        actionability=3,
        verifiability=3,
    ),
    "tradeoff_analysis": ActionSpec(
        title="显式说明关键技术取舍",
        why="没有取舍依据时，方案看起来像单一路径背诵，无法证明决策质量。",
        practice="为所引用题目的主方案补充一个备选方案，并比较收益、代价和适用边界。",
        completion="下一轮能给出至少一个备选项、两个比较维度和明确选择条件。",
        actionability=3,
        verifiability=3,
    ),
    "risk_identification": ActionSpec(
        title="先列失败模式与风险边界",
        why="高风险信号若未被识别，可能让后续设计和验证建立在不安全前提上。",
        practice="针对所引用题目列出触发条件、影响范围、检测信号和缓解措施。",
        completion="下一轮至少说清一个失败模式及对应的检测与缓解闭环。",
        actionability=3,
        verifiability=3,
    ),
    "recovery_strategy": ActionSpec(
        title="补全失败恢复与兜底",
        why="缺少恢复路径会让方案只覆盖正常流程，无法证明故障时仍可控。",
        practice="为所引用题目补充失败触发、降级或回滚动作、恢复条件和事后验证。",
        completion="下一轮能说清触发、动作、恢复和验证四个环节。",
        actionability=3,
        verifiability=3,
    ),
    "measurable_outcomes": ActionSpec(
        title="加入可量化的验证指标",
        why="没有指标和基线时，无法区分方案是否真正改善，也无法复盘结果。",
        practice="为所引用题目补充一个基线、一个目标指标、采样窗口和验收方式。",
        completion="下一轮能给出指标名称、对比基线、观察窗口和通过条件；未知数字使用事实占位符。",
        actionability=3,
        verifiability=3,
    ),
    "production_operations": ActionSpec(
        title="补全生产监控与发布闭环",
        why="缺少监控、告警和发布控制，会使方案难以安全进入生产环境。",
        practice="为所引用题目补充灰度步骤、核心监控、告警阈值来源和回滚条件。",
        completion="下一轮能覆盖发布、监控、告警、回滚四项，并说明如何验证恢复。",
        actionability=3,
        verifiability=3,
    ),
    "technical_specificity": ActionSpec(
        title="补充关键技术细节",
        why="缺少接口、数据流或关键操作细节时，方案无法被实现和审查。",
        practice="选择所引用题目的一个关键路径，补充输入、处理、状态变化和输出。",
        completion="下一轮能用可实现的技术名词说明完整路径，并指出一个边界情况。",
        actionability=3,
        verifiability=2,
    ),
    "communication_clarity": ActionSpec(
        title="用结论—依据—边界组织回答",
        why="表达结构不清会让有效技术信息难以识别，也容易遗漏结论边界。",
        practice="将所引用题目的回答压缩为结论、两条依据和一个限制。",
        completion="下一轮先给结论，再给不重复的依据，最后主动说明适用边界。",
        actionability=3,
        verifiability=3,
    ),
    "architecture_design": ActionSpec(
        title="补全架构边界与扩展路径",
        why="缺少组件职责和扩展边界时，架构方案无法判断瓶颈与演进成本。",
        practice="为所引用题目补充组件职责、关键数据流、容量假设和扩展触发条件。",
        completion="下一轮能画面化描述组件与数据流，并说清一个扩展触发条件。",
        actionability=2,
        verifiability=2,
    ),
}


DEFAULT_ACTION_SPEC = ActionSpec(
    title="补齐可验证的技术说明",
    why="当前证据显示回答仍有关键缺口，需要用可核查的机制和边界补全。",
    practice="针对所引用题目重答一次，明确机制、取舍、风险和验证方式。",
    completion="下一轮能给出具体机制、至少一个边界和可观察的完成信号。",
    actionability=2,
    verifiability=2,
)


@dataclass(frozen=True)
class ActionCandidate:
    topic: str
    observations: tuple[ReportObservationV2, ...]
    question_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    severity: int
    role_relevance: int
    evidence_strength: int
    actionability: int
    verifiability: int


def plan_priority_actions(
    *,
    observations: Iterable[ReportObservationV2],
    coverage: ReportCoverageV2,
    evidence_refs: Iterable[ReportEvidenceRefV2],
    max_actions: int = 3,
) -> list[ReportPriorityActionV2]:
    if not 1 <= max_actions <= 3:
        raise ValueError("max_actions must be between 1 and 3")

    evidence_by_id = {item.evidence_ref_id: item for item in evidence_refs}
    evidence_ids = set(evidence_by_id)
    grouped: dict[str, list[ReportObservationV2]] = {}
    for observation in observations:
        dimension_coverage = coverage.per_dimension.get(observation.dimension)
        if (
            observation.type not in {"gap", "risk"}
            or not observation.answer_evidence_refs
            or dimension_coverage is None
            or dimension_coverage.status != "evaluated"
        ):
            continue
        referenced = {
            *observation.answer_evidence_refs,
            *observation.knowledge_refs,
        }
        if not referenced.issubset(evidence_ids):
            continue
        if any(
            evidence_by_id[ref].namespace != "candidate"
            or evidence_by_id[ref].question_id not in observation.question_refs
            for ref in observation.answer_evidence_refs
        ):
            continue
        if any(
            evidence_by_id[ref].namespace != "reference"
            for ref in observation.knowledge_refs
        ):
            continue
        grouped.setdefault(observation.normalized_topic, []).append(observation)

    candidates = [_candidate(topic, items) for topic, items in grouped.items()]
    candidates.sort(key=_candidate_rank)
    selected: list[ActionCandidate] = []
    low_evidence_count = 0
    for candidate in candidates:
        if candidate.evidence_strength == EVIDENCE_ORDER["low"]:
            if low_evidence_count >= 1:
                continue
            low_evidence_count += 1
        selected.append(candidate)
        if len(selected) == max_actions:
            break
    return [_render_action(item) for item in selected]


def _candidate(
    topic: str,
    observations: list[ReportObservationV2],
) -> ActionCandidate:
    observations = sorted(observations, key=lambda item: item.observation_id)
    spec = ACTION_SPECS.get(topic, DEFAULT_ACTION_SPEC)
    return ActionCandidate(
        topic=topic,
        observations=tuple(observations),
        question_refs=tuple(
            sorted({ref for item in observations for ref in item.question_refs})
        ),
        evidence_refs=tuple(
            sorted(
                {
                    ref
                    for item in observations
                    for ref in (
                        *item.answer_evidence_refs,
                        *item.knowledge_refs,
                    )
                }
            )
        ),
        severity=max(SEVERITY_ORDER[item.severity] for item in observations),
        role_relevance=max(
            RELEVANCE_ORDER[item.role_relevance] for item in observations
        ),
        evidence_strength=max(
            EVIDENCE_ORDER[item.evidence_strength] for item in observations
        ),
        actionability=spec.actionability,
        verifiability=spec.verifiability,
    )


def _candidate_rank(item: ActionCandidate) -> tuple:
    return (
        -len(item.question_refs),
        -item.severity,
        -item.role_relevance,
        -item.evidence_strength,
        -item.actionability,
        -item.verifiability,
        item.topic,
    )


def _render_action(item: ActionCandidate) -> ReportPriorityActionV2:
    spec = ACTION_SPECS.get(item.topic, DEFAULT_ACTION_SPEC)
    questions = "、".join(item.question_refs)
    action_hash = hashlib.sha256(item.topic.encode("utf-8")).hexdigest()[:16]
    return ReportPriorityActionV2(
        action_id=f"action-{action_hash}",
        title=spec.title,
        why_it_matters=spec.why,
        practice=f"{spec.practice} 练习范围：题目{questions}。",
        completion_criteria=spec.completion,
        limitation=(
            f"仅基于题目{questions}的已引用回答；"
            "不推断未观察到的实际项目经历。"
        ),
        question_refs=list(item.question_refs),
        observation_refs=[
            observation.observation_id for observation in item.observations
        ],
        evidence_refs=list(item.evidence_refs),
    )
