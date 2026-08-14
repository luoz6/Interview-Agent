export const metricLabels = {
  recall_at_5: "Recall@5",
  mrr_at_5: "MRR@5",
  ndcg_at_5: "NDCG@5",
  hit_at_1: "Hit@1",
  filter_correctness_rate: "筛选正确率",
  no_evidence_precision: "无证据精确率",
  no_evidence_recall: "无证据召回率",
  no_evidence_f1: "无证据 F1",
  evidence_precision_at_5: "证据 P@5",
  evidence_replay_stability_rate: "回放稳定率",
  domain_routing_accuracy: "领域路由准确率",
  topic_routing_accuracy: "主题路由准确率",
  p95_latency_ms: "P95 延迟",
};

const statusLabels = {
  available: "可用",
  unavailable: "不可用",
  not_evaluated: "未评估",
  not_recorded: "未记录",
  passed: "通过",
  allowed: "允许",
  failed: "未通过",
  blocked: "已阻断",
  hard_stop: "硬性阻断",
  degraded: "降级",
  weak: "较弱",
  possible_conflict: "可能冲突",
  confirmed_conflict: "确认冲突",
  insufficient: "证据不足",
  medium: "中等",
  partial_historical: "历史数据不完整",
  full_snapshot: "完整快照",
  pending: "待处理",
  warning: "警告",
  sufficient: "证据充分",
  consistent: "一致",
  high: "高",
  active: "已激活",
  inactive: "未激活",
  machine_preannotation: "机器预标注",
  historical_diagnostic: "历史诊断",
  current: "当前诊断",
  historical_compatible: "历史兼容",
  diagnostic: "诊断",
  curated_machine_assisted: "Curated / Machine-assisted",
  engineering_comparison: "工程对比",
  demo_diagnostic_dataset: "Demo Diagnostic Dataset",
  not_applicable: "不适用",
  live: "实时诊断",
  live_diagnostic: "实时诊断",
  artifact_replay: "冻结制品回放",
  tuning: "调优集",
  holdout: "最终诊断集",
  theory: "理论资料",
  engineering_guide: "工程指南",
  expert_benchmark: "专家基准",
  schema_validated: "结构已校验",
  official_cn: "官方中文来源",
  secondary_cn: "中文二手来源",
  official: "官方资料",
  internal: "内部资料",
};

const fieldLabels = {
  retrieval: "检索服务",
  fusion: "融合策略",
  reranker: "重排器",
  evidence_gate: "证据门禁",
  taxonomy: "分类体系",
  console_read: "控制台读取",
  live_execution: "实时执行",
  corpus_write: "语料写入",
  label: "数据集名称",
  curation: "整理方式",
  tuning_case_count: "调优案例数",
  diagnostic_case_count: "诊断案例数",
  production_claim: "生产结论",
  benchmark_type: "Benchmark 类型",
  label_source: "标签来源",
  purpose: "用途",
  diagnostic_status: "诊断状态",
  semantic_enabled: "语义通道",
  lexical_enabled: "词法通道",
  fusion_strategy: "融合策略",
  semantic_weight: "语义权重",
  lexical_weight: "词法权重",
  semantic_candidate_limit: "语义候选上限",
  lexical_candidate_limit: "词法候选上限",
  fusion_candidate_limit: "融合候选上限",
  rerank_candidate_limit: "重排候选上限",
  evidence_limit: "最终证据上限",
  semantic_timeout_ms: "语义超时",
  lexical_timeout_ms: "词法超时",
  rerank_timeout_ms: "重排超时",
  total_timeout_ms: "总超时",
  availability: "可用性",
  sufficiency: "充分性",
  consistency: "一致性",
  evaluation_confidence: "评估置信度",
  semantic: "语义检索",
  lexical: "词法检索",
  total: "总耗时",
  mode: "模式",
  engine: "检索引擎",
  profile: "检索配置",
  trace_schema: "Trace 结构版本",
  query_hash: "问题摘要",
  query_sha256: "问题摘要",
  character_count: "字符数",
  detected_domain: "识别领域",
  detected_topic: "识别主题",
  routing_domain: "路由领域",
  routing_topic: "路由主题",
  covered_signals: "已覆盖信号",
  missing_signals: "缺失信号",
  reason_codes: "原因代码",
  gate_version: "门禁版本",
  base_score_source: "基础分数来源",
  base_score: "基础分数",
  exact_term_boost: "精确词加分",
  routing_tag_boost: "路由标签加分",
  eligibility_score: "资格分数",
  eligible: "满足资格",
  final_rerank_score: "最终重排分数",
  tie_break_fusion_rank: "融合排名决胜位",
  artifact_sha256: "制品 SHA",
  corpus_manifest_sha256: "语料清单 SHA",
  code_revision: "代码版本",
  code_tree_sha256: "代码树 SHA",
  profile_id: "检索配置 ID",
  profile_version: "检索配置版本",
  profile_sha256: "检索配置 SHA",
  semantic_rank: "语义排名",
  semantic_score: "语义分数",
  lexical_rank: "词法排名",
  lexical_score: "词法分数",
  fusion_rank: "融合排名",
  fusion_score: "融合分数",
  rerank_rank: "重排排名",
  rerank_score: "重排分数",
  channel_hits: "命中通道",
  matched_terms: "命中词",
};

const caseTypeLabels = {
  alias_only: "仅别名",
  semantic_paraphrase: "语义改写",
  chinese_paraphrase: "中文改写",
  weak_keyword: "弱关键词",
  ambiguous: "歧义问题",
  hard_negative: "强负例",
  cross_domain: "跨领域",
  no_evidence: "无证据",
};

export function displayStatus(value) {
  return statusLabels[value] || value || "不可用";
}

export function displayFieldLabel(value) {
  return fieldLabels[value] || value;
}

export function displayCaseType(value) {
  return caseTypeLabels[value] ? `${caseTypeLabels[value]}（${value}）` : value;
}

export function shortHash(value = "") {
  return value ? `${value.slice(0, 8)}…${value.slice(-6)}` : "—";
}
export function formatMetric(name, value) {
  if (value == null) return "—";
  if (name.endsWith("latency_ms")) return `${Number(value).toFixed(1)} ms`;
  return `${(Number(value) * 100).toFixed(1)}%`;
}
export function toneFor(value) {
  if (["passed", "allowed"].includes(value)) return "success";
  if (
    [
      "degraded",
      "weak",
      "possible_conflict",
      "medium",
      "partial_historical",
      "pending",
      "warning",
    ].includes(value)
  )
    return "warning";
  if (
    [
      "failed",
      "insufficient",
      "confirmed_conflict",
      "blocked",
      "hard_stop",
    ].includes(value)
  )
    return "danger";
  return "neutral";
}
