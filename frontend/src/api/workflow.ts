import { apiFetch } from "./client";
import type {
  CandidateAdmissionBatchResponse,
  CandidatePreparationBatchResponse,
  CandidateReviewFilter,
  CandidateReviewItem,
  CandidateSelectionResponse,
  ResearchPlan,
  ResearchScope,
  ResearchSubmissionResponse,
  SearchCandidatesResponse,
  SearchCandidatePageResponse,
  SearchRun,
} from "./types";

export const startResearch = (rawRequest: string): Promise<ResearchSubmissionResponse> =>
  apiFetch<ResearchSubmissionResponse>("/api/v1/collections/research", {
    method: "POST",
    body: JSON.stringify({ raw_request: rawRequest }),
  });

export const getPlan = (workspaceId: string): Promise<ResearchPlan> =>
  apiFetch<ResearchPlan>(`/api/v1/collections/${workspaceId}/plan`);

export const confirmPlan = (
  workspaceId: string,
  selectedDirectionId: string,
  scope: ResearchScope,
): Promise<ResearchPlan> =>
  apiFetch<ResearchPlan>(`/api/v1/collections/${workspaceId}/plan/confirm`, {
    method: "POST",
    body: JSON.stringify({ selected_direction_id: selectedDirectionId, scope }),
  });

export const regeneratePlan = (workspaceId: string, rawRequest: string): Promise<ResearchPlan> =>
  apiFetch<ResearchPlan>(`/api/v1/collections/${workspaceId}/plan/regenerate`, {
    method: "POST",
    body: JSON.stringify({ raw_request: rawRequest }),
  });

export const startSearch = (workspaceId: string): Promise<SearchRun> =>
  apiFetch<SearchRun>(`/api/v1/collections/${workspaceId}/search-runs`, { method: "POST" });

export const getCurrentSearchRun = (workspaceId: string): Promise<SearchRun> =>
  apiFetch<SearchRun>(`/api/v1/collections/${workspaceId}/search-runs/current`);

export interface SearchCandidatePageQuery {
  limit: number;
  cursor?: string | null;
  query?: string;
  filter?: CandidateReviewFilter;
}

export const getSearchCandidates = (
  workspaceId: string,
  runId: string,
  options: SearchCandidatePageQuery,
): Promise<SearchCandidatePageResponse> => {
  const params = new URLSearchParams({ limit: String(options.limit) });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.query?.trim()) params.set("query", options.query.trim());
  if (options.filter && options.filter !== "all") params.set("filter", options.filter);
  return apiFetch<SearchCandidatePageResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates?${params.toString()}`,
  );
};

/** 详情页按候选 ID 读取，不扫描受游标限制的候选分页。 */
export const getSearchCandidate = (
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<CandidateReviewItem> =>
  apiFetch<CandidateReviewItem>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}`,
  );

export const updateCandidateSelection = (
  workspaceId: string,
  runId: string,
  candidateIds: string[],
  selected: boolean,
): Promise<CandidateSelectionResponse> =>
  apiFetch<CandidateSelectionResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidate-selection`,
    { method: "PATCH", body: JSON.stringify({ candidate_ids: candidateIds, selected }) },
  );

export const clearCandidateSelection = (
  workspaceId: string,
  runId: string,
): Promise<CandidateSelectionResponse> =>
  apiFetch<CandidateSelectionResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidate-selection`,
    { method: "DELETE" },
  );

export const prepareCandidateSelection = (
  workspaceId: string,
  runId: string,
): Promise<CandidatePreparationBatchResponse> =>
  apiFetch<CandidatePreparationBatchResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidate-selection/prepare`,
    { method: "POST" },
  );

export const admitCandidateSelection = (
  workspaceId: string,
  runId: string,
): Promise<CandidateAdmissionBatchResponse> =>
  apiFetch<CandidateAdmissionBatchResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidate-selection/admission`,
    { method: "POST" },
  );

export const retrySearch = (workspaceId: string, runId: string): Promise<SearchRun> =>
  apiFetch<SearchRun>(`/api/v1/collections/${workspaceId}/search-runs/${runId}/retry`, {
    method: "POST",
  });

export const retryCandidateRelevance = (
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<SearchCandidatesResponse> =>
  apiFetch<SearchCandidatesResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/relevance/retry`,
    { method: "POST" },
  );
