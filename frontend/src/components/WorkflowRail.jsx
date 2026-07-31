const steps = [
  ["面试准备", "输入 JD 与简历，生成计划"],
  ["模拟面试", "AI 动态提问与流式追问"],
  ["报告生成", "异步评审与证据聚合"],
  ["面试复盘", "阅读评分与行动建议"],
];

export function WorkflowRail({ current = 1, title = "面试流程", note }) {
  return (
    <aside className="workflow-rail" aria-label="面试流程">
      <p className="rail-label">{title}</p>
      <ol>
        {steps.map(([name, description], index) => {
          const step = index + 1;
          const state = step < current ? "done" : step === current ? "current" : "pending";
          const stateLabel = state === "done" ? "已完成" : state === "current" ? "当前步骤" : "待进行";
          return (
            <li key={name} data-state={state} aria-current={state === "current" ? "step" : undefined}>
              <span className="rail-number">{String(step).padStart(2, "0")}</span>
              <span><strong>{name}</strong><small>{description}</small><em>{stateLabel}</em></span>
            </li>
          );
        })}
      </ol>
      {note && <div className="rail-note">{note}</div>}
    </aside>
  );
}
