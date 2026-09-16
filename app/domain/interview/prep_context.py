from app.domain.interview.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)


_TOPIC_LABELS = {
    "python": "Python",
    "fastapi": "FastAPI",
    "redis": "Redis",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "java": "Java",
    "spring": "Spring",
    "kafka": "Kafka",
    "rocketmq": "RocketMQ",
    "rabbitmq": "RabbitMQ",
    "system-design": "系统设计",
    "general": "通用后端能力",
}

_TOPIC_HINTS = {
    "python": "追问 Python 运行时、异步模型、异常处理和工程质量。",
    "fastapi": "追问 FastAPI 依赖注入、请求生命周期、异步接口和可测试性。",
    "redis": "追问缓存一致性、失效时机、穿透保护和降级兜底。",
    "postgresql": "追问索引设计、事务隔离、慢查询定位和连接池配置。",
    "mysql": "追问索引设计、事务隔离、慢查询定位和表结构取舍。",
    "java": "追问 JVM、并发模型、集合框架和服务稳定性。",
    "spring": "追问 Spring Bean 生命周期、事务边界和依赖注入。",
    "kafka": "追问消息可靠性、消费语义、重试和积压处理。",
    "rocketmq": "追问消息可靠性、消费语义、重试死信和积压处理。",
    "rabbitmq": "追问消息确认、死信队列、重试和削峰策略。",
    "system-design": "追问容量估算、瓶颈定位、故障隔离和演进方案。",
    "general": "追问项目背景、职责边界、技术取舍和量化结果。",
}


def deterministic_follow_up_hint(tag: str) -> str:
    return _TOPIC_HINTS.get(tag, _TOPIC_HINTS["general"])


def build_prep_context(
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
    plan: InterviewPlan,
) -> PrepContext:
    normalized_tags = _normalize_topic_tags(job_tags)
    topics = [
        _build_topic(tag, job_description=job_description, resume_text=resume_text)
        for tag in normalized_tags
    ]
    question_hints = [
        _build_question_hint(question, topics=topics)
        for question in plan.questions
    ]
    return PrepContext(
        summary=(
            f"Knowledge Agent 预热了 {len(topics)} 个岗位考点，"
            f"并为 {len(question_hints)} 道题生成追问线索。"
        ),
        topics=topics,
        question_hints=question_hints,
    )


def attach_prep_context(
    plan: InterviewPlan,
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewPlan:
    return plan.model_copy(
        update={
            "prep_context": build_prep_context(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                plan=plan,
            )
        }
    )


def get_question_prep_hint(
    plan: InterviewPlan,
    question_id: str | None,
) -> PrepQuestionHint | None:
    if not question_id or plan.prep_context is None:
        return None
    for hint in plan.prep_context.question_hints:
        if hint.question_id == question_id:
            return hint
    return None


def build_question_prep_context_messages(
    plan: InterviewPlan,
    question_id: str | None,
) -> list[dict[str, str]]:
    hint = get_question_prep_hint(plan, question_id)
    if hint is None or plan.prep_context is None:
        return []

    topic_lookup = {topic.id: topic for topic in plan.prep_context.topics}
    topics = [
        topic_lookup[topic_id]
        for topic_id in hint.topic_ids
        if topic_id in topic_lookup
    ]
    topic_labels = [topic.label for topic in topics] or list(hint.evidence_titles)
    evidence_items = [topic.evidence for topic in topics if topic.evidence]

    parts = [f"Prep guidance for {hint.question_id}:"]
    if topic_labels:
        parts.append(f"focus topics {', '.join(topic_labels)}.")
    if hint.follow_up_hints:
        parts.append(f"Suggested follow-up angles: {' '.join(hint.follow_up_hints)}")
    if evidence_items:
        parts.append(f"Evidence: {' '.join(evidence_items)}")

    content = " ".join(parts).strip()
    return [{"role": "knowledge_agent", "content": content}] if content else []


def _normalize_topic_tags(job_tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in job_tags or ["general"]:
        value = tag.strip().lower()
        if not value:
            continue
        if value not in _TOPIC_LABELS:
            value = "general"
        if value not in normalized:
            normalized.append(value)
    return normalized or ["general"]


def _build_topic(
    tag: str,
    *,
    job_description: str,
    resume_text: str,
) -> PrepKnowledgeTopic:
    label = _TOPIC_LABELS[tag]
    jd_hit = tag != "general" and tag in job_description.lower()
    resume_hit = tag != "general" and tag in resume_text.lower()
    if jd_hit and resume_hit:
        source = "jd_resume_keyword"
        evidence = (
            f"JD 和简历同时命中 {label}，适合作为"
            f"{_topic_evidence_focus(tag)}追问依据。"
        )
    elif jd_hit:
        source = "jd_keyword"
        evidence = f"JD 明确要求 {label}，需要验证候选人是否具备岗位匹配能力。"
    elif resume_hit:
        source = "resume_keyword"
        evidence = f"简历出现 {label}，适合围绕真实项目经历继续深挖。"
    else:
        source = "fallback"
        evidence = "未命中特定技术关键词，先围绕通用后端项目表达和工程实践预热。"
    return PrepKnowledgeTopic(
        id=f"topic-{tag}",
        label=label,
        source=source,
        evidence=evidence,
        tags=[tag],
    )


def _build_question_hint(
    question: InterviewQuestion,
    *,
    topics: list[PrepKnowledgeTopic],
) -> PrepQuestionHint:
    text = f"{question.prompt} {question.focus}".lower()
    matched_topics = [
        topic
        for topic in topics
        if topic.tags[0] == "general"
        or topic.tags[0] in text
        or topic.label.lower() in text
    ]
    if not matched_topics:
        matched_topics = topics[:1]
    return PrepQuestionHint(
        question_id=question.id,
        topic_ids=[topic.id for topic in matched_topics],
        follow_up_hints=[
            _TOPIC_HINTS.get(topic.tags[0], _TOPIC_HINTS["general"])
            for topic in matched_topics
        ],
        evidence_titles=[topic.label for topic in matched_topics],
    )


def _topic_evidence_focus(tag: str) -> str:
    if tag == "redis":
        return "缓存一致性、穿透保护和高并发"
    if tag in {"mysql", "postgresql"}:
        return "索引设计、事务边界和慢查询优化"
    if tag in {"kafka", "rocketmq", "rabbitmq"}:
        return "消息可靠性、重试和削峰"
    if tag == "fastapi":
        return "接口设计、依赖注入和异步服务"
    if tag == "system-design":
        return "容量估算、故障隔离和服务演进"
    return "项目深度、工程实践和技术取舍"
