import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowsClockwise,
  BookOpenText,
  Check,
  Eye,
  EyeSlash,
  LockSimple,
  SpinnerGap,
  Target,
} from "@phosphor-icons/react";

const KIND_LABELS = {
  project: "项目经历",
  technical: "技术能力",
  "system-design": "系统设计",
  system_design: "系统设计",
  behavioral: "行为问题",
};

const SOURCE_LABELS = {
  jd: "岗位 JD",
  resume: "候选人经历",
  knowledge: "知识证据",
};

function orderedQuestions(questions) {
  const enabled = questions
    .filter((question) => question.enabled)
    .sort((left, right) => left.position - right.position);
  const excluded = questions.filter((question) => !question.enabled);
  return [...enabled, ...excluded];
}

export function PlanEditor({
  plan,
  busy = false,
  activeQuestionId = "",
  onPatch,
  onRegenerate,
}) {
  const [focusDrafts, setFocusDrafts] = useState({});
  const questions = useMemo(
    () => orderedQuestions(plan?.questions || []),
    [plan?.questions],
  );
  const enabledCount = questions.filter((question) => question.enabled).length;

  useEffect(() => {
    setFocusDrafts(
      Object.fromEntries(
        (plan?.questions || []).map((question) => [
          question.question_id,
          question.focus,
        ]),
      ),
    );
  }, [plan?.plan_version, plan?.questions]);

  if (!plan) return null;

  return (
    <section className="plan-editor" aria-labelledby="plan-editor-title">
      <header className="plan-editor-heading">
        <div>
          <span className="plan-editor-kicker">第 {plan.plan_version} 版计划</span>
          <h2 id="plan-editor-title">{plan.title}</h2>
          <p>启用 {enabledCount} 道题。调整范围、顺序和重点后，本版本会直接用于面试。</p>
        </div>
        <div className="plan-editor-count" aria-label={`已启用 ${enabledCount} 道题`}>
          <strong>{enabledCount}</strong>
          <span>道启用题</span>
        </div>
      </header>

      <ol className="plan-editor-list">
        {questions.map((question) => {
          const position = question.enabled ? question.position : null;
          const focusValue = focusDrafts[question.question_id] ?? question.focus;
          const focusChanged = focusValue.trim() !== question.focus;
          const questionBusy = busy && activeQuestionId === question.question_id;
          const sourceSignals = question.source_signals?.length
            ? question.source_signals
            : ["jd", "resume"];
          const evidenceIds = question.evidence_ids || [];
          const topicLabels = question.topic_labels || [];
          const focusId = `focus-${question.question_id}`;
          const requiredHelpId = `required-help-${question.question_id}`;

          return (
            <li
              key={question.question_id}
              className="plan-question"
              data-enabled={question.enabled}
              data-required={question.required}
              aria-label={
                question.enabled
                  ? `第 ${position} 题，共 ${enabledCount} 题`
                  : "已排除题目"
              }
            >
              <div className="plan-question-index" aria-hidden="true">
                {question.enabled ? String(position).padStart(2, "0") : "—"}
              </div>

              <div className="plan-question-body">
                <div className="plan-question-meta">
                  <span className="plan-kind">{KIND_LABELS[question.kind] || question.kind}</span>
                  {question.required ? (
                    <span className="plan-required-label">
                      <LockSimple size={13} weight="bold" aria-hidden="true" />必考
                    </span>
                  ) : null}
                  {!question.enabled ? <span className="plan-excluded-label">已排除</span> : null}
                </div>

                <h3>{question.prompt}</h3>

                <div className="plan-focus-editor">
                  <label htmlFor={focusId}>
                    <Target size={16} weight="bold" aria-hidden="true" />
                    考察重点
                  </label>
                  <div>
                    <input
                      id={focusId}
                      value={focusValue}
                      maxLength={120}
                      disabled={busy || !question.enabled}
                      onChange={(event) => {
                        const value = event.target.value;
                        setFocusDrafts((current) => ({
                          ...current,
                          [question.question_id]: value,
                        }));
                      }}
                    />
                    <button
                      type="button"
                      className="plan-compact-action"
                      disabled={busy || !question.enabled || !focusChanged || !focusValue.trim()}
                      onClick={() => onPatch(
                        [
                          {
                            type: "set_focus",
                            question_id: question.question_id,
                            focus: focusValue.trim(),
                          },
                        ],
                        `${position ? `第 ${position} 题` : "该题"}考察重点已保存。`,
                        question.question_id,
                      )}
                    >
                      <Check size={15} weight="bold" aria-hidden="true" />保存重点
                    </button>
                  </div>
                </div>

                <details className="plan-question-evidence">
                  <summary>
                    <BookOpenText size={16} weight="bold" aria-hidden="true" />
                    来源与证据
                  </summary>
                  <div className="plan-evidence-content">
                    <div className="plan-source-signals" aria-label="题目来源">
                      {sourceSignals.map((source) => (
                        <span key={source}>{SOURCE_LABELS[source] || source}</span>
                      ))}
                    </div>
                    {topicLabels.length ? (
                      <p><strong>覆盖主题：</strong>{topicLabels.join("、")}</p>
                    ) : null}
                    {evidenceIds.length ? (
                      <ul aria-label="绑定证据">
                        {evidenceIds.map((evidenceId) => <li key={evidenceId}><code>{evidenceId}</code></li>)}
                      </ul>
                    ) : (
                      <p className="plan-evidence-fallback">
                        {plan.prep_context?.knowledge_status === "degraded"
                          ? "知识证据不可用，但可继续使用岗位 JD 与候选人经历生成题目。"
                          : "本题基于岗位 JD 与候选人经历生成，没有绑定额外知识证据。"}
                      </p>
                    )}
                  </div>
                </details>
              </div>

              <div className="plan-question-actions" aria-label="题目操作">
                {question.enabled ? (
                  <>
                    <button
                      type="button"
                      disabled={busy || position <= 1}
                      onClick={() => onPatch(
                        [{ type: "move", question_id: question.question_id, position: position - 1 }],
                        `第 ${position} 题已上移。`,
                        question.question_id,
                      )}
                      aria-label={`上移第 ${position} 题`}
                    >
                      <ArrowUp size={17} weight="bold" aria-hidden="true" /><span>上移</span>
                    </button>
                    <button
                      type="button"
                      disabled={busy || position >= enabledCount}
                      onClick={() => onPatch(
                        [{ type: "move", question_id: question.question_id, position: position + 1 }],
                        `第 ${position} 题已下移。`,
                        question.question_id,
                      )}
                      aria-label={`下移第 ${position} 题`}
                    >
                      <ArrowDown size={17} weight="bold" aria-hidden="true" /><span>下移</span>
                    </button>
                    <button
                      type="button"
                      aria-pressed={question.required}
                      disabled={busy}
                      onClick={() => onPatch(
                        [{ type: "set_required", question_id: question.question_id, required: !question.required }],
                        question.required ? `第 ${position} 题已取消必考。` : `第 ${position} 题已设为必考。`,
                        question.question_id,
                      )}
                    >
                      <LockSimple size={17} weight={question.required ? "fill" : "bold"} aria-hidden="true" />
                      <span>{question.required ? "取消必考" : "设为必考"}</span>
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onRegenerate(question.question_id, position)}
                    >
                      {questionBusy
                        ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" />
                        : <ArrowsClockwise size={17} weight="bold" aria-hidden="true" />}
                      <span>{questionBusy ? "生成中" : "换一道"}</span>
                    </button>
                    <button
                      type="button"
                      className="plan-exclude-action"
                      disabled={busy || question.required || enabledCount <= 3}
                      aria-describedby={question.required ? requiredHelpId : undefined}
                      onClick={() => onPatch(
                        [{ type: "set_enabled", question_id: question.question_id, enabled: false }],
                        `第 ${position} 题已排除，当前启用 ${enabledCount - 1} 道题。`,
                        question.question_id,
                      )}
                    >
                      <EyeSlash size={17} weight="bold" aria-hidden="true" /><span>排除</span>
                    </button>
                    {question.required ? (
                      <span id={requiredHelpId} className="sr-only">请先取消必考，再排除该题。</span>
                    ) : null}
                  </>
                ) : (
                  <button
                    type="button"
                    className="plan-enable-action"
                    disabled={busy || enabledCount >= 5}
                    onClick={() => onPatch(
                      [{ type: "set_enabled", question_id: question.question_id, enabled: true }],
                      `题目已重新启用并追加到第 ${enabledCount + 1} 题。`,
                      question.question_id,
                    )}
                  >
                    <Eye size={17} weight="bold" aria-hidden="true" /><span>重新启用</span>
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
