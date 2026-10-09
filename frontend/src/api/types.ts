// 与后端 schemas 对齐的 TS 类型（手写，不引 codegen）

export interface ApiEnvelope<T> {
  ok: boolean;
  data: T;
  warnings: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  research_topic: string;
  keywords: string[];
  created_at: string;
  paper_count: number;
}

export interface Author {
  name?: string;
  affiliation?: string;
  orcid?: string;
  is_corresponding?: boolean | null;
  h_index?: number | null;
}

export interface EvalSummary {
  composite_score: number | null;
  decision: string | null;
  tier: string | null;
  radar_scores: Record<string, number> | null;
  relevance_score?: number | null;
  citation_intent?: string | null;
}

export interface Paper {
  id: string;
  project_id: string;
  doi?: string | null;
  pmid?: string | null;
  title?: string | null;
  authors?: Author[] | null;
  journal?: string | null;
  publication_year?: number | null;
  cited_by_count?: number | null;
  citation_percentile?: number | null;
  impact_factor?: number | null;
  jcr_quartile?: string | null;
  cas_zone?: number | null;
  journal_percentile?: number | null;
  field_of_study?: string | null;
  abstract?: string | null;
  keywords?: string[] | null;
  status: string;
  has_fulltext?: boolean;
  missing_fields: string[];
  is_classic: boolean;
  is_retracted?: boolean;
  created_at: string;
  updated_at: string;
  evaluation?: EvalSummary | null;
}

export interface LLMAssessment {
  source: string;
  research_design: { score: number; detail: string };
  conclusion_reliability: { score: number; detail: string };
  content_quality?: { score: number; detail: string };
  citation_value: { intent: string; confidence?: number; detail: string };
  controversy: { detected: boolean; detail: string };
}

export interface EvaluationDetail {
  composite_score: number | null;
  weights: Record<string, number> | null;
  component_scores: Record<string, number> | null;
  radar_scores: Record<string, number> | null;
  confidence: number | null;
  abstract_structure: Record<string, unknown> | null;
  relevance: { score: number; method: string; topic?: string; basis?: string } | null;
  reproducibility: { score: number; flags: Record<string, boolean>; basis: string } | null;
  figure_analysis: {
    total_figures: number;
    tables: number;
    data_figures: number;
    schematic_figures: number;
    unclassified_figures: number;
    score: number | null;
    basis: string;
    figures: Array<{ number: string; caption: string; kind: "data" | "schematic" | "unknown" }>;
  } | null;
  llm_assessment: LLMAssessment | null;
  citation_intent: string | null;
  decision: string | null;
  tier: string | null;
  decision_reasons: string[];
  warnings: string[];
}

export interface Report {
  paper_id: string;
  title: string | null;
  doi: string | null;
  journal: string | null;
  publication_year: number | null;
  paper_summary: Record<string, unknown>;
  component_scores: Record<string, number>;
  radar_scores: Record<string, number>;
  composite_score: number | null;
  confidence: number | null;
  decision: string | null;
  decision_reasons: string[];
  content_eval: Record<string, unknown>;
  llm_assessment: LLMAssessment | null;
  citation_intent: string | null;
  warnings: string[];
  citation_formats: { apa?: string | null; mla?: string | null; gbt7714?: string | null };
  source_trace: Array<Record<string, unknown>>;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  message: string;
  name?: string;
  result?: unknown;
}

export interface SearchResult {
  doi: string | null;
  title: string | null;
  journal: string | null;
  publication_year: number | null;
  publication_date: string | null;
  authors: Author[] | null;
  abstract: string | null;
  keywords: string[] | null;
  cited_by_count: number | null;
  citation_percentile: number | null;
  field_of_study: string | null;
  work_type: string | null;
  openalex_work_id: string | null;
  open_access: Record<string, unknown> | null;
  relevance_score: number | null;
  relevance_method: string | null;
  already_imported: boolean;
  openalex_score: number | null;
}

export interface SearchResponse {
  query: string;
  total: number;
  results: SearchResult[];
  /** 实际发给 OpenAlex 的检索式（同义词扩展被 URL 长度切块时会有多段，取并集） */
  query_used?: string[];
  /** raw | expanded | expert_passthrough */
  query_mode?: string;
  query_note?: string;
  /** 多段并集时 total 为各段命中数之和（会重复计数），非并集真实命中数 */
  total_estimated?: boolean;
}


export interface CitationNetworkNode {
  id: string;
  title: string | null;
  cited_by_count: number;
  publication_year: number | null;
  role: "root" | "citing" | "reference";
  pagerank?: number;
  community?: number;
  is_textbook?: boolean;
  bibliographic_coupling?: number;
  co_cited_count?: number;
}

export interface CitationNetworkData {
  root: CitationNetworkNode;
  nodes: CitationNetworkNode[];
  edges: Array<{ source: string; target: string; type: string; weight: number }>;
  stats: {
    node_count: number;
    edge_count: number;
    community_count: number;
    root_pagerank: number;
    root_pagerank_rank: number;
    top_central: Array<{ title: string | null; role: string; pagerank: number; community: number }>;
    textbook_citations: CitationNetworkNode[];
    citing_with_bibliographic_coupling: Array<{ title: string | null; shared_references: number; pagerank: number }>;
    co_cited_references: Array<{ title: string | null; co_cited_count: number; is_textbook: boolean }>;
  };
}


export interface ProjectNetworkNode {
  id: string;
  title: string | null;
  cited_by_count: number;
  publication_year: number | null;
  role: "project_paper" | "reference";
  openalex_id?: string | null;
  pagerank?: number;
  degree?: number;
  community?: number;
  co_cited_count?: number;
  is_textbook?: boolean;
}

export interface ProjectNetworkData {
  nodes: ProjectNetworkNode[];
  edges: Array<{ source: string; target: string; type: "cites" | "coupling"; weight: number }>;
  stats: {
    paper_count: number;
    shared_reference_count: number;
    coupling_edge_count: number;
    community_count: number;
    core_references: Array<{ title: string | null; co_cited_count: number; cited_by_count: number; community: number; is_textbook: boolean }>;
    paper_ranking: Array<{ id: string; title: string | null; pagerank: number; degree: number; community: number }>;
    top_coupling_pairs: Array<{ a: string | null; b: string | null; shared_references: number }>;
    textbook_citations: ProjectNetworkNode[];
  };
}
