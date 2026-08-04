<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useQueryClient } from "@tanstack/vue-query";
import {
  ArrowRight,
  Check,
  CircleAlert,
  DatabaseZap,
  FileCheck2,
  ListTree,
  LoaderCircle,
  RotateCcw,
  ScanSearch,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import { apiUrl, getAccessToken, ApiError } from "@/api/client";
import {
  isSearchRunProgressStalled,
  searchRunCandidateCount,
  searchRunRelevanceProgress,
} from "@/features/research/search-run-state";
import { getCurrentSearchRun, retrySearch, startSearch } from "@/api/workflow";
import type { ProviderSummary, SearchProgressEvent, SearchRun, SearchRunStage } from "@/api/types";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const workspaceId = computed(() => String(route.params.workspaceId));
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
const stages: { key: SearchRunStage; title: string; detail: string; icon: typeof DatabaseZap }[] = [
  {
    key: "provider_search",
    title: "多源检索",
    detail: "并行调用已启用的文献来源",
    icon: DatabaseZap,
  },
  { key: "normalize", title: "记录规整", detail: "统一标题、作者、DOI 与摘要字段", icon: ListTree },
  { key: "triage", title: "去重与初筛", detail: "合并重复记录并检查候选边界", icon: FileCheck2 },
  {
    key: "relevance_assessment",
    title: "相关性判断",
    detail: "在完整候选集合中统一判断与研究方向的关系",
    icon: ScanSearch,
  },
  {
    key: "citation_enrichment",
    title: "题录补全",
    detail: "补齐可复制的格式中立题录",
    icon: Check,
  },
];
const terminal = (status: string | undefined): boolean =>
  ["completed", "partial_failed", "failed", "expired", "cancelled"].includes(status ?? "");
const candidatesReady = computed(() =>
  ["completed", "partial_failed"].includes(run.value?.status ?? ""),
);
const candidateCount = computed(() => searchRunCandidateCount(run.value?.candidate_counts ?? {}));
const relevanceProgress = computed(() =>
  searchRunRelevanceProgress(run.value?.candidate_counts ?? {}),
);
const stageIndex = (stage: string | undefined): number =>
  stages.findIndex((item) => item.key === stage);
const stageState = (stage: SearchRunStage): "done" | "active" | "locked" => {
  // 即使部分来源失败，只要运行已产生候选，五个可验证处理阶段都已经走完。
  if (candidatesReady.value) return "done";
  const current = stageIndex(run.value?.stage);
  const index = stageIndex(stage);
  return current > index ? "done" : current === index ? "active" : "locked";
};
const relevanceProgressLabel = computed(() => {
  const { total, completed, failed } = relevanceProgress.value;
  if (!total) return "正在准备相关性判断";
  if (failed) return `已分析 ${completed} / ${total} 篇，${failed} 篇需要重试`;
  return `已分析 ${completed} / ${total} 篇`;
});
const currentStageDetail = computed(() => {
  const currentStage = stages.find((stage) => stage.key === run.value?.stage);
  if (run.value?.stage === "relevance_assessment") return relevanceProgressLabel.value;
  return currentStage?.detail ?? "正在准备检索任务";
});
const searchHeading = computed(() => {
  if (candidatesReady.value && candidateCount.value) {
    return `${candidateCount.value} 篇候选文献，已经准备好。`;
  }
  if (candidatesReady.value) return "检索已经完成，可以查看候选结果。";
  if (run.value?.stage === "relevance_assessment") {
    const count = candidateCount.value || relevanceProgress.value.total;
    return count
      ? `已找到 ${count} 篇候选，正在统一判断相关性。`
      : "正在依据完整候选集合统一判断与研究方向的相关性。";
  }
  return "正在建立候选文献集合。";
});
const candidateCountLabel = computed(() => {
  if (candidatesReady.value) return "可审核候选";
  if (run.value?.stage === "relevance_assessment") return "已规整候选";
  return "当前候选";
});
const lastProgressLabel = computed(() => {
  if (!lastProgressAt.value) return "正在等待第一条进度更新";
  const elapsedSeconds = Math.max(
    0,
    Math.floor((progressClock.value - lastProgressAt.value) / 1000),
  );
  if (elapsedSeconds <= 5) return "刚刚收到进度更新";
  return `${elapsedSeconds} 秒前收到进度更新`;
});
const streamStalled = computed(() => {
  if (terminal(run.value?.status)) return false;
  return isSearchRunProgressStalled(
    lastProgressAt.value,
    progressStreamStartedAt.value,
    progressClock.value,
  );
});
const progressNotice = computed(() => {
  if (streamProblemMessage.value) return streamProblemMessage.value;
  if (streamStalled.value) return "暂未收到新的进度事件，可重新连接确认任务状态。";
  return null;
});
function stageDetail(stage: (typeof stages)[number]): string {
  return stage.key === "relevance_assessment" ? relevanceProgressLabel.value : stage.detail;
}
function stageStateLabel(stage: (typeof stages)[number]): string {
  const state = stageState(stage.key);
  if (state === "done") return "已完成";
  if (state === "active" && stage.key === "relevance_assessment")
    return relevanceProgressLabel.value;
  return state === "active" ? "进行中" : "等待";
}

const stageLabels: Record<SearchRunStage, string> = {
  dispatch: "准备执行",
  provider_search: "多源检索",
  normalize: "记录规整",
  triage: "去重与初筛",
  relevance_assessment: "候选相关性分析",
  citation_enrichment: "题录补全",
  completed: "检索完成",
};
const currentStageLabel = computed(() => stageLabels[run.value?.stage ?? "dispatch"]);
const providerLabelMap: Record<string, string> = {
  openalex: "OpenAlex",
  crossref: "Crossref",
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};
const providerStatusMap: Record<string, { label: string; tone: string }> = {
  queued: { label: "等待执行", tone: "pending" },
  running: { label: "检索中", tone: "pending" },
  completed: { label: "已返回", tone: "completed" },
  partial: { label: "部分返回", tone: "partial" },
  failed: { label: "暂未返回", tone: "failed" },
};

function providerErrorMessage(provider: ProviderSummary): string | null {
  if (typeof provider.error === "string" && provider.error.trim()) return provider.error;
  const firstError = provider.errors?.find((error) => typeof error.message === "string");
  return firstError?.message?.trim() || null;
}

const providerEntries = computed(() =>
  Object.entries(run.value?.provider_summary ?? {}).map(([name, provider]) => {
    const status = provider.status ?? "queued";
    const presentation = providerStatusMap[status] ?? { label: "状态待刷新", tone: "pending" };
    return {
      key: name,
      label: providerLabelMap[name] ?? name,
      statusLabel: presentation.label,
      tone: presentation.tone,
      rawCandidateCount: provider.raw_candidate_count ?? provider.candidate_count ?? 0,
      queryCount: provider.query_count ?? 0,
      errorMessage: providerErrorMessage(provider),
    };
  }),
);
const readyProviderCount = computed(
  () =>
    providerEntries.value.filter((provider) => ["completed", "partial"].includes(provider.tone))
      .length,
);
const providerHealthSummary = computed(() => {
  const total = providerEntries.value.length;
  if (!total) return "正在连接来源";
  return `${readyProviderCount.value} / ${total} 个来源已返回`;
});
const failedProviderCount = computed(
  () => providerEntries.value.filter((provider) => provider.tone === "failed").length,
);
const failedProviderNotice = computed(() => {
  if (!failedProviderCount.value) return null;
  return `${failedProviderCount.value} 个来源暂未返回，系统仍会继续处理其他来源已返回的候选。`;
});

async function loadRun(): Promise<SearchRun> {
  try {
    return await getCurrentSearchRun(workspaceId.value);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return startSearch(workspaceId.value);
    throw error;
  }
}

function saveRun(nextRun: SearchRun): void {
  run.value = nextRun;
  queryClient.setQueryData(["search-run", workspaceId.value], nextRun);
}

function updateFromEvent(event: SearchProgressEvent): void {
  if (!run.value) return;
  saveRun({
    ...run.value,
    status: event.status,
    stage: event.stage,
    provider_summary: event.provider_summary,
    // 事件可能只携带本阶段新增的统计，不能覆盖已从持久化运行读取到的最终候选数。
    candidate_counts: { ...run.value.candidate_counts, ...event.candidate_counts },
  });
  // 时间与说明只在确实收到 SSE 事件时更新，避免把页面首次加载伪装成实时进度。
  lastProgressAt.value = Date.now();
  if (event.message) lastProgressMessage.value = event.message;
  streamProblemMessage.value = null;
}

async function refreshTerminalRun(runId: string): Promise<void> {
  const persistedRun = await getCurrentSearchRun(workspaceId.value);
  if (persistedRun.id === runId) saveRun(persistedRun);
}

async function streamEvents(runId: string): Promise<void> {
  controller.value?.abort();
  const abort = new AbortController();
  controller.value = abort;
  try {
    const response = await fetch(
      apiUrl(`/api/v1/collections/${workspaceId.value}/search-runs/${runId}/events`),
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
          /* 忽略心跳或损坏事件，下一次刷新会恢复真实状态。 */
        }
      }
    }
    if (run.value && terminal(run.value.status)) {
      // SSE 只用于推进画布；终态重新读取数据库快照，确保显示最终候选统计。
      await refreshTerminalRun(runId);
      return;
    }
    if (!abort.signal.aborted) throw new Error("进度连接已结束，请重新连接确认任务状态。");
  } catch (error) {
    // 用户主动重连、路由卸载时会终止旧请求，不应把这类正常中断显示为错误。
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
  if (run.value && !terminal(run.value.status)) void connectProgressStream(run.value.id);
}

