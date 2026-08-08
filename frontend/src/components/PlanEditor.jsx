import { useEffect, useMemo, useState } from "react";
import { CheckCircle } from "@phosphor-icons/react";
import { PlanQuestionCard } from "./PlanQuestionCard";

const TAG_LABELS = {
  general: "通用",
};

function orderedQuestions(questions) {
  const enabled = questions
    .filter((question) => question.enabled)
    .sort((left, right) => left.position - right.position);
  const excluded = questions.filter((question) => !question.enabled);
  return [...enabled, ...excluded];
}

export function PlanEditor({ plan, tags = [], busy = false, activeQuestionId = "", onPatch, onRegenerate }) {
  const [focusDrafts, setFocusDrafts] = useState({});
  const questions = useMemo(() => orderedQuestions(plan?.questions || []), [plan?.questions]);
  const enabledCount = questions.filter((question) => question.enabled).length;

  useEffect(() => {
    setFocusDrafts(Object.fromEntries(
      (plan?.questions || []).map((question) => [question.question_id, question.focus]),
    ));
  }, [plan?.plan_version, plan?.questions]);

  if (!plan) return null;

  return (
    <section className="start-plan-editor" aria-labelledby="plan-editor-title">
      <header className="start-plan-editor-heading">
        <div className="start-plan-editor-heading-copy">
          <div className="start-plan-editor-meta" aria-label="蓝图元数据">
            <span className="start-plan-editor-version">v{plan.plan_version} 当前版本</span>
            {tags.map((tag) => <span key={tag} className="start-plan-editor-tag">{TAG_LABELS[tag] || tag}</span>)}
          </div>
          <h2 id="plan-editor-title">{plan.title}</h2>
          <p>按实际面试顺序检查题目；这里保存的范围、重点与证据会直接用于本次面试。</p>
        </div>
        <div className="start-plan-editor-count" aria-label={`当前已启用 ${enabledCount} 道题`}>
          <CheckCircle size={18} weight="fill" aria-hidden="true" />
          <strong>{enabledCount}</strong><span>道题已启用</span>
        </div>
      </header>

      <ol className="start-plan-editor-list">
        {questions.map((question) => (
          <PlanQuestionCard
            key={question.question_id}
            question={question}
            plan={plan}
            busy={busy}
            questionBusy={busy && activeQuestionId === question.question_id}
            enabledCount={enabledCount}
            focusValue={focusDrafts[question.question_id] ?? question.focus}
            onFocusChange={(value) => setFocusDrafts((current) => ({ ...current, [question.question_id]: value }))}
            onPatch={onPatch}
            onRegenerate={onRegenerate}
          />
        ))}
      </ol>
    </section>
  );
}
