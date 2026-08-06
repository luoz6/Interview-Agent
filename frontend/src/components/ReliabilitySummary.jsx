import { Info, ShieldCheck } from "@phosphor-icons/react";

export function ReliabilitySummary({ reliability, summary, reasonLabels = {} }) {
  return (
    <section
      className="report-detail-panel report-detail-reliability"
      aria-labelledby="report-reliability-title"
      data-tone={summary.tone}
      data-report-reveal
      style={{ "--reveal-order": 1 }}
    >
      <header className="report-detail-section-head">
        <div className="report-detail-section-heading-copy">
          <span className="report-detail-section-icon" aria-hidden="true"><ShieldCheck size={18} weight="duotone" /></span>
          <div><h2 id="report-reliability-title">报告可靠性</h2><p>{summary.description}</p></div>
        </div>
      </header>
      <span className="report-detail-reliability-state"><ShieldCheck size={15} weight="duotone" aria-hidden="true" />{summary.title}</span>
      {reliability ? (
        <>
          <dl className="report-detail-reliability-metrics">
            <div><dt>计划题数</dt><dd>{reliability.planned_question_count}</dd></div>
            <div><dt>有效回答</dt><dd>{reliability.answered_question_count}</dd></div>
            <div><dt>完成评审</dt><dd>{reliability.reviewed_answer_count}</dd></div>
            <div><dt>绑定证据</dt><dd>{reliability.evidence_bound_question_count}</dd></div>
            <div><dt>降级影响</dt><dd>{reliability.degraded_question_count}</dd></div>
          </dl>
          {reliability.degraded_reasons?.length > 0 && (
            <ul className="report-detail-reliability-reasons">
              {reliability.degraded_reasons.map((reason) => <li key={reason}>{reasonLabels[reason] || "部分报告依据受限"}</li>)}
            </ul>
          )}
        </>
      ) : (
        <p className="report-detail-compatibility-note"><Info size={17} weight="bold" aria-hidden="true" />这份旧报告没有可靠性字段。逐题内容仍可阅读，但总分不应被视为精确结论。</p>
      )}
    </section>
  );
}
