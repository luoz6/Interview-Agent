import {
  ArrowDown,
  ArrowUp,
  ArrowsClockwise,
  BookOpenText,
  CaretDown,
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
  const focusStatusId = `focus-status-${question.question_id}`;
  const actionHelpId = `action-help-${question.question_id}`;
  const exclusionHelp = question.required
    ? "该题已设为必考。请先选择“取消必考”，再排除该题。"
    : enabledCount <= 3
      ? "当前只剩 3 道启用题。请先重新启用一道已排除题，再排除本题。"
      : "";
  const enableHelp = !question.enabled && enabledCount >= 5
    ? "当前已启用 5 道题。请先排除一道非必考题，再重新启用本题。"
    : "";
  const actionHelp = question.enabled ? exclusionHelp : enableHelp;
  const evidenceSummary = `${sourceSignals.length} 个来源${evidenceIds.length ? ` · ${evidenceIds.length} 条证据` : " · 无额外证据"}`;

  return (
    <li
      className="start-plan-question"
      data-enabled={question.enabled}
      data-required={question.required}
      data-busy={questionBusy || undefined}
      aria-label={question.enabled ? `第 ${position} 题，共 ${enabledCount} 题` : "已排除题目"}
    >
      <div className="start-plan-question-index" aria-hidden="true">
        {question.enabled ? String(position).padStart(2, "0") : "—"}
      </div>

      <div className="start-plan-question-body">
        <div className="start-plan-question-head">
          <div className="start-plan-question-meta">
            <span className="start-plan-kind">{KIND_LABELS[question.kind] || question.kind}</span>
            {question.required && <span className="start-plan-required-label"><LockSimple size={13} weight="bold" aria-hidden="true" />必考</span>}
            {!question.enabled && <span className="start-plan-excluded-label">已排除</span>}
          </div>

          <div className="start-plan-question-actions" aria-label="题目操作">
            {question.enabled ? (
              <>
                <div className="start-plan-order-actions" role="group" aria-label="调整题目顺序">
                  <button
                    type="button"
                    className="start-plan-icon-action"
                    disabled={busy || position <= 1}
                    onClick={() => onPatch(
                      [{ type: "move", question_id: question.question_id, position: position - 1 }],
                      `第 ${position} 题已上移。`,
                      question.question_id,
                    )}
                    aria-label={`上移第 ${position} 题`}
                    title={position <= 1 ? "已经是第一题" : "上移一题"}
                  ><ArrowUp size={17} weight="bold" aria-hidden="true" /></button>
                  <button
                    type="button"
                    className="start-plan-icon-action"
                    disabled={busy || position >= enabledCount}
                    onClick={() => onPatch(
                      [{ type: "move", question_id: question.question_id, position: position + 1 }],
                      `第 ${position} 题已下移。`,
                      question.question_id,
                    )}
                    aria-label={`下移第 ${position} 题`}
                    title={position >= enabledCount ? "已经是最后一题" : "下移一题"}
                  ><ArrowDown size={17} weight="bold" aria-hidden="true" /></button>
                </div>
                <button
                  type="button"
                  className="start-plan-required-action"
                  aria-pressed={question.required}
                  disabled={busy}
                  onClick={() => onPatch(
                    [{ type: "set_required", question_id: question.question_id, required: !question.required }],
                    question.required ? `第 ${position} 题已取消必考。` : `第 ${position} 题已设为必考。`,
                    question.question_id,
                  )}
                ><LockSimple size={16} weight={question.required ? "fill" : "bold"} aria-hidden="true" /><span>{question.required ? "取消必考" : "设为必考"}</span></button>
                <button type="button" className="start-plan-regenerate-action" disabled={busy} aria-busy={questionBusy || undefined} onClick={() => onRegenerate(question.question_id, position)}>
                  {questionBusy
                    ? <SpinnerGap className="start-spinner" size={16} weight="bold" aria-hidden="true" />
                    : <ArrowsClockwise size={16} weight="bold" aria-hidden="true" />}
                  <span>{questionBusy ? "生成中" : "换一道"}</span>
                </button>
                <button
                  type="button"
                  className="start-plan-exclude-action"
                  disabled={busy || Boolean(exclusionHelp)}
                  aria-describedby={exclusionHelp ? actionHelpId : undefined}
                  title={exclusionHelp || "排除这道题"}
                  onClick={() => onPatch(
                    [{ type: "set_enabled", question_id: question.question_id, enabled: false }],
                    `第 ${position} 题已排除，当前启用 ${enabledCount - 1} 道题。`,
                    question.question_id,
                  )}
                ><EyeSlash size={16} weight="bold" aria-hidden="true" /><span>排除</span></button>
              </>
            ) : (
              <button
                type="button"
                className="start-plan-enable-action"
                disabled={busy || enabledCount >= 5}
                aria-describedby={enableHelp ? actionHelpId : undefined}
                title={enableHelp || "重新启用这道题"}
                onClick={() => onPatch(
                  [{ type: "set_enabled", question_id: question.question_id, enabled: true }],
                  `题目已重新启用并追加到第 ${enabledCount + 1} 题。`,
                  question.question_id,
                )}
              ><Eye size={16} weight="bold" aria-hidden="true" /><span>重新启用</span></button>
            )}
          </div>
        </div>

        {actionHelp && (
          <p id={actionHelpId} className="start-plan-action-help" role="note" tabIndex={0}>
            <strong>如何继续：</strong><span>{actionHelp}</span>
          </p>
        )}

        <h3>{question.prompt}</h3>

        <div className="start-plan-focus-editor">
          <div className="start-plan-focus-heading">
            <label htmlFor={focusId}><Target size={16} weight="bold" aria-hidden="true" />考察重点</label>
            <span id={focusStatusId} data-changed={focusChanged}>{focusChanged ? "有未保存修改" : "已保存"}</span>
          </div>
          <div className="start-plan-focus-control">
            <input
              id={focusId}
              value={focusValue}
              maxLength={120}
              disabled={busy || !question.enabled}
              aria-describedby={focusStatusId}
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
              <Check size={15} weight="bold" aria-hidden="true" /><span>{focusChanged ? "保存重点" : "已保存"}</span>
            </button>
          </div>
        </div>

        <details className="start-plan-question-evidence">
          <summary>
            <span className="start-plan-evidence-label"><BookOpenText size={16} weight="bold" aria-hidden="true" />来源与证据</span>
            <span className="start-plan-evidence-summary">{evidenceSummary}</span>
            <CaretDown className="start-plan-evidence-caret" size={15} weight="bold" aria-hidden="true" />
          </summary>
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
    </li>
  );
}
