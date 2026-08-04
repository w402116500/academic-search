export type WorkflowStage =
  | "draft"
  | "analyzing"
  | "plan_review"
  | "retrieving"
  | "screening"
  | "collection_building"
  | "researching"
  | "failed";

export type PlanStatus = "generating" | "ready" | "confirmed" | "failed" | "superseded";
export type SearchRunStatus =
  "queued" | "running" | "completed" | "partial_failed" | "failed" | "cancelled" | "expired";
export type SearchRunStage =
  | "dispatch"
  | "provider_search"
  | "normalize"
  | "triage"
  | "relevance_assessment"
  | "citation_enrichment"
  | "completed";
export type FulltextStatus =
  "queued" | "downloading" | "validating" | "available" | "failed" | "rejected" | "requires_upload";
export type CitationFormat =
  "gb_t_7714_2015_numeric" | "apa_7" | "mla_9" | "chicago_author_date" | "bibtex";
/** 候选阶段按来源声明或文本规则识别出的主语言。 */
export type CandidateLanguage = "zh" | "en" | "other" | "unknown";
export type IngestionStatus =
  "pending" | "queued" | "running" | "completed" | "failed" | "cancelled";

export interface User {
  id: string;
  email: string | null;
  display_name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface WorkflowStageDisplay {
  label: string;
  description: string;
}

export interface Workspace {
  id: string;
  name: string;
  description: string | null;
  research_question: string | null;
  status: string;
  workflow_stage: WorkflowStage;
  workflow_stage_display: WorkflowStageDisplay;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceListResponse {
  items: Workspace[];
  next_cursor: string | null;
}

export interface ResearchDirection {
  id: string;
  title: string;
  summary: string;
  subtopics: string[];
}

export interface ResearchScope {
  start_year: number | null;
  end_year: number | null;
  languages: ("zh" | "en")[];
}

/**
 * 计划生成阶段保存模型建议，用户确认后再补入最终范围。
 *
 * 后端复用同一个 JSON 字段持久化这两种状态，因此前端读取计划时不能假设
 * ``scope`` 总是可直接提交的 ``ResearchScope``。
 */
export interface ResearchPlanScopeEnvelope {
  suggested?: ResearchScope;
  confirmed?: ResearchScope;
  admission_rules?: Record<string, unknown>;
}

export type ResearchPlanScope = ResearchScope | ResearchPlanScopeEnvelope;

export interface ResearchPlan {
  id: string;
  collection_id: string;
  revision: number;
  raw_request: string;
  status: PlanStatus;
  direction_options: ResearchDirection[];
  selected_direction_id: string | null;
  scope: ResearchPlanScope;
  query_plan: Record<string, unknown>;
  model_snapshot: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchSubmissionResponse {
  workspace_id: string;
  workflow_stage: WorkflowStage;
  plan: ResearchPlan;
}

export interface SearchRun {
  id: string;
  collection_id: string;
  research_plan_id: string;
  status: SearchRunStatus;
  stage: SearchRunStage;
  attempt_no: number;
  provider_summary: Record<string, ProviderSummary>;
  candidate_counts: Record<string, number>;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderSummary {
  status?: string;
  candidate_count?: number;
  query_count?: number;
  result_count?: number;
  raw_candidate_count?: number;
  error?: string;
  errors?: Array<{ code?: string; message?: string; retryable?: boolean }>;
  [key: string]: unknown;
}

export interface CandidateAuthor {
  name: string;
  source_author_id?: string | null;
}

export interface CandidateLinks {
  landing_url: string | null;
  open_access_url: string | null;
  fulltext_url: string | null;
}

export interface CitationMetadata {
  status: "ready" | "partial" | "conflict" | "unresolved";
  doi: string | null;
  url: string | null;
  missing_fields?: string[];
  conflicts?: Record<string, string[]>;
}

export interface CandidateCitation {
  candidate_id: string;
  format: CitationFormat;
  text: string;
}

export type CandidateRelevanceLevel =
  "core" | "related" | "background" | "not_recommended" | "insufficient_information";
export type CandidateRelevanceState = "pending" | "completed" | "failed" | "skipped";
export interface CandidateRelevanceAssessment {
  level: CandidateRelevanceLevel;
  study_focus: string;
  reason: string;
  helpful_aspect: string;
  limitations: string[];
  recommendation: string;
  evidence: Array<{ source_field: "title" | "abstract"; quote: string }>;
}

export interface Candidate {
  candidate_id: string;
  doi: string | null;
  title: string;
  language: CandidateLanguage;
  authors: CandidateAuthor[];
  abstract: string | null;
  published_year: number | null;
  venue: string | null;
  document_type: string | null;
  citation_counts_by_source: Record<string, number>;
  links: CandidateLinks;
  is_open_access: boolean | null;
  triage: { included: boolean; exclusion_reasons: string[]; warnings: string[] } | null;
  relevance_state?: CandidateRelevanceState;
  relevance_assessment?: CandidateRelevanceAssessment | null;
  relevance_error?: { code: string; message: string; retryable: boolean } | null;
  citation: CitationMetadata | null;
}

export interface SearchCandidatesResponse {
  run_id: string;
  status: SearchRunStatus;
  candidate_counts: Record<string, number>;
  candidates: Candidate[];
}

/** 候选审核页的服务端筛选值，避免分页后仍在浏览器二次筛选整份快照。 */
export type CandidateReviewFilter =
  | "all"
  | "zh"
  | "en"
  | "priority"
  | "background"
  | "needs_review"
  | "available"
  | "open_access"
  | "doi"
  | "selected";

/** 当前页面候选同时携带跨页准备选择和独立的全文状态。 */
export interface CandidateReviewItem {
  candidate: Candidate;
  is_selected: boolean;
  fulltext: FulltextResponse | null;
}

/** 只描述本次 Redis 准备清单，不能与 PostgreSQL 待确认集合计数混用。 */
export interface CandidateSelectionSummary {
  selected_count: number;
  needs_fulltext_count: number;
  fulltext_in_progress_count: number;
  ready_for_admission_count: number;
  blocked_count: number;
}

export interface SearchCandidatePageResponse {
  run_id: string;
  status: SearchRunStatus;
  candidate_counts: Record<string, number>;
  items: CandidateReviewItem[];
  page: { limit: number; total: number; next_cursor: string | null };
  selection: CandidateSelectionSummary;
}

export interface CandidateSelectionResponse {
  run_id: string;
  selected_count: number;
}

export interface CandidatePreparationBatchResponse {
  run_id: string;
  selected_count: number;
  queued_count: number;
  items: Array<{
    candidate_id: string;
    status: FulltextStatus | null;
    message: string;
    retryable: boolean;
  }>;
}

export interface CandidateAdmissionBatchResponse {
  run_id: string;
  selected_count: number;
  admitted_count: number;
  already_joined_count: number;
  blocked_count: number;
  items: Array<{
    candidate_id: string;
    status: string;
    message: string;
    retryable: boolean;
  }>;
}

export interface SearchProgressEvent {
  run_id: string;
  status: SearchRunStatus;
  stage: SearchRunStage;
  provider_summary: Record<string, ProviderSummary>;
  candidate_counts: Record<string, number>;
  message: string | null;
}

export interface FulltextResponse {
  search_run_id: string;
  candidate_id: string;
  attempt_no: number;
  status: FulltextStatus;
  document: { staging_object_key: string; sha256: string; byte_size: number } | null;
  error: { code: string; message: string; retryable: boolean } | null;
  requested_at: string;
  updated_at: string;
}

export interface IngestionRun {
  id: string;
  document_id: string;
  arq_job_id: string | null;
  pipeline_version: string;
  status: IngestionStatus;
  stage: string;
  statistics: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  attempt_no: number;
  is_current: boolean;
  started_at: string | null;
  finished_at: string | null;
  submitted_at: string | null;
  created_at: string;
}

export interface CollectionDocument {
  document_id: string;
  paper_id: string;
  doi: string;
  title: string;
  authors: Record<string, unknown>[];
  publication_year: number | null;
  venue: string | null;
  citation_text: string;
  tags: string[];
  note: string | null;
  original_filename: string;
  byte_size: number;
  source_url: string | null;
  access_rights: string;
  added_at: string;
  latest_ingestion_run: IngestionRun | null;
}

export interface CollectionDocumentsResponse {
  collection_id: string;
  documents: CollectionDocument[];
  summary: {
    active_document_count: number;
    researchable_document_count: number;
    ingestion_status_counts: Record<string, number>;
  };
}

export interface CollectionBuildResponse {
  collection_id: string;
  workflow_stage: WorkflowStage;
  runs: {
    ingestion_run_id: string;
    status: IngestionStatus;
    arq_job_id: string | null;
    error_code: string | null;
    error_message: string | null;
  }[];
}

export type ConversationStatus = "active" | "archived" | "deleted";
export type ResearchRunMode = "single_rag" | "multi_agent" | "research_note";
export type ResearchRunStatus =
  "queued" | "running" | "awaiting_clarification" | "completed" | "failed" | "cancelled";
export type ResearchRunStage =
  | "dispatch"
  | "preparing"
  | "hybrid_retrieval"
  | "parent_merging"
  | "reranking"
  | "evidence_verifying"
  | "answering"
  | "completed"
  | "awaiting_clarification"
  | "failed"
  | "cancelled";

export interface Conversation {
  id: string;
  collection_id: string;
  title: string | null;
  status: ConversationStatus;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ResearchMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: "pending" | "streaming" | "completed" | "failed";
  metadata: Record<string, unknown>;
  created_at: string;
  research_run_id: string | null;
}

export interface ResearchEvidence {
  id: string;
  chunk_id: string;
  selection_stage: string;
  rank: number | null;
  vector_score: number | null;
  rrf_score: number | null;
  rerank_score: number | null;
  is_cited: boolean;
  citation_excerpt: string | null;
  locator_snapshot: Record<string, unknown> | null;
  paper_id: string;
  title: string;
  authors: Record<string, unknown>[];
  publication_year: number | null;
  source_url: string | null;
}

export interface ResearchRun {
  id: string;
  conversation_id: string;
  collection_id: string;
  input_message_id: string;
  output_message_id: string | null;
  arq_job_id: string | null;
  mode: ResearchRunMode;
  status: ResearchRunStatus;
  stage: ResearchRunStage;
  stage_display: { label: string; description: string };
  model_snapshot: Record<string, unknown>;
  retrieval_trace: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  stage_started_at: string | null;
  finished_at: string | null;
  created_at: string;
  evidences: ResearchEvidence[];
}

export interface ConversationDetailResponse {
  conversation: Conversation;
  messages: ResearchMessage[];
  runs: ResearchRun[];
}

export interface AskResearchQuestionResponse {
  user_message: ResearchMessage;
  research_run: ResearchRun;
}

export interface ResearchProgressEvent {
  run_id: string;
  status: ResearchRunStatus;
  stage: ResearchRunStage;
  message: string | null;
  evidence_count: number;
}
