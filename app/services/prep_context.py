from app.services.prep import InterviewPlan, PrepQuestionHint


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
