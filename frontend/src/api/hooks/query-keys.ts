export const queryKeys = {
  auth: {
    current: () => ["auth", "current"] as const,
  },
  workspace: {
    detail: (workspaceId: string) => ["workspace", workspaceId] as const,
    list: () => ["workspaces", "sidebar"] as const,
    search: (query: string) => ["workspaces", query] as const,
    documents: (workspaceId: string) => ["collection-documents", workspaceId] as const,
  },
  search: {
    run: (workspaceId: string) => ["search-run", workspaceId] as const,
    candidates: (workspaceId: string, runId: string) => ["candidates", workspaceId, runId] as const,
    candidate: (workspaceId: string, runId: string, candidateId: string) =>
      ["candidate-review-item", workspaceId, runId, candidateId] as const,
    verification: (workspaceId: string, runId: string) =>
      ["verification-candidates", workspaceId, runId] as const,
  },
  literature: {
    citation: (workspaceId: string, runId: string, candidateId: string, format: string) =>
      ["candidate-citation", workspaceId, runId, candidateId, format] as const,
    fulltext: (workspaceId: string, runId: string, candidateId: string) =>
      ["candidate-fulltext", workspaceId, runId, candidateId] as const,
  },
  research: {
    plan: (workspaceId: string) => ["plan", workspaceId] as const,
    conversations: (workspaceId: string) => ["research-conversations", workspaceId] as const,
    conversation: (workspaceId: string, conversationId: string) =>
      ["research-conversation", workspaceId, conversationId] as const,
  },
} as const;
