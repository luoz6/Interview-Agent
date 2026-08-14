import { useMemo, useState } from "react";
import { AsyncState } from "../AsyncState";
import { displayStatus, shortHash, toneFor } from "../../rag/ragDisplay";

export function RagState({ resource, empty = "暂无诊断数据。", children }) {
  if (resource.status === "loading" || resource.status === "idle") {
    return (
      <AsyncState
        className="rag-state"
        title="读取诊断事实"
        description="正在验证功能开关与后端安全边界。"
      />
    );
  }
  if (resource.status === "error") {
    return (
      <AsyncState
        className="rag-state"
        tone="error"
        role="alert"
        title="诊断接口不可用"
        description={resource.error?.message || "当前环境未开放此能力。"}
        action={
          <button type="button" onClick={resource.refresh}>
            重试
          </button>
        }
      />
    );
  }
  if (!resource.data)
    return (
      <AsyncState className="rag-state" title="没有数据" description={empty} />
    );
  return children(resource.data);
}

export function StatusPill({ value, children }) {
  const text = children || displayStatus(value);
  const tone = toneFor(value);
  const symbols = { success: "✓", warning: "!", danger: "×", neutral: "•" };
  return (
    <span className="rag-pill" data-tone={tone}>
      <span aria-hidden="true">{symbols[tone]}</span>
      {text}
    </span>
  );
}

export function CopyButton({ value, label }) {
  const [status, setStatus] = useState("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(String(value));
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
  };
  return (
    <button
      className="rag-copy-button"
      type="button"
      onClick={copy}
      disabled={value == null || value === ""}
      aria-label={`复制${label}`}
    >
      <span aria-live="polite">
        {status === "copied"
          ? "已复制"
          : status === "failed"
            ? "复制失败"
            : "复制"}
      </span>
    </button>
  );
}

export function IdentityValue({ label, value, hash = false, copy }) {
  const rendered = hash ? shortHash(value) : (value ?? "—");
  const copyable = copy ?? /(?:\bID\b|SHA|manifest|revision)/i.test(label);
  return (
    <div className="rag-identity-value">
      <dt>{label}</dt>
      <dd title={String(value ?? "")}>
        <span>{rendered}</span>
        {copyable && (
          <CopyButton key={String(value)} value={value} label={label} />
        )}
      </dd>
    </div>
  );
}

export function SectionHead({ eyebrow, title, aside }) {
  return (
    <header className="rag-section-head">
      <div>
        <p>{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {aside}
    </header>
  );
}

const sortOptions = [
  ["final", "最终证据"],
  ["semantic_rank", "语义排名"],
  ["semantic_score", "语义分数"],
  ["lexical_rank", "词法排名"],
  ["lexical_score", "词法分数"],
  ["fusion_rank", "融合排名"],
  ["fusion_score", "融合分数"],
  ["rerank_rank", "重排排名"],
  ["rerank_score", "重排分数"],
];

function nullLast(left, right, direction) {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  return direction * (Number(left) - Number(right));
}

export function CandidateTable({
  candidates = [],
  onInspect,
  activeCandidateId = null,
}) {
  const [sortBy, setSortBy] = useState("final");
  const sorted = useMemo(
    () =>
      [...candidates].sort((left, right) => {
        if (sortBy === "final") {
          return (
            Number(right.selected) - Number(left.selected) ||
            nullLast(left.rerank_rank, right.rerank_rank, 1) ||
            nullLast(left.fusion_rank, right.fusion_rank, 1) ||
            left.candidate_id.localeCompare(right.candidate_id)
          );
        }
        const direction = sortBy.endsWith("_score") ? -1 : 1;
        return (
          nullLast(left[sortBy], right[sortBy], direction) ||
          left.candidate_id.localeCompare(right.candidate_id)
        );
      }),
    [candidates, sortBy],
  );

  return (
    <>
      <div className="rag-table-tools">
        <label>
          <span>候选排序</span>
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value)}
          >
            {sortOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="rag-table-wrap">
        <table className="rag-table">
          <thead>
            <tr>
              <th>候选内容</th>
              <th>语义检索</th>
              <th>词法检索</th>
              <th>融合</th>
              <th>重排</th>
              <th>最终结果</th>
              <th>原因</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => (
              <tr key={item.candidate_id}>
                <td>
                  <strong>{item.title}</strong>
                  <span className="rag-copy-line">
                    <code>{item.candidate_id}</code>
                    <CopyButton
                      value={item.candidate_id}
                      label={`候选 ID ${item.candidate_id}`}
                    />
                  </span>
                  <small>
                    {item.domain} / {item.topic || "—"}
                  </small>
                  <p>{item.safe_excerpt}</p>
                </td>
                <RankCell
                  rank={item.semantic_rank}
                  score={item.semantic_score}
                />
                <RankCell
                  rank={item.lexical_rank}
                  score={item.lexical_score}
                  extra={item.matched_terms?.join(", ")}
                />
                <RankCell rank={item.fusion_rank} score={item.fusion_score} />
                <RankCell rank={item.rerank_rank} score={item.rerank_score} />
                <td>
                  <StatusPill
                    value={item.selected ? "available" : "not_evaluated"}
                  >
                    {item.selected ? "已选用" : "未选用"}
                  </StatusPill>
                </td>
                <td>
                  <small>
                    {item.ranking_explanation?.reason_codes?.join(" · ") ||
                      "未记录"}
                  </small>
                </td>
                <td>
                  {onInspect && (
                    <button
                      className="rag-link-button"
                      type="button"
                      aria-expanded={activeCandidateId === item.candidate_id}
                      onClick={(event) => onInspect(item, event.currentTarget)}
                    >
                      查看解释
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!candidates.length && (
          <p className="rag-empty-row">没有记录候选项。</p>
        )}
      </div>
    </>
  );
}

function RankCell({ rank, score, extra }) {
  return (
    <td>
      <strong>{rank == null ? "—" : `#${rank}`}</strong>
      <small>{score == null ? "未记录" : Number(score).toFixed(5)}</small>
      {extra && <em>{extra}</em>}
    </td>
  );
}
