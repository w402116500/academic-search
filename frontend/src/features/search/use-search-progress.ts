import { onMounted, onScopeDispose, ref, toValue, type MaybeRefOrGetter } from "vue";
import { useQueryClient } from "@tanstack/vue-query";

import { ApiError, apiUrl, getAccessToken } from "@/api/client";
import { getCurrentSearchRun, retrySearch, startSearch } from "@/api/workflow";
import { queryKeys } from "@/api/hooks/query-keys";
import type { SearchProgressEvent, SearchRun } from "@/api/types";

export const SEARCH_TERMINAL_STATUSES = [
  "completed",
  "partial_failed",
  "failed",
  "expired",
  "cancelled",
] as const;

export function isSearchRunTerminal(status: string | undefined): boolean {
  return SEARCH_TERMINAL_STATUSES.includes(status as (typeof SEARCH_TERMINAL_STATUSES)[number]);
}

export function useSearchProgress(workspaceId: MaybeRefOrGetter<string>) {
  const queryClient = useQueryClient();
  const run = ref<SearchRun | null>(null);
  const errorMessage = ref<string | null>(null);
  const loading = ref(true);
  const controller = ref<AbortController | null>(null);
  const lastProgressMessage = ref<string | null>(null);
  const lastProgressAt = ref<number | null>(null);
  const progressStreamStartedAt = ref<number | null>(null);
  const streamProblemMessage = ref<string | null>(null);
  const reconnecting = ref(false);
  const progressClock = ref(Date.now());
  let progressClockTimer: ReturnType<typeof setInterval> | null = null;

  const currentWorkspaceId = (): string => toValue(workspaceId);

  function saveRun(nextRun: SearchRun): void {
    run.value = nextRun;
    queryClient.setQueryData(queryKeys.search.run(currentWorkspaceId()), nextRun);
  }

  async function loadRun(): Promise<SearchRun> {
    try {
      return await getCurrentSearchRun(currentWorkspaceId());
    } catch (error) {
      if (error instanceof ApiError && error.status === 404)
        return startSearch(currentWorkspaceId());
      throw error;
    }
  }

  function updateFromEvent(event: SearchProgressEvent): void {
    if (!run.value) return;
    saveRun({
      ...run.value,
      status: event.status,
      stage: event.stage,
      provider_summary: event.provider_summary ?? run.value.provider_summary,
      candidate_counts: { ...run.value.candidate_counts, ...event.candidate_counts },
    });
    lastProgressAt.value = Date.now();
    if (event.message) lastProgressMessage.value = event.message;
    streamProblemMessage.value = null;
  }

  async function refreshTerminalRun(runId: string): Promise<void> {
    const persistedRun = await getCurrentSearchRun(currentWorkspaceId());
    if (persistedRun.id === runId) saveRun(persistedRun);
  }

  async function streamEvents(runId: string): Promise<void> {
    controller.value?.abort();
    const abort = new AbortController();
    controller.value = abort;
    try {
      const response = await fetch(
        apiUrl(`/api/v1/collections/${currentWorkspaceId()}/search-runs/${runId}/events`),
        { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` }, signal: abort.signal },
      );
      if (!response.ok || !response.body) throw new Error("无法建立检索进度流。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          try {
            updateFromEvent(JSON.parse(dataLine.slice(5).trim()) as SearchProgressEvent);
          } catch {
            // 心跳或损坏事件由下一次持久化读取恢复。
          }
        }
      }
      if (run.value && isSearchRunTerminal(run.value.status)) {
        await refreshTerminalRun(runId);
        return;
      }
      if (!abort.signal.aborted) throw new Error("进度连接已结束，请重新连接确认任务状态。");
    } catch (error) {
      if (abort.signal.aborted || (error instanceof Error && error.name === "AbortError")) return;
      throw error;
    } finally {
      if (controller.value === abort) controller.value = null;
    }
  }

  async function connectProgressStream(runId: string): Promise<void> {
    streamProblemMessage.value = null;
    progressStreamStartedAt.value = Date.now();
    try {
      await streamEvents(runId);
    } catch (error) {
      streamProblemMessage.value =
        error instanceof Error ? error.message : "无法继续读取检索进度，请重新连接。";
    }
  }

  async function initialize(): Promise<void> {
    loading.value = true;
    errorMessage.value = null;
    controller.value?.abort();
    try {
      saveRun(await loadRun());
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "无法读取检索状态。";
    } finally {
      loading.value = false;
    }
    if (run.value && !isSearchRunTerminal(run.value.status))
      void connectProgressStream(run.value.id);
  }

  async function retry(): Promise<void> {
    if (!run.value) return;
    loading.value = true;
    errorMessage.value = null;
    controller.value?.abort();
    try {
      saveRun(await retrySearch(currentWorkspaceId(), run.value.id));
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "检索重试失败。";
    } finally {
      loading.value = false;
    }
    if (run.value && !isSearchRunTerminal(run.value.status))
      void connectProgressStream(run.value.id);
  }

  async function reconnectProgress(): Promise<void> {
    if (!run.value || isSearchRunTerminal(run.value.status)) return;
    reconnecting.value = true;
    try {
      await connectProgressStream(run.value.id);
    } finally {
      reconnecting.value = false;
    }
  }

  function stop(): void {
    controller.value?.abort();
    controller.value = null;
  }

  onMounted(() => {
    progressClockTimer = setInterval(() => {
      progressClock.value = Date.now();
    }, 1_000);
  });
  onScopeDispose(() => {
    stop();
    if (progressClockTimer) clearInterval(progressClockTimer);
  });

  return {
    run,
    errorMessage,
    loading,
    lastProgressMessage,
    lastProgressAt,
    progressStreamStartedAt,
    streamProblemMessage,
    reconnecting,
    progressClock,
    initialize,
    retry,
    reconnectProgress,
    stop,
  };
}