async function retry(): Promise<void> {
  if (!run.value) return;
  loading.value = true;
  errorMessage.value = null;
  controller.value?.abort();
  try {
    saveRun(await retrySearch(workspaceId.value, run.value.id));
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "检索重试失败。";
  } finally {
    loading.value = false;
  }
  if (run.value && !terminal(run.value.status)) void connectProgressStream(run.value.id);
}

async function reconnectProgress(): Promise<void> {
  if (!run.value || terminal(run.value.status)) return;
  reconnecting.value = true;
  await connectProgressStream(run.value.id);
  reconnecting.value = false;
}

onMounted(() => {
  progressClockTimer = setInterval(() => {
    progressClock.value = Date.now();
  }, 1_000);
  void initialize();
});
onUnmounted(() => {
  controller.value?.abort();
  if (progressClockTimer) clearInterval(progressClockTimer);
});
</script>

<template>
  <section class="stage-view search-view">
    <div class="view-heading search-handoff-heading">
      <div>
        <div class="eyebrow">{{ candidatesReady ? "检索结果已交接" : "文献检索中" }}</div>
        <h1>
          {{ searchHeading }}
        </h1>
        <p>
          {{
            candidatesReady
              ? run?.status === "partial_failed"
                ? "部分来源没有返回，但当前候选已经完成规整，可以直接开始审核。"
                : "多源记录已经规整完成。接下来请审核题录与全文状态，再确认进入研究集合。"
              : run?.status === "failed"
                ? "这次检索没有完成，可以保留计划并重试。"
                : "系统按可验证阶段推进，不展示模型内部思考。"
          }}
        </p>
      </div>
      <div v-if="run" class="search-handoff-command">
        <span class="status-chip" :class="`status-${run.status}`">{{
          run.status === "completed"
            ? "已完成"
            : run.status === "partial_failed"
              ? "部分完成"
              : run.status === "failed"
                ? "需要处理"
                : "处理中"
        }}</span>
        <button
          v-if="terminal(run.status) && run.status !== 'completed' && run.status !== 'cancelled'"
          class="secondary-button"
          type="button"
          :disabled="loading"
          @click="retry"
        >
          <RotateCcw :size="15" />重试检索
        </button>
        <button
          v-if="candidatesReady"
          class="primary-button handoff-primary-action"
          type="button"
          @click="
            router.push({
              name: 'workspace-results',
              params: { workspaceId },
              query: { run: run.id },
            })
          "
        >
          <span>开始筛选{{ candidateCount ? ` ${candidateCount} 篇文献` : "" }}</span
          ><ArrowRight :size="17" />
        </button>
      </div>
    </div>
    <div v-if="loading && !run" class="loading-state">
      <LoaderCircle class="spin" :size="18" />正在连接检索任务…
    </div>
    <div v-else-if="errorMessage" class="failure-panel">
      <CircleAlert :size="18" />
      <div>
        <strong>进度读取失败</strong>
        <p>{{ errorMessage }}</p>
      </div>
      <button class="secondary-button" type="button" @click="initialize">
        <RotateCcw :size="15" />重新连接
      </button>
    </div>
    <template v-else-if="run">
      <section class="search-outcome-band" aria-label="本次检索结果摘要">
        <div class="search-outcome-count">
          <span>{{ candidateCountLabel }}</span>
          <strong>{{ candidateCount }}</strong
          ><small>篇</small>
        </div>
        <div class="search-outcome-copy">
          <strong>{{ candidatesReady ? "候选已完成规整与初筛" : currentStageDetail }}</strong>
          <p>
            {{
              candidatesReady
                ? "进入筛选后，你可以核对题录、引用格式和全文可用性，再决定是否纳入研究集合。"
                : (lastProgressMessage ?? "候选数与来源状态会随着检索进度实时刷新。")
            }}
          </p>
          <small v-if="!candidatesReady" class="search-progress-timestamp">{{
            lastProgressLabel
          }}</small>
        </div>
        <dl class="search-outcome-facts">
          <div>
            <dt>来源状态</dt>
            <dd>{{ providerHealthSummary }}</dd>
          </div>
          <div>
            <dt>当前阶段</dt>
            <dd>{{ currentStageLabel }}</dd>
          </div>
          <div>
            <dt>运行尝试</dt>
            <dd>#{{ run.attempt_no }}</dd>
          </div>
        </dl>
      </section>

      <section
        v-if="progressNotice"
        class="search-progress-notice"
        role="status"
        aria-live="polite"
      >
        <CircleAlert :size="16" />
        <p>{{ progressNotice }}</p>
        <button
          class="secondary-button search-progress-reconnect"
          type="button"
          :disabled="reconnecting"
          @click="reconnectProgress"
        >
          <RotateCcw :class="{ spin: reconnecting }" :size="14" />
          {{ reconnecting ? "正在连接" : "重新连接" }}
        </button>
      </section>

      <div class="search-handoff-grid">
        <section class="search-stage-panel" aria-labelledby="search-stage-heading">
          <div class="search-panel-heading">
            <div>
              <span class="section-kicker">处理轨迹</span>
              <h2 id="search-stage-heading">这次检索已经经过哪些处理</h2>
            </div>
            <span class="search-stage-caption">{{
              candidatesReady
                ? "全部处理完成"
                : run?.stage === "relevance_assessment"
                  ? relevanceProgressLabel
                  : "实时更新"
            }}</span>
          </div>
          <div class="search-stage-track" role="list">
            <div
              v-for="stage in stages"
              :key="stage.key"
              class="search-stage-item"
              :class="`stage-${stageState(stage.key)}`"
              role="listitem"
            >
              <span class="search-stage-icon"
                ><Check v-if="stageState(stage.key) === 'done'" :size="15" /><LoaderCircle
                  v-else-if="stageState(stage.key) === 'active'"
                  class="spin"
                  :size="15" /><component :is="stage.icon" v-else :size="15"
              /></span>
              <div>
                <strong>{{ stage.title }}</strong
                ><small>{{ stageDetail(stage) }}</small>
              </div>
              <span class="search-stage-state">{{ stageStateLabel(stage) }}</span>
            </div>
          </div>
        </section>

        <aside class="provider-health-panel" aria-labelledby="provider-health-heading">
          <div class="search-panel-heading">
            <div>
              <span class="section-kicker">来源健康度</span>
              <h2 id="provider-health-heading">本次来源执行情况</h2>
            </div>
            <span class="provider-health-count">{{ providerHealthSummary }}</span>
          </div>
          <div v-if="providerEntries.length" class="provider-health-list">
            <article
              v-for="provider in providerEntries"
              :key="provider.key"
              class="provider-health-item"
              :class="`provider-${provider.tone}`"
            >
              <div class="provider-health-row">
                <strong>{{ provider.label }}</strong
                ><span>{{ provider.statusLabel }}</span>
              </div>
              <p v-if="provider.errorMessage" class="provider-health-message">
                {{ provider.errorMessage }}
              </p>
              <small v-else-if="provider.rawCandidateCount">
                返回 {{ provider.rawCandidateCount }} 条初始记录
              </small>
              <small v-else-if="provider.queryCount"
                >已执行 {{ provider.queryCount }} 条检索表达式</small
              >
              <small v-else>正在等待来源状态</small>
            </article>
          </div>
          <div v-else class="provider-health-empty">
            <LoaderCircle class="spin" :size="16" /><span>正在连接已启用的文献来源…</span>
          </div>
          <p v-if="failedProviderNotice" class="provider-health-continuation">
            {{ failedProviderNotice }}
          </p>
        </aside>
      </div>
    </template>
  </section>
</template>
