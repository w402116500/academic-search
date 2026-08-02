import { apiFetch } from "./client";
import type {
  AskResearchQuestionResponse,
  Conversation,
  ConversationDetailResponse,
  ResearchRun,
} from "./types";

export const listConversations = (workspaceId: string): Promise<Conversation[]> =>
  apiFetch<Conversation[]>(`/api/v1/collections/${workspaceId}/conversations`);

export const createConversation = (workspaceId: string, title?: string): Promise<Conversation> =>
  apiFetch<Conversation>(`/api/v1/collections/${workspaceId}/conversations`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });

export const getConversation = (
  workspaceId: string,
  conversationId: string,
): Promise<ConversationDetailResponse> =>
  apiFetch<ConversationDetailResponse>(
    `/api/v1/collections/${workspaceId}/conversations/${conversationId}`,
  );

export const askResearchQuestion = (
  workspaceId: string,
  conversationId: string,
  content: string,
): Promise<AskResearchQuestionResponse> =>
  apiFetch<AskResearchQuestionResponse>(
    `/api/v1/collections/${workspaceId}/conversations/${conversationId}/questions`,
    { method: "POST", body: JSON.stringify({ content }) },
  );

export const getResearchRun = (
  workspaceId: string,
  conversationId: string,
  researchRunId: string,
): Promise<ResearchRun> =>
  apiFetch<ResearchRun>(
    `/api/v1/collections/${workspaceId}/conversations/${conversationId}/research-runs/${researchRunId}`,
  );

export const retryResearchRun = (
  workspaceId: string,
  conversationId: string,
  researchRunId: string,
): Promise<ResearchRun> =>
  apiFetch<ResearchRun>(
    `/api/v1/collections/${workspaceId}/conversations/${conversationId}/research-runs/${researchRunId}/retry`,
    { method: "POST" },
  );

export const cancelResearchRun = (
  workspaceId: string,
  conversationId: string,
  researchRunId: string,
): Promise<ResearchRun> =>
  apiFetch<ResearchRun>(
    `/api/v1/collections/${workspaceId}/conversations/${conversationId}/research-runs/${researchRunId}/cancel`,
    { method: "POST" },
  );

export const deleteConversation = (
  workspaceId: string,
  conversationId: string,
): Promise<Conversation> =>
  apiFetch<Conversation>(`/api/v1/collections/${workspaceId}/conversations/${conversationId}`, {
    method: "DELETE",
  });
