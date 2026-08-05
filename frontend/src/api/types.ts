/**
 * Compatibility names for application code while OpenAPI remains the only DTO source.
 * New API modules may import `components` directly from `./generated/schema`.
 */
import type { components } from "./generated/schema";

export type { components, operations, paths } from "./generated/schema";

type Schemas = components["schemas"];
type Response<T> = Required<T>;

export type WorkflowStage = Schemas["WorkspaceWorkflowStage"];
export type PlanStatus = Schemas["ResearchPlanStatus"];
export type SearchRunStatus = Schemas["SearchRunStatus"];
export type SearchRunStage = Schemas["SearchRunStage"];
export type FulltextStatus = Schemas["FulltextAcquisitionStatus"];
export type CitationFormat = Schemas["CitationFormat"];
export type CandidateLanguage = Schemas["CandidateLanguage"];
export type IngestionStatus = Schemas["IngestionRunStatus"];

export type User = Schemas["CurrentUserResponse"];
export type AuthResponse = Schemas["AuthenticationResponse"];
export type WorkflowStageDisplay = Response<Schemas["WorkflowStageDisplay"]>;
export type Workspace = Response<Schemas["WorkspaceResponse"]>;
export type WorkspaceListResponse = Response<Schemas["WorkspaceListResponse"]>;
export type ResearchDirection = Schemas["ResearchDirection"];
export type ResearchScope = Schemas["ResearchScope"];
export type ResearchPlanScopeEnvelope = Schemas["ResearchPlanScopeEnvelope"];
export type ResearchPlanScope = ResearchPlanScopeEnvelope;
export type ResearchPlan = Response<Schemas["ResearchPlanResponse"]>;
export type ResearchSubmissionResponse = Response<Schemas["ResearchSubmissionResponse"]>;
export type SearchRun = Response<Schemas["SearchRunResponse"]>;
export type ProviderSummary = Schemas["ProviderSummary"];
export type CandidateCounts = Schemas["CandidateCounts"];
export type CandidateAuthor = Schemas["CandidateAuthor"];
export type CandidateLinks = Schemas["CandidateLinks"];
export type CitationMetadata = Schemas["CitationMetadata"];
export type CandidateCitation = Schemas["CandidateCitationResponse"];
export type CandidateRelevanceLevel = Schemas["CandidateRelevanceLevel"];
export type CandidateRelevanceState = Schemas["CandidateRelevanceState"];
export type CandidateRelevanceAssessment = Schemas["CandidateRelevanceAssessment"];
export type Candidate = Omit<
  Schemas["UnifiedCandidate"],
  | "candidate_id"
  | "authors"
  | "citation"
  | "links"
  | "relevance_assessment"
  | "relevance_error"
  | "relevance_state"
  | "source_records"
  | "title_key"
> & {
  authors: CandidateAuthor[];
  candidate_id: string;
  citation: CitationMetadata | null;
  links: CandidateLinks;
  relevance_assessment?: CandidateRelevanceAssessment | null;
  relevance_error?: Response<Schemas["CandidateRelevanceError"]> | null;
  relevance_state?: CandidateRelevanceState;
  source_records?: Schemas["RawCandidate"][];
  title_key?: string;
};
export type CandidateReviewFilter = Schemas["CandidateReviewFilter"];
export type CandidateReviewItem = Omit<
  Response<Schemas["SearchCandidateReviewItem"]>,
  "candidate"
> & { candidate: Candidate };
export type CandidateSelectionSummary = Response<Schemas["CandidateSelectionSummary"]>;
export type SearchCandidatePageResponse = Omit<
  Response<Schemas["SearchCandidatePageResponse"]>,
  "items"
> & { items: CandidateReviewItem[] };
export type CandidateSelectionResponse = Response<Schemas["CandidateSelectionResponse"]>;
export type CandidatePreparationBatchResponse = Response<
  Schemas["CandidatePreparationBatchResponse"]
>;
export type CandidateAdmissionBatchResponse = Response<Schemas["CandidateAdmissionBatchResponse"]>;
export type SearchProgressEvent = Response<Schemas["SearchProgressEvent"]>;
export type FulltextResponse = Schemas["CandidateFulltextResponse"];
export type IngestionRun = Response<Schemas["IngestionRunResponse"]>;
export type CollectionDocument = Omit<
  Response<Schemas["CollectionDocumentResponse"]>,
  "latest_ingestion_run"
> & { latest_ingestion_run: IngestionRun | null };
export type CollectionDocumentsResponse = Omit<
  Response<Schemas["CollectionDocumentsResponse"]>,
  "documents" | "summary"
> & {
  documents: CollectionDocument[];
  summary: Response<Schemas["CollectionIngestionSummary"]>;
};
export type CollectionBuildResponse = Response<Schemas["CollectionBuildResponse"]>;
export type ConversationStatus = Schemas["ConversationStatus"];
export type ResearchRunMode = Schemas["ResearchRunMode"];
export type ResearchRunStatus = Schemas["ResearchRunStatus"];
export type ResearchRunStage = Schemas["ResearchRunStage"];
export type Conversation = Response<Schemas["ConversationResponse"]>;
export type ResearchMessage = Response<Schemas["ResearchMessageResponse"]>;
export type ResearchEvidence = Response<Schemas["ResearchEvidenceResponse"]>;
export type ResearchRun = Response<Schemas["ResearchRunResponse"]>;
export type ConversationDetailResponse = Omit<
  Response<Schemas["ConversationDetailResponse"]>,
  "messages" | "runs"
> & {
  messages: ResearchMessage[];
  runs: ResearchRun[];
};
export type AskResearchQuestionResponse = Omit<
  Response<Schemas["AskResearchQuestionResponse"]>,
  "research_run" | "user_message"
> & {
  research_run: ResearchRun;
  user_message: ResearchMessage;
};
export type ResearchProgressEvent = Response<Schemas["ResearchProgressEvent"]>;
