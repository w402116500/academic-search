import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import {
  buildCollection,
  getCollectionDocuments,
  getWorkspace,
  listWorkspaces,
  removePendingDocument,
} from "@/api/collections";
import {
  askResearchQuestion,
  cancelResearchRun,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  retryResearchRun,
} from "@/api/research";
import { confirmPlan, getPlan, regeneratePlan, startResearch, startSearch } from "@/api/workflow";
import type { ResearchQuestionMode, ResearchScope } from "@/api/types";
import { queryKeys } from "./query-keys";

export function useWorkspaceQuery(workspaceId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => queryKeys.workspace.detail(toValue(workspaceId))),
    queryFn: () => getWorkspace(toValue(workspaceId)),
  });
}

export function useWorkspaceListQuery() {
  return useInfiniteQuery({
    queryKey: queryKeys.workspace.list(),
    queryFn: ({ pageParam }) => listWorkspaces("", pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

export function useWorkspaceSearchQuery(
  search: MaybeRefOrGetter<string>,
  enabled: MaybeRefOrGetter<boolean>,
) {
  return useInfiniteQuery({
    queryKey: computed(() => queryKeys.workspace.search(toValue(search))),
    queryFn: ({ pageParam }) => listWorkspaces(toValue(search), pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: computed(() => toValue(enabled)),
  });
}

export function useCollectionDocumentsQuery(
  workspaceId: MaybeRefOrGetter<string>,
  pollActive = false,
) {
  return useQuery({
    queryKey: computed(() => queryKeys.workspace.documents(toValue(workspaceId))),
    queryFn: () => getCollectionDocuments(toValue(workspaceId)),
    refetchInterval: pollActive
      ? (query) =>
          query.state.data?.summary.ingestion_status_counts?.running ||
          query.state.data?.summary.ingestion_status_counts?.queued
            ? 2_000
            : false
      : false,
  });
}

export function useCollectionMutations(workspaceId: MaybeRefOrGetter<string>) {
  const queryClient = useQueryClient();

  async function refreshDocuments(): Promise<void> {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.workspace.documents(toValue(workspaceId)),
    });
  }

  const buildCollectionMutation = useMutation({
    mutationFn: () => buildCollection(toValue(workspaceId)),
    onSuccess: refreshDocuments,
  });
  const removePendingDocumentMutation = useMutation({
    mutationFn: (documentId: string) => removePendingDocument(toValue(workspaceId), documentId),
    onSuccess: refreshDocuments,
  });

  return {
    buildCollectionMutation,
    removePendingDocumentMutation,
    refreshDocuments,
  };
}

export function useResearchPlanQuery(workspaceId: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => queryKeys.research.plan(toValue(workspaceId))),
    queryFn: () => getPlan(toValue(workspaceId)),
    refetchInterval: (query) => (query.state.data?.status === "generating" ? 1_200 : false),
  });
}

export function useResearchPlanMutations(workspaceId: MaybeRefOrGetter<string>) {
  const queryClient = useQueryClient();
  const confirmMutation = useMutation({
    mutationFn: async ({
      selectedDirectionId,
      scope,
    }: {
      selectedDirectionId: string;
      scope: ResearchScope;
    }) => {
      const plan = await confirmPlan(toValue(workspaceId), selectedDirectionId, scope);
      const run = await startSearch(toValue(workspaceId));
      return { plan, run };
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspace.detail(toValue(workspaceId)) }),
  });
  const regenerateMutation = useMutation({
    mutationFn: (rawRequest: string) => regeneratePlan(toValue(workspaceId), rawRequest),
    onSuccess: async (plan) => {
      queryClient.setQueryData(queryKeys.research.plan(toValue(workspaceId)), plan);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workspace.detail(toValue(workspaceId)),
      });
    },
  });

  return { confirmMutation, regenerateMutation };
}

export function useStartResearchMutation() {
  return useMutation({ mutationFn: (rawRequest: string) => startResearch(rawRequest) });
}

export function useResearchQueries(
  workspaceId: MaybeRefOrGetter<string>,
  conversationId: MaybeRefOrGetter<string>,
) {
  const queryClient = useQueryClient();
  const workspaceQuery = useQuery({
    queryKey: computed(() => queryKeys.workspace.detail(toValue(workspaceId))),
    queryFn: () => getWorkspace(toValue(workspaceId)),
  });
  const documentsQuery = useQuery({
    queryKey: computed(() => queryKeys.workspace.documents(toValue(workspaceId))),
    queryFn: () => getCollectionDocuments(toValue(workspaceId)),
  });
  const conversationsQuery = useQuery({
    queryKey: computed(() => queryKeys.research.conversations(toValue(workspaceId))),
    queryFn: () => listConversations(toValue(workspaceId)),
  });
  const conversationQuery = useQuery({
    queryKey: computed(() =>
      queryKeys.research.conversation(toValue(workspaceId), toValue(conversationId)),
    ),
    queryFn: () => getConversation(toValue(workspaceId), toValue(conversationId)),
    enabled: computed(() => Boolean(toValue(conversationId))),
  });

  const createConversationMutation = useMutation({
    mutationFn: () => createConversation(toValue(workspaceId)),
  });
  const askQuestionMutation = useMutation({
    mutationFn: ({
      conversationId: targetId,
      content,
      mode,
    }: {
      conversationId: string;
      content: string;
      mode: ResearchQuestionMode;
    }) => askResearchQuestion(toValue(workspaceId), targetId, content, mode),
  });
  const retryRunMutation = useMutation({
    mutationFn: ({ conversationId: targetId, runId }: { conversationId: string; runId: string }) =>
      retryResearchRun(toValue(workspaceId), targetId, runId),
  });
  const cancelRunMutation = useMutation({
    mutationFn: ({ conversationId: targetId, runId }: { conversationId: string; runId: string }) =>
      cancelResearchRun(toValue(workspaceId), targetId, runId),
  });
  const deleteConversationMutation = useMutation({
    mutationFn: (targetId: string) => deleteConversation(toValue(workspaceId), targetId),
  });

  async function refreshConversations(): Promise<void> {
    await queryClient.invalidateQueries({
      queryKey: queryKeys.research.conversations(toValue(workspaceId)),
    });
  }

  async function refreshConversation(): Promise<void> {
    await Promise.all([
      refreshConversations(),
      queryClient.invalidateQueries({
        queryKey: queryKeys.research.conversation(toValue(workspaceId), toValue(conversationId)),
      }),
    ]);
    if (toValue(conversationId)) await conversationQuery.refetch();
  }

  return {
    workspaceQuery,
    documentsQuery,
    conversationsQuery,
    conversationQuery,
    createConversationMutation,
    askQuestionMutation,
    retryRunMutation,
    cancelRunMutation,
    deleteConversationMutation,
    refreshConversations,
    refreshConversation,
  };
}
