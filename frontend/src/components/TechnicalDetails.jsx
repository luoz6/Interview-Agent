import { ListChecks } from "@phosphor-icons/react";

export function TechnicalDetails({ className = "", summary = "技术详情", children }) {
  return (
    <details className={className || undefined}>
      <summary><ListChecks size={16} weight="duotone" aria-hidden="true" />{summary}</summary>
      {children}
    </details>
  );
}
