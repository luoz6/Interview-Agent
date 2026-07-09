from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import DimensionScores, InterviewFeedback


def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a backend project.",
                focus="project depth",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            ),
        ],
    )


def make_single_question_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a backend project.",
                focus="project depth",
            )
        ],
    )


def make_feedback(*, question_id: str, score: int = 80) -> InterviewFeedback:
    question_text = {
        "q1": "Introduce a backend project.",
        "q2": "Explain Redis cache invalidation.",
    }[question_id]
    return InterviewFeedback(
        question_id=question_id,
        question_text=question_text,
        user_answer=f"\u5019\u9009\u4eba\u56de\u7b54\u4e86 {question_id} \u7684\u6838\u5fc3\u601d\u8def\u3002",
        answer_state="answered",
        score=score,
        dimension_scores=make_dimension_scores(score),
        rationale=f"{question_id} \u7684\u56de\u7b54\u8986\u76d6\u4e86\u6838\u5fc3\u94fe\u8def\u548c\u4e3b\u8981\u53d6\u820d\u3002",
        critique=f"{question_id} \u7684\u56de\u7b54\u8fd8\u9700\u8981\u8865\u5145\u8fb9\u754c\u6761\u4ef6\u548c\u91cf\u5316\u7ed3\u679c\u3002",
        better_answer=f"{question_id} \u53ef\u4ee5\u8865\u5145\u6545\u969c\u515c\u5e95\u3001\u76d1\u63a7\u6307\u6807\u548c\u6027\u80fd\u6570\u636e\u3002",
        references=[],
    )


def completed_record(session_id: str, question_id: str, score: int = 80):
    return question_evaluation_from_feedback(
        session_id=session_id,
        feedback=make_feedback(question_id=question_id, score=score),
    )
