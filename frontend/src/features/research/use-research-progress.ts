import { onScopeDispose, ref, toValue, type MaybeRefOrGetter } from "vue";

import { apiUrl, getAccessToken } from "@/api/client";
import { getResearchRun } from "@/api/research";
import type { ResearchProgressEvent, ResearchRun } from "@/api/types";

export function isResearchRunTerminal(status: ResearchRun["status"]): boolean {
  return ["awaiting_clarification", "completed", "failed", "cancelled"].includes(status);
}

interface ResearchProgressOptions {
  onRefresh: () => Promise<void>;
  onError?: (message: string | null) => void;
}

export function useResearchProgress(
  workspaceId: MaybeRefOrGetter<string>,
  conversationId: MaybeRefOrGetter<string>,
  options: ResearchProgressOptions,
) {
  const activeRun = ref<ResearchRun | null>(null);
  const progressEvent = ref<ResearchProgressEvent | null>(null);
  const eventController = ref<AbortController | null>(null);
  const streamedRunId = ref<string | null>(null);
  let reconnectTimer: number | null = null;

  function stop(): void {
    eventController.value?.abort();
    eventController.value = null;
    streamedRunId.value = null;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function reset(): void {
    stop();
    activeRun.value = null;
    progressEvent.value = null;
  }

  async function updateRunFromDatabase(runId: string): Promise<ResearchRun | null> {
    const currentConversationId = toValue(conversationId);
    if (!currentConversationId) return null;
    const run = await getResearchRun(toValue(workspaceId), currentConversationId, runId);
    activeRun.value = run;
    return run;
  }

  async function streamRun(run: ResearchRun): Promise<void> {
    if (isResearchRunTerminal(run.status) || streamedRunId.value === run.id) return;
    stop();

    const controller = new AbortController();
    eventController.value = controller;
    streamedRunId.value = run.id;
    options.onError?.(null);
    try {
      const response = await fetch(
        apiUrl(
          `/api/v1/collections/${toValue(workspaceId)}/conversations/${run.conversation_id}/research-runs/${run.id}/events`,
        ),
        {
          headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
          signal: controller.signal,
        },
      );
      if (!response.ok || !response.body) throw new Error("无法建立研究进度流。");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const packets = buffer.split("\n\n");
        buffer = packets.pop() ?? "";
        for (const packet of packets) {
          const dataLine = packet.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          try {
            const event = JSON.parse(dataLine.slice(5).trim()) as ResearchProgressEvent;
            progressEvent.value = event;
            activeRun.value = {
              ...run,
              ...activeRun.value,
              status: event.status,
              stage: event.stage,
            };
            if (isResearchRunTerminal(event.status)) {
              await options.onRefresh();
              return;
            }
          } catch {
            // 心跳和不完整事件不改变持久化状态，数据库读取负责恢复。
          }
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      options.onError?.(error instanceof Error ? error.message : "研究进度连接中断。");
      try {
        const persisted = await updateRunFromDatabase(run.id);
        if (persisted && !isResearchRunTerminal(persisted.status)) {
          reconnectTimer = window.setTimeout(() => void streamRun(persisted), 1_500);
        } else {
          await options.onRefresh();
        }
      } catch {
        // 页面刷新仍会从 PostgreSQL 恢复，浏览器内存不作为完成依据。
      }
    } finally {
      if (streamedRunId.value === run.id) streamedRunId.value = null;
    }
  }

  onScopeDispose(stop);

  return { activeRun, progressEvent, streamRun, stop, reset };
}
