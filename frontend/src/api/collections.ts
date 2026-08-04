import { apiFetch } from "./client";
import type {
  CollectionBuildResponse,
  CollectionDocumentsResponse,
  CandidateCitation,
  CitationFormat,
  FulltextResponse,
  Workspace,
  WorkspaceListResponse,
} from "./types";

export const getWorkspace = (workspaceId: string): Promise<Workspace> =>
  apiFetch<Workspace>(`/api/v1/collections/${workspaceId}`);

export const listWorkspaces = (
  query = "",
  cursor?: string | null,
): Promise<WorkspaceListResponse> => {
  const params = new URLSearchParams({ limit: "20" });
  if (query.trim()) params.set("q", query.trim());
  if (cursor) params.set("cursor", cursor);
  return apiFetch<WorkspaceListResponse>(`/api/v1/collections?${params.toString()}`);
};

export const getCollectionDocuments = (workspaceId: string): Promise<CollectionDocumentsResponse> =>
  apiFetch<CollectionDocumentsResponse>(`/api/v1/collections/${workspaceId}/documents`);

export const buildCollection = (workspaceId: string): Promise<CollectionBuildResponse> =>
  apiFetch<CollectionBuildResponse>(`/api/v1/collections/${workspaceId}/build`, { method: "POST" });

export const removePendingDocument = (workspaceId: string, documentId: string): Promise<unknown> =>
  apiFetch<unknown>(`/api/v1/collections/${workspaceId}/documents/${documentId}`, {
    method: "DELETE",
  });

export const requestFulltext = (
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<FulltextResponse> =>
  apiFetch<FulltextResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/fulltext`,
    { method: "POST" },
  );

export const getFulltext = (
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<FulltextResponse> =>
  apiFetch<FulltextResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/fulltext`,
  );

export const uploadAuthorizedFulltext = (
  workspaceId: string,
  runId: string,
  candidateId: string,
  file: File,
): Promise<FulltextResponse> =>
  apiFetch<FulltextResponse>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/fulltext/upload`,
    {
      method: "POST",
      headers: {
        "Content-Type": file.type || "application/pdf",
        "X-Upload-Authorized": "true",
      },
      body: file,
    },
  );

export const getCandidateCitation = (
  workspaceId: string,
  runId: string,
  candidateId: string,
  citationFormat: CitationFormat = "gb_t_7714_2015_numeric",
): Promise<CandidateCitation> => {
  const params = new URLSearchParams({ format: citationFormat });
  return apiFetch<CandidateCitation>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/citation?${params.toString()}`,
  );
};

export const admitFulltext = (
  workspaceId: string,
  runId: string,
  candidateId: string,
): Promise<unknown> =>
  apiFetch<unknown>(
    `/api/v1/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/fulltext/admission`,
    { method: "POST" },
  );
