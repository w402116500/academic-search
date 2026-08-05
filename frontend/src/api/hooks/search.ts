import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import {
  admitCandidateSelection,
  clearCandidateSelection,
  getCurrentSearchRun,
  getSearchCandidate,
  getSearchCandidates,
  prepareCandidateSelection,
  updateCandidateSelection,
} from "@/api/workflow";
import type { CandidateReviewFilter } from "@/api/types";
import { queryKeys } from "./query-keys";

export function useCurrentSearchRunQuery(
  workspaceId: MaybeRefOrGetter<string>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  return useQuery({
    queryKey: computed(() => queryKeys.search.run(toValue(workspaceId))),
    queryFn: () => getCurrentSearchRun(toValue(workspaceId)),
    enabled: computed(() => toValue(enabled)),
  });
}

interface CandidatePageSources {
  cursor: MaybeRefOrGetter<string | null>;
  query?: MaybeRefOrGetter<string>;
  filter?: MaybeRefOrGetter<CandidateReviewFilter>;
  limit: MaybeRefOrGetter<number>;
}

export function useSearchCandidatesQuery(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
  sources: CandidatePageSources,
  staleTime = 5_000,
) {
  return useQuery({
    queryKey: computed(() => [
      ...queryKeys.search.candidates(toValue(workspaceId), toValue(runId)),
      toValue(sources.cursor),
      sources.query === undefined ? "" : toValue(sources.query),
      sources.filter === undefined ? "all" : toValue(sources.filter),
      toValue(sources.limit),
    ]),
    queryFn: () =>
      getSearchCandidates(toValue(workspaceId), toValue(runId), {
        limit: toValue(sources.limit),
        cursor: toValue(sources.cursor),
        query: sources.query === undefined ? "" : toValue(sources.query),
        filter: sources.filter === undefined ? "all" : toValue(sources.filter),
      }),
    enabled: computed(() => Boolean(toValue(runId))),
    staleTime,
  });
}

export function useVerificationCandidatesQuery(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
  cursor: MaybeRefOrGetter<string | null>,
  limit: number,
) {
  return useQuery({
    queryKey: computed(() => [
      ...queryKeys.search.verification(toValue(workspaceId), toValue(runId)),
      toValue(cursor),
    ]),
    queryFn: () =>
      getSearchCandidates(toValue(workspaceId), toValue(runId), {
        limit,
        cursor: toValue(cursor),
        filter: "selected",
      }),
    enabled: computed(() => Boolean(toValue(runId))),
    staleTime: 3_000,
  });
}

export function useSearchCandidateQuery(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
  candidateId: MaybeRefOrGetter<string>,
) {
  return useQuery({
    queryKey: computed(() =>
      queryKeys.search.candidate(toValue(workspaceId), toValue(runId), toValue(candidateId)),
    ),
    queryFn: () => getSearchCandidate(toValue(workspaceId), toValue(runId), toValue(candidateId)),
    enabled: computed(() => Boolean(toValue(runId)) && Boolean(toValue(candidateId))),
  });
}

export function useSearchReviewMutations(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
) {
  const queryClient = useQueryClient();

  async function refreshCandidates(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.search.candidates(toValue(workspaceId), toValue(runId)),
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.search.verification(toValue(workspaceId), toValue(runId)),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.search.run(toValue(workspaceId)) }),
    ]);
  }

  const selectionMutation = useMutation({
    mutationFn: ({ candidateIds, selected }: { candidateIds: string[]; selected: boolean }) =>
      updateCandidateSelection(toValue(workspaceId), toValue(runId), candidateIds, selected),
    onSuccess: refreshCandidates,
  });
  const clearSelectionMutation = useMutation({
    mutationFn: () => clearCandidateSelection(toValue(workspaceId), toValue(runId)),
    onSuccess: refreshCandidates,
  });
  const prepareSelectionMutation = useMutation({
    mutationFn: () => prepareCandidateSelection(toValue(workspaceId), toValue(runId)),
    onSuccess: refreshCandidates,
  });
  const admitSelectionMutation = useMutation({
    mutationFn: () => admitCandidateSelection(toValue(workspaceId), toValue(runId)),
    onSuccess: refreshCandidates,
  });

  return {
    selectionMutation,
    clearSelectionMutation,
    prepareSelectionMutation,
    admitSelectionMutation,
    refreshCandidates,
  };
}
