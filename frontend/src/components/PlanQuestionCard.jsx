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

export function PlanQuestionCard({
  question,
  plan,
  busy,
  questionBusy,
  enabledCount,
  focusValue,
  onFocusChange,
  onPatch,
  onRegenerate,
}) {
  const position = question.enabled ? question.position : null;
  const focusChanged = focusValue.trim() !== question.focus;
  const sourceSignals = question.source_signals?.length ? question.source_signals : ["jd", "resume"];
  const evidenceIds = question.evidence_ids || [];
  const topicLabels = question.topic_labels || [];
  const focusId = `focus-${question.question_id}`;
  const requiredHelpId = `required-help-${question.question_id}`;

  return (
    <li
      className="start-plan-question"
      data-enabled={question.enabled}
      data-required={question.required}
      aria-label={question.enabled ? `第 ${position} 题，共 ${enabledCount} 题` : "已排除题目"}
    >
      <div className="start-plan-question-index" aria-hidden="true">
        {question.enabled ? String(position).padStart(2, "0") : "—"}
      </div>

      <div className="start-plan-question-body">
        <div className="start-plan-question-meta">
          <span className="start-plan-kind">{KIND_LABELS[question.kind] || question.kind}</span>
          {question.required && <span className="start-plan-required-label"><LockSimple size={13} weight="bold" aria-hidden="true" />必考</span>}
          {!question.enabled && <span className="start-plan-excluded-label">已排除</span>}
        </div>

        <h3>{question.prompt}</h3>

        <div className="start-plan-focus-editor">
          <label htmlFor={focusId}><Target size={16} weight="bold" aria-hidden="true" />考察重点</label>
          <div>
            <input
              id={focusId}
              value={focusValue}
              maxLength={120}
              disabled={busy || !question.enabled}
              onChange={(event) => onFocusChange(event.target.value)}
            />
            <button
              type="button"
              className="start-plan-compact-action"
              disabled={busy || !question.enabled || !focusChanged || !focusValue.trim()}
              onClick={() => onPatch(
                [{ type: "set_focus", question_id: question.question_id, focus: focusValue.trim() }],
                `${position ? `第 ${position} 题` : "该题"}考察重点已保存。`,
                question.question_id,
              )}
            >
              <Check size={15} weight="bold" aria-hidden="true" />保存重点
            </button>
          </div>
        </div>

        <details className="start-plan-question-evidence">
          <summary><BookOpenText size={16} weight="bold" aria-hidden="true" />来源与证据</summary>
          <div className="start-plan-evidence-content">
            <div className="start-plan-source-signals" aria-label="题目来源">
              {sourceSignals.map((source) => <span key={source}>{SOURCE_LABELS[source] || source}</span>)}
            </div>
            {topicLabels.length > 0 && <p><strong>覆盖主题：</strong>{topicLabels.join("、")}</p>}
            {evidenceIds.length > 0 ? (
              <ul aria-label="绑定证据">
                {evidenceIds.map((evidenceId) => <li key={evidenceId}><code>{evidenceId}</code></li>)}
              </ul>
            ) : (
              <p className="start-plan-evidence-fallback">
                {plan.prep_context?.knowledge_status === "degraded"
                  ? "知识证据不可用，但可继续使用岗位 JD 与候选人经历生成题目。"
                  : "本题基于岗位 JD 与候选人经历生成，没有绑定额外知识证据。"}
              </p>
            )}
          </div>
        </details>
      </div>

      <div className="start-plan-question-actions" aria-label="题目操作">
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
            ><ArrowUp size={17} weight="bold" aria-hidden="true" /><span>上移</span></button>
            <button
              type="button"
              disabled={busy || position >= enabledCount}
              onClick={() => onPatch(
                [{ type: "move", question_id: question.question_id, position: position + 1 }],
                `第 ${position} 题已下移。`,
                question.question_id,
              )}
              aria-label={`下移第 ${position} 题`}
            ><ArrowDown size={17} weight="bold" aria-hidden="true" /><span>下移</span></button>
            <button
              type="button"
              aria-pressed={question.required}
              disabled={busy}
              onClick={() => onPatch(
                [{ type: "set_required", question_id: question.question_id, required: !question.required }],
                question.required ? `第 ${position} 题已取消必考。` : `第 ${position} 题已设为必考。`,
                question.question_id,
              )}
            ><LockSimple size={17} weight={question.required ? "fill" : "bold"} aria-hidden="true" /><span>{question.required ? "取消必考" : "设为必考"}</span></button>
            <button type="button" disabled={busy} onClick={() => onRegenerate(question.question_id, position)}>
              {questionBusy
                ? <SpinnerGap className="start-spinner" size={17} weight="bold" aria-hidden="true" />
                : <ArrowsClockwise size={17} weight="bold" aria-hidden="true" />}
              <span>{questionBusy ? "生成中" : "换一道"}</span>
            </button>
            <button
              type="button"
              className="start-plan-exclude-action"
              disabled={busy || question.required || enabledCount <= 3}
              aria-describedby={question.required ? requiredHelpId : undefined}
              onClick={() => onPatch(
                [{ type: "set_enabled", question_id: question.question_id, enabled: false }],
                `第 ${position} 题已排除，当前启用 ${enabledCount - 1} 道题。`,
                question.question_id,
              )}
            ><EyeSlash size={17} weight="bold" aria-hidden="true" /><span>排除</span></button>
            {question.required && <span id={requiredHelpId} className="sr-only">请先取消必考，再排除该题。</span>}
          </>
        ) : (
          <button
            type="button"
            className="start-plan-enable-action"
            disabled={busy || enabledCount >= 5}
            onClick={() => onPatch(
              [{ type: "set_enabled", question_id: question.question_id, enabled: true }],
              `题目已重新启用并追加到第 ${enabledCount + 1} 题。`,
              question.question_id,
            )}
          ><Eye size={17} weight="bold" aria-hidden="true" /><span>重新启用</span></button>
        )}
      </div>
    </li>
  );
}
