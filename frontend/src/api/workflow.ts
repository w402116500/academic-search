import { apiFetch } from "./client";
import type {
  ResearchPlan,
  ResearchScope,
  ResearchSubmissionResponse,
  SearchCandidatesResponse,
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

export const getSearchCandidates = (
  workspaceId: string,
  runId: string,
): Promise<SearchCandidatesResponse> =>
  apiFetch<SearchCandidatesResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates`,
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
