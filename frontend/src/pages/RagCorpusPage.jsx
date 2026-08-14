import { useCallback, useState } from "react";
import { Plus } from "@phosphor-icons/react";
import { CorpusEntryForm } from "../components/rag/CorpusEntryForm";
import { RagConsoleShell } from "../components/rag/RagConsoleShell";
import {
  IdentityValue,
  RagState,
  SectionHead,
  StatusPill,
} from "../components/rag/RagPrimitives";
import { usePageMeta } from "../hooks/usePageMeta";
import { getRagCorpus } from "../rag/ragApi";
import { displayStatus } from "../rag/ragDisplay";
import { useRagResource } from "../rag/useRagResource";
import "../styles/pages/rag-console.css";

export function RagCorpusPage() {
  usePageMeta({
    title: "知识语料",
    description: "知识语料的目录、治理与版本化新增工作区。",
    bodyClass: "start-page-body",
  });
  const loader = useCallback((options) => getRagCorpus(options), []);
  const resource = useRagResource(loader);
  return (
    <RagConsoleShell statusLabel="语料管理">
      <RagState resource={resource}>
        {(data) => <Corpus data={data} onRefresh={resource.refresh} />}
      </RagState>
    </RagConsoleShell>
  );
}

function Corpus({ data, onRefresh }) {
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(null);
  const [adding, setAdding] = useState(false);
  const query = filter.trim().toLowerCase();
  const units = data.units.filter(
    (unit) =>
      !query ||
      [
        unit.unit_id,
        unit.title,
        unit.domain,
        unit.topic,
        ...unit.tags,
        ...unit.aliases,
      ]
        .join(" ")
        .toLowerCase()
        .includes(query),
  );
  return (
    <>
      <header className="rag-page-head">
        <div>
          <p>版本化知识资料</p>
          <h1>知识语料</h1>
          <span>查看现有知识单元，并按“校验 → 预览 → 创建新版本”增加中文资料。</span>
        </div>
        <div className="rag-corpus-head-actions">
          <StatusPill value={data.write_enabled ? "available" : "not_recorded"}>{data.write_enabled ? "可新增" : "写入未启用"}</StatusPill>
          <button className="rag-primary" type="button" disabled={!data.write_enabled || adding} onClick={() => setAdding(true)} title={data.write_enabled ? undefined : "后端未启用本机语料写入能力"}>
            <Plus size={17} weight="bold" aria-hidden="true" />新增资料
          </button>
        </div>
      </header>
      <section className="rag-identity-strip">
        <IdentityValue label="语料版本" value={data.corpus_version} />
        <IdentityValue
          label="清单摘要"
          value={data.manifest_sha256}
          hash
        />
        <IdentityValue label="知识单元" value={data.chunk_count} />
        <IdentityValue
          label="激活状态"
          value={displayStatus(data.activation_status)}
        />
        <IdentityValue
          label="向量模型"
          value={`${data.embedding.provider} / ${data.embedding.model} / ${data.embedding.revision} / ${data.embedding.dimension}`}
        />
        <IdentityValue
          label="已退役版本"
          value={data.retired_versions.join(", ") || "未记录"}
        />
      </section>
      {adding && (
        <CorpusEntryForm
          corpus={data}
          onCancel={() => setAdding(false)}
          onCreated={() => onRefresh?.()}
        />
      )}
      <section className="rag-panel">
        <SectionHead
          eyebrow={`${adding ? "02" : "01"} · 语料目录`}
          title="知识单元"
          aside={
            <label className="rag-search">
              <span className="sr-only">筛选语料</span>
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="按领域、主题、别名或标签筛选…"
              />
            </label>
          }
        />
        <div className="rag-table-wrap">
          <table className="rag-table">
            <thead>
              <tr>
                <th>知识单元</th>
                <th>领域 / 主题</th>
                <th>别名 / 标签</th>
                <th>权威来源 / 审核</th>
                <th>版本 / 生命周期</th>
                <th>向量状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {units.map((unit) => (
                <tr key={unit.unit_id}>
                  <td>
                    <strong>{unit.title}</strong>
                    <code>{unit.unit_id}</code>
                  </td>
                  <td>
                    {unit.domain}
                    <small>{unit.topic}</small>
                  </td>
                  <td>
                    <small>{unit.aliases.join(" · ") || "无"}</small>
                    <small>{unit.tags.join(" · ")}</small>
                  </td>
                  <td>
                    {displayStatus(unit.source_authority)}
                    <small>{displayStatus(unit.review_status)}</small>
                  </td>
                  <td>
                    {unit.version}
                    <small>{displayStatus(unit.retirement_status)}</small>
                  </td>
                  <td>
                    <StatusPill value={unit.embedding_status} />
                  </td>
                  <td>
                    <button
                      className="rag-link-button"
                      type="button"
                      onClick={() => setActive(unit)}
                    >
                      查看安全详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {active && (
        <section className="rag-panel">
          <SectionHead
            eyebrow={`${adding ? "03" : "02"} · 安全详情`}
            title={active.title}
            aside={
              <button
                type="button"
                className="rag-secondary"
                onClick={() => setActive(null)}
              >
                关闭
              </button>
            }
          />
          <dl className="rag-detail-list">
            <IdentityValue label="单元 ID" value={active.unit_id} />
            <IdentityValue label="内容 SHA" value={active.content_sha256} />
            <IdentityValue
              label="领域 / 主题"
              value={`${active.domain} / ${active.topic}`}
            />
            <IdentityValue
              label="来源类型"
              value={displayStatus(active.source_type)}
            />
            <IdentityValue
              label="权威来源"
              value={displayStatus(active.source_authority)}
            />
            <IdentityValue
              label="审核状态"
              value={displayStatus(active.review_status)}
            />
            <IdentityValue label="版本" value={active.version} />
            <IdentityValue
              label="退役状态"
              value={displayStatus(active.retirement_status)}
            />
            <IdentityValue
              label="向量状态"
              value={displayStatus(active.embedding_status)}
            />
          </dl>
          <p className="rag-footnote">
            完整正文、来源网址与采集定位信息按安全策略不提供。
          </p>
        </section>
      )}
    </>
  );
}
