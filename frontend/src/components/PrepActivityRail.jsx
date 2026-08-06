import { BookOpenText, FileText, ListChecks } from "@phosphor-icons/react";

const ITEMS = [
  { id: "sources", label: "资料", Icon: FileText },
  { id: "plan", label: "蓝图", Icon: ListChecks },
  { id: "evidence", label: "证据", Icon: BookOpenText },
];

export function PrepActivityRail({ activePane, onSelect }) {
  return (
    <nav className="start-activity-rail start-prep-activity-rail" aria-label="准备工作区">
      {ITEMS.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          aria-controls={`prep-${id}-pane`}
          aria-pressed={activePane === id}
          onClick={() => onSelect(id)}
        >
          <span aria-hidden="true">
            <Icon size={18} weight={activePane === id ? "fill" : "bold"} focusable="false" />
          </span>
          <strong>{label}</strong>
        </button>
      ))}
    </nav>
  );
}
