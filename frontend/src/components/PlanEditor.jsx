import { useEffect, useMemo, useState } from "react";
import { PlanQuestionCard } from "./PlanQuestionCard";

function orderedQuestions(questions) {
  const enabled = questions
    .filter((question) => question.enabled)
    .sort((left, right) => left.position - right.position);
  const excluded = questions.filter((question) => !question.enabled);
  return [...enabled, ...excluded];
}

export function PlanEditor({ plan, busy = false, activeQuestionId = "", onPatch, onRegenerate }) {
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
        <div>
          <span className="start-plan-editor-kicker">第 {plan.plan_version} 版计划</span>
          <h2 id="plan-editor-title">{plan.title}</h2>
          <p>启用 {enabledCount} 道题。调整范围、顺序和重点后，本版本会直接用于面试。</p>
        </div>
        <div className="start-plan-editor-count" aria-label={`已启用 ${enabledCount} 道题`}>
          <strong>{enabledCount}</strong><span>道启用题</span>
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
