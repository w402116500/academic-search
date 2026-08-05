import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import {
  getCandidateCitation,
  getFulltext,
  requestFulltext,
  uploadAuthorizedFulltext,
} from "@/api/collections";
import type { CitationFormat } from "@/api/types";
import { queryKeys } from "./query-keys";

export function useCandidateCitationQuery(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
  candidateId: MaybeRefOrGetter<string>,
  format: MaybeRefOrGetter<CitationFormat>,
  enabled: MaybeRefOrGetter<boolean> = true,
) {
  return useQuery({
    queryKey: computed(() =>
      queryKeys.literature.citation(
        toValue(workspaceId),
        toValue(runId),
        toValue(candidateId),
        toValue(format),
      ),
    ),
    queryFn: () =>
      getCandidateCitation(
        toValue(workspaceId),
        toValue(runId),
        toValue(candidateId),
        toValue(format),
      ),
    enabled: computed(
      () => Boolean(toValue(runId)) && Boolean(toValue(candidateId)) && toValue(enabled),
    ),
  });
}

export function useCandidateLiteratureMutations(
  workspaceId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
) {
  const queryClient = useQueryClient();

  async function refreshCandidateReview(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.search.candidates(toValue(workspaceId), toValue(runId)),
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.search.verification(toValue(workspaceId), toValue(runId)),
      }),
    ]);
  }

  const requestFulltextMutation = useMutation({
    mutationFn: (candidateId: string) =>
      requestFulltext(toValue(workspaceId), toValue(runId), candidateId),
    onSuccess: refreshCandidateReview,
  });
  const citationMutation = useMutation({
    mutationFn: (candidateId: string) =>
      getCandidateCitation(toValue(workspaceId), toValue(runId), candidateId),
  });
  const uploadFulltextMutation = useMutation({
    mutationFn: ({ candidateId, file }: { candidateId: string; file: File }) =>
      uploadAuthorizedFulltext(toValue(workspaceId), toValue(runId), candidateId, file),
    onSuccess: refreshCandidateReview,
  });

  return { requestFulltextMutation, citationMutation, uploadFulltextMutation };
}

export async function readCandidateFulltext(
  workspaceId: string,
  runId: string,
  candidateId: string,
) {
  return getFulltext(workspaceId, runId, candidateId);
}
