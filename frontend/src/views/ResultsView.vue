<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  FileDown,
  FileSearch,
  Layers2,
  ListChecks,
  LoaderCircle,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import {
  buildCollection,
  getCandidateCitation,
  getCollectionDocuments,
  requestFulltext,
} from "@/api/collections";
import {
  canRequestFulltext,
  citationReadinessMessage,
  citationStatusLabel,
  fulltextStatusLabel,
  isFulltextTerminal,
} from "@/features/research/search-run-state";
import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/research/candidate-language";
import { presentCandidateRelevance } from "@/features/research/candidate-relevance";
import {
  clearCandidateSelection,
  cancelCandidateRelevance,
  getCurrentSearchRun,
  getSearchCandidates,
  retryCandidateRelevance,
  updateCandidateSelection,
} from "@/api/workflow";
import type {
  Candidate,
  CandidateReviewFilter,
  CandidateReviewItem,
  FulltextResponse,
} from "@/api/types";

const PAGE_SIZE_OPTIONS = [20, 50] as const;

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = ref(typeof route.query.run === "string" ? route.query.run : "");
const searchInput = ref("");
const searchQuery = ref("");
const selectedFilter = ref<CandidateReviewFilter>("all");
const pageSize = ref<(typeof PAGE_SIZE_OPTIONS)[number]>(20);
const cursorHistory = ref<Array<string | null>>([null]);
const selectedCandidateId = ref<string | null>(null);
const collectionConfirmOpen = ref(false);
const toast = ref<string | null>(null);
let searchDebounceTimer: number | undefined;
let reviewRefreshTimer: number | undefined;

const activeCursor = computed(() => cursorHistory.value.at(-1) ?? null);
const currentPageNumber = computed(() => cursorHistory.value.length);

const runQuery = useQuery({
  queryKey: computed(() => ["search-run", workspaceId.value]),
  queryFn: () => getCurrentSearchRun(workspaceId.value),
});

watch(
  () => runQuery.data.value?.id,
  (id) => {
    if (id && !runId.value) runId.value = id;
  },
  { immediate: true },
);

const candidatesQuery = useQuery({
  queryKey: computed(() => [
    "candidates",
    workspaceId.value,
    runId.value,
    activeCursor.value,
    searchQuery.value,
    selectedFilter.value,
    pageSize.value,
  ]),
  queryFn: () =>
    getSearchCandidates(workspaceId.value, runId.value, {
      limit: pageSize.value,
      cursor: activeCursor.value,
      query: searchQuery.value,
      filter: selectedFilter.value,
    }),
  enabled: computed(() => Boolean(runId.value)),
  staleTime: 5_000,
});

const collectionQuery = useQuery({
  queryKey: computed(() => ["collection-documents", workspaceId.value]),
  queryFn: () => getCollectionDocuments(workspaceId.value),
});

const reviewItems = computed(() => candidatesQuery.data.value?.items ?? []);
const selection = computed(
  () =>
    candidatesQuery.data.value?.selection ?? {
      selected_count: 0,
      needs_fulltext_count: 0,
      fulltext_in_progress_count: 0,
      ready_for_admission_count: 0,
      blocked_count: 0,
    },
);
const page = computed(
  () => candidatesQuery.data.value?.page ?? { limit: pageSize.value, total: 0, next_cursor: null },
);
const pendingCount = computed(
  () => collectionQuery.data.value?.summary.ingestion_status_counts.pending ?? 0,
);
const indexedCount = computed(
  () => collectionQuery.data.value?.summary.researchable_document_count ?? 0,
);
const providerCount = computed(
  () => Object.keys(runQuery.data.value?.provider_summary ?? {}).length,
);
const selectedReviewItem = computed(
  () =>
    reviewItems.value.find((item) => item.candidate.candidate_id === selectedCandidateId.value) ??
    null,
);
const selectedCandidate = computed(() => selectedReviewItem.value?.candidate ?? null);
const selectedCandidateReason = computed(() =>
  selectedCandidate.value ? presentCandidateRelevance(selectedCandidate.value) : null,
);
const selectablePageItems = computed(() => reviewItems.value.filter(isCandidateSelectable));
const allCurrentPageSelected = computed(
  () =>
    selectablePageItems.value.length > 0 &&
    selectablePageItems.value.every((item) => item.is_selected),
);
const isPreparing = computed(() =>
  reviewItems.value.some((item) => item.fulltext && !isFulltextTerminal(item.fulltext.status)),
);
const isRelevanceAnalyzing = computed(
  () =>
    runQuery.data.value?.status === "running" &&
    runQuery.data.value.stage === "relevance_assessment",
);
const isSearchRunActive = computed(() =>
  ["queued", "running"].includes(runQuery.data.value?.status ?? ""),
);
const canRetryRelevanceRun = computed(
  () =>
    !isSearchRunActive.value &&
    (Number(candidatesQuery.data.value?.candidate_counts.relevance_failed_count ?? 0) > 0 ||
      runQuery.data.value?.status === "cancelled"),
);

watch(
  reviewItems,
  (items) => {
    if (!items.length) {
      selectedCandidateId.value = null;
      return;
    }
    if (!items.some((item) => item.candidate.candidate_id === selectedCandidateId.value)) {
      selectedCandidateId.value = items[0].candidate.candidate_id;
    }
  },
  { immediate: true },
);

watch(searchInput, (value) => {
  window.clearTimeout(searchDebounceTimer);
  searchDebounceTimer = window.setTimeout(() => {
    searchQuery.value = value;
    resetPage();
  }, 250);
});

watch([selectedFilter, pageSize], resetPage);
watch([isPreparing, isSearchRunActive], restartReviewPolling, { immediate: true });
watch(isSearchRunActive, (active, wasActive) => {
  if (!active && wasActive && toast.value === "正在重新分析当前完整候选集合。") {
    toast.value = null;
  }
});

const selectionMutation = useMutation({
  mutationFn: ({ candidateIds, selected }: { candidateIds: string[]; selected: boolean }) =>
    updateCandidateSelection(workspaceId.value, runId.value, candidateIds, selected),
  onSuccess: async () => {
    await refreshCandidates();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "候选选择无法同步。";
  },
});

const clearSelectionMutation = useMutation({
  mutationFn: () => clearCandidateSelection(workspaceId.value, runId.value),
  onSuccess: async () => {
    toast.value = "本次准备清单已清空。";
    await refreshCandidates();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "准备清单无法清空。";
  },
});

const fulltextMutation = useMutation({
  mutationFn: (candidateId: string) => requestFulltext(workspaceId.value, runId.value, candidateId),
  onSuccess: async () => {
    toast.value = "单篇题录与全文核验已安排。";
    await refreshCandidates();
    restartReviewPolling();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "全文任务无法启动。";
  },
});

const relevanceRetryMutation = useMutation({
  mutationFn: () => retryCandidateRelevance(workspaceId.value, runId.value),
  onSuccess: async () => {
    toast.value = "正在重新分析当前完整候选集合。";
    await refreshCandidates();
    restartReviewPolling();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "候选理由暂时无法重新分析。";
  },
});

const relevanceCancelMutation = useMutation({
  mutationFn: () => cancelCandidateRelevance(workspaceId.value, runId.value),
  onSuccess: async () => {
    toast.value = "候选相关性分析已取消。";
    await refreshCandidates();
    await queryClient.invalidateQueries({ queryKey: ["search-run", workspaceId.value] });
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "候选相关性分析暂时无法取消。";
  },
});

const citationMutation = useMutation({
  mutationFn: (candidateId: string) =>
    getCandidateCitation(workspaceId.value, runId.value, candidateId),
  onSuccess: async (citation) => {
    try {
      await navigator.clipboard.writeText(citation.text);
      toast.value = "已复制 GB/T 7714-2015 正式引用。";
    } catch {
      toast.value = "浏览器未授予剪贴板权限，请在详情页查看并手动复制。";
    }
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "当前题录无法生成正式引用。";
  },
});

const buildMutation = useMutation({
  mutationFn: () => buildCollection(workspaceId.value),
  onSuccess: async () => {
    collectionConfirmOpen.value = false;
    toast.value = "集合构建任务已启动。";
    await refreshCollectionDocuments();
    await router.push({ name: "workspace-collection", params: { workspaceId: workspaceId.value } });
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "集合构建无法启动。";
  },
});

function resetPage(): void {
  cursorHistory.value = [null];
  selectedCandidateId.value = null;
}

function changeFilter(filter: CandidateReviewFilter): void {
  selectedFilter.value = filter;
}

function goToPreviousPage(): void {
  if (cursorHistory.value.length <= 1) return;
  cursorHistory.value = cursorHistory.value.slice(0, -1);
  selectedCandidateId.value = null;
}

function goToNextPage(): void {
  const nextCursor = page.value.next_cursor;
  if (!nextCursor) return;
  cursorHistory.value = [...cursorHistory.value, nextCursor];
  selectedCandidateId.value = null;
}

function toggleCandidate(item: CandidateReviewItem, selected: boolean): void {
  if (!isCandidateSelectable(item)) return;
  selectionMutation.mutate({ candidateIds: [item.candidate.candidate_id], selected });
}

function toggleCurrentPageSelection(): void {
  const candidateIds = selectablePageItems.value.map((item) => item.candidate.candidate_id);
  if (!candidateIds.length) {
    toast.value = "当前页没有可加入准备清单的候选。";
    return;
  }
  selectionMutation.mutate({ candidateIds, selected: !allCurrentPageSelected.value });
}

function openVerificationTask(): void {
  void router.push({
    name: "workspace-verification",
    params: { workspaceId: workspaceId.value },
    query: { run: runId.value },
  });
}

function isCandidateSelectable(item: CandidateReviewItem): boolean {
  return Boolean(item.candidate.doi && item.candidate.triage?.included);
}

function candidateSelectionHint(item: CandidateReviewItem): string {
  if (!item.candidate.doi) return "缺少 DOI，只能查看和人工核对，不能进入研究集合。";
  if (!item.candidate.triage?.included) return "未通过基础筛选，不能进入研究集合。";
  return "加入本次准备清单。";
}

function count(key: string): number {
  return Number(
    candidatesQuery.data.value?.candidate_counts[key] ??
      runQuery.data.value?.candidate_counts[key] ??
      0,
  );
}

function fulltextOf(item: CandidateReviewItem | null): FulltextResponse | null {
  return item?.fulltext ?? null;
}

function candidateState(candidate: Candidate, fulltext: FulltextResponse | null): string {
  if (!candidate.doi) return "缺少 DOI";
  if (fulltext?.status === "available") return "可加入集合";
  if (fulltext?.status === "rejected") return "未通过全文准入";
  if (fulltext?.status === "failed") return "全文不可用";
  if (fulltext?.status === "requires_upload") return "需要上传已授权 PDF";
  if (fulltext && !isFulltextTerminal(fulltext.status)) return "全文处理中";
  return "待准备核验";
}

function candidateProcessingSummary(
  candidate: Candidate,
  fulltext: FulltextResponse | null,
): string {
  if (!candidate.doi) return "该记录缺少 DOI，不能进入后续研究集合。";
  if (fulltext?.status === "rejected") {
    return fulltext.error?.message || "该文献不满足全文准入条件，不能进入研究集合。";
  }
  if (fulltext?.status === "failed") {
    return fulltext.error?.message || "全文获取失败，可根据提示重试或改选其他文献。";
  }
  if (fulltext?.status === "requires_upload") {
    return (
      fulltext.error?.message || "没有可处理的开放获取 PDF。请在完整记录中确认有权处理后上传文件。"
    );
  }
  if (fulltext?.status === "available") {
    return "DOI、正式题录与可处理全文均已核验，可以加入待确认研究集合。";
  }
  if (fulltext && !isFulltextTerminal(fulltext.status)) {
    return "题录与全文核验正在进行，结果会自动更新到本页。";
  }
  return candidate.citation?.status === "ready"
    ? "题录已通过核验。下一步需要获取并验证可处理的全文。"
    : `${citationReadinessMessage(candidate.citation)} 你可以开始核验，系统会先按 DOI 重新补齐题录。`;
}

async function refreshCandidates(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["candidates", workspaceId.value, runId.value] });
  await queryClient.invalidateQueries({ queryKey: ["search-run", workspaceId.value] });
}

async function refreshCollectionDocuments(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["collection-documents", workspaceId.value] });
}

function restartReviewPolling(): void {
  window.clearInterval(reviewRefreshTimer);
  if (!isPreparing.value && !isSearchRunActive.value) return;
  reviewRefreshTimer = window.setInterval(() => {
    void refreshCandidates();
  }, 1_500);
}

onUnmounted(() => {
  window.clearTimeout(searchDebounceTimer);
  window.clearInterval(reviewRefreshTimer);
});
</script>

<template>
  <section class="stage-view results-view">
    <div class="view-heading results-heading">
      <div>
        <div class="eyebrow">候选文献</div>
        <h1>把候选记录收敛成可研究的文献集合。</h1>
        <p>
          先建立本次准备清单，再批量核验题录和全文。只有通过严格准入的文献，才会进入待确认集合。
        </p>
      </div>
      <button
        class="collection-entry-button"
        type="button"
        :disabled="!pendingCount"
        @click="collectionConfirmOpen = true"
      >
        <Layers2 :size="17" /><span>待确认集合</span><strong>{{ pendingCount }} 篇</strong>
      </button>
    </div>

    <div class="processing-ledger" aria-label="文献处理台账">
      <div class="process-item">
        <strong>多源检索</strong><span>{{ providerCount || "-" }} 个来源已记录状态</span>
      </div>
      <div class="process-item">
        <strong>记录规整</strong
        ><span
          >{{ count("raw_candidate_count") }} 条收集，{{
            count("deduplicated_candidate_count")
          }}
          条合并后保留</span
        >
      </div>
      <div class="process-item current">
        <strong>候选审核</strong
        ><span>本次准备清单 {{ selection.selected_count }} 篇，等待你决定下一步</span>
      </div>
      <div class="process-item">
        <strong>向量索引</strong
        ><span>{{ pendingCount }} 篇待构建，{{ indexedCount }} 篇已可研究</span>
      </div>
    </div>

    <div v-if="providerCount" class="processing-detail" aria-label="来源处理明细">
      <div v-for="(provider, name) in runQuery.data.value?.provider_summary" :key="name">
        <strong>{{ name }}</strong
        ><br /><span
          >{{ provider.status ?? "状态待刷新"
          }}<template v-if="provider.candidate_count"
            >，{{ provider.candidate_count }} 条候选</template
          ></span
        >
      </div>
    </div>

    <div class="results-layout">
      <main class="results-main">
        <div class="results-toolbar">
          <label class="search-input">
            <Search :size="15" /><span class="sr-only">按标题或作者筛选</span
            ><input v-model="searchInput" placeholder="按标题或作者筛选" />
          </label>
          <span class="result-count">{{ page.total }} 条候选，第 {{ currentPageNumber }} 页</span>
        </div>

        <div class="filter-row" aria-label="候选文献筛选">
          <button
            :class="{ active: selectedFilter === 'all' }"
            type="button"
            @click="changeFilter('all')"
          >
            全部
          </button>
          <button
            :class="{ active: selectedFilter === 'zh' }"
            type="button"
            @click="changeFilter('zh')"
          >
            中文文献
          </button>
          <button
            :class="{ active: selectedFilter === 'en' }"
            type="button"
            @click="changeFilter('en')"
          >
            英文文献
          </button>
          <button
            :class="{ active: selectedFilter === 'priority' }"
            type="button"
            @click="changeFilter('priority')"
          >
            优先审核
          </button>
          <button
            :class="{ active: selectedFilter === 'background' }"
            type="button"
            @click="changeFilter('background')"
          >
            背景参考
          </button>
          <button
            :class="{ active: selectedFilter === 'needs_review' }"
            type="button"
            @click="changeFilter('needs_review')"
          >
            需人工核对
          </button>
          <button
            :class="{ active: selectedFilter === 'available' }"
            type="button"
            @click="changeFilter('available')"
          >
            全文已核验
          </button>
          <button
            :class="{ active: selectedFilter === 'open_access' }"
            type="button"
            @click="changeFilter('open_access')"
          >
            开放获取
          </button>
          <button
            :class="{ active: selectedFilter === 'doi' }"
            type="button"
            @click="changeFilter('doi')"
          >
            有 DOI
          </button>
        </div>

        <section
          v-if="selection.selected_count"
          class="selection-action-bar"
          aria-label="本次准备清单操作"
        >
          <div class="selection-action-summary">
            <span>本次准备清单</span><strong>已选 {{ selection.selected_count }} 篇</strong>
            <small
              >待核验 {{ selection.needs_fulltext_count }}，核验中
              {{ selection.fulltext_in_progress_count }}，可入集合
              {{ selection.ready_for_admission_count }}，暂不可用
              {{ selection.blocked_count }}</small
            >
          </div>
          <div class="selection-action-buttons">
            <button
              class="compact-button"
              type="button"
              :disabled="selectionMutation.isPending.value"
              @click="toggleCurrentPageSelection"
            >
              <Check :size="14" />{{ allCurrentPageSelected ? "取消本页选择" : "本页全选" }}
            </button>
            <button class="compact-button" type="button" @click="changeFilter('selected')">
              <ListChecks :size="14" />只看已选
            </button>
            <button class="compact-button" type="button" @click="openVerificationTask">
              <FileDown :size="14" />核验任务
            </button>
            <button
              class="compact-button danger"
              type="button"
              :disabled="clearSelectionMutation.isPending.value"
              @click="clearSelectionMutation.mutate()"
            >
              <X :size="14" />清空选择
            </button>
          </div>
        </section>

        <div
          v-if="candidatesQuery.isPending.value || runQuery.isPending.value"
          class="loading-state"
        >
          <LoaderCircle class="spin" :size="18" />正在读取候选文献…
        </div>
        <div v-else-if="candidatesQuery.isError.value" class="failure-panel">
          <strong>候选会话不可用</strong>
          <p>候选结果可能已过期，或分页条件已经变化。</p>
          <button class="secondary-button" type="button" @click="resetPage">
            <ArrowLeft :size="15" />返回第一页
          </button>
        </div>
        <div v-else class="candidate-table-wrap">
          <table class="candidate-table candidate-review-table">
            <thead>
              <tr>
                <th class="selection-column">
                  <input
                    aria-label="选择当前页可处理候选"
                    type="checkbox"
                    :checked="allCurrentPageSelected"
                    :disabled="!selectablePageItems.length || selectionMutation.isPending.value"
                    @change="toggleCurrentPageSelection"
                  />
                </th>
                <th>文献</th>
                <th>来源与年份</th>
                <th>准入状态</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in reviewItems"
                :key="item.candidate.candidate_id"
                :class="{
                  selected: selectedCandidateId === item.candidate.candidate_id,
                  'review-selected': item.is_selected,
                }"
                tabindex="0"
                @click="selectedCandidateId = item.candidate.candidate_id"
                @keydown.enter="selectedCandidateId = item.candidate.candidate_id"
              >
                <td class="selection-column">
                  <input
                    :aria-label="`选择 ${item.candidate.title}`"
                    type="checkbox"
                    :checked="item.is_selected"
                    :disabled="!isCandidateSelectable(item) || selectionMutation.isPending.value"
                    :title="candidateSelectionHint(item)"
                    @click.stop
                    @change="toggleCandidate(item, !item.is_selected)"
                  />
                </td>
                <td>
                  <div class="candidate-title">
                    <strong>{{ item.candidate.title }}</strong>
                    <div class="candidate-title-footer">
                      <small
                        >{{
                          item.candidate.authors
                            .slice(0, 3)
                            .map((author) => author.name)
                            .join("、") || "作者信息待补全"
                        }}<span v-if="item.candidate.authors.length > 3"> 等</span></small
                      >
                      <span
                        class="candidate-language-tag"
                        :class="`language-${normalizeCandidateLanguage(item.candidate.language)}`"
                        >{{ candidateLanguageLabel(item.candidate.language) }}</span
                      >
                    </div>
                    <div class="candidate-relevance-row" aria-label="候选理由摘要">
                      <span
                        class="candidate-relevance-tier"
                        :class="`tier-${presentCandidateRelevance(item.candidate).tier}`"
                        >{{ presentCandidateRelevance(item.candidate).tierLabel }}</span
                      >
                      <span
                        class="candidate-relevance-summary-inline"
                        :title="presentCandidateRelevance(item.candidate).relevanceSummary"
                        >{{ presentCandidateRelevance(item.candidate).relevanceSummary }}</span
                      >
                    </div>
                  </div>
                </td>
                <td>
                  <span>{{ item.candidate.venue || "未标注来源" }}</span
                  ><small
                    >{{ item.candidate.published_year ?? "年份待补全" }} ·
                    {{ item.candidate.doi ? "DOI 已有" : "无 DOI" }}</small
                  >
                </td>
                <td>
                  <span class="status-text" :class="{ ok: item.fulltext?.status === 'available' }"
                    ><ShieldCheck :size="14" />{{
                      candidateState(item.candidate, item.fulltext)
                    }}</span
                  >
                </td>
                <td>
                  <div class="table-actions">
                    <button
                      class="icon-button"
                      type="button"
                      title="查看详情"
                      @click.stop="
                        router.push({
                          name: 'paper-detail',
                          params: { workspaceId, candidateId: item.candidate.candidate_id },
                          query: { run: runId },
                        })
                      "
                    >
                      <ArrowUpRight :size="16" />
                    </button>
                    <button
                      v-if="item.candidate.citation?.status === 'ready'"
                      class="icon-button"
                      type="button"
                      title="复制 GB/T 7714-2015 引用"
                      :disabled="citationMutation.isPending.value"
                      @click.stop="citationMutation.mutate(item.candidate.candidate_id)"
                    >
                      <Clipboard :size="16" />
                    </button>
                  </div>
                </td>
              </tr>
              <tr v-if="!reviewItems.length">
                <td colspan="5" class="empty-row">没有匹配的候选文献。</td>
              </tr>
            </tbody>
          </table>
        </div>

        <nav class="candidate-pagination" aria-label="候选文献分页">
          <div>
            <label
              >每页
              <select v-model="pageSize" aria-label="每页候选数量">
                <option v-for="size in PAGE_SIZE_OPTIONS" :key="size" :value="size">
                  {{ size }} 条
                </option>
              </select>
            </label>
            <span>第 {{ currentPageNumber }} 页，共 {{ page.total }} 条</span>
          </div>
          <div>
            <button
              class="compact-button"
              type="button"
              :disabled="cursorHistory.length <= 1"
              @click="goToPreviousPage"
            >
              <ChevronLeft :size="15" />上一页
            </button>
            <button
              class="compact-button"
              type="button"
              :disabled="!page.next_cursor"
              @click="goToNextPage"
            >
              下一页<ChevronRight :size="15" />
            </button>
          </div>
        </nav>
      </main>

      <aside class="selection-inspector" aria-label="候选文献检查器">
        <template v-if="selectedCandidate && selectedReviewItem && selectedCandidateReason">
          <div class="inspector-head">
            <strong>候选文献检查器</strong><SlidersHorizontal :size="16" />
          </div>
          <div class="inspector-body">
            <span class="eyebrow">正在查看</span>
            <h2>{{ selectedCandidate.title }}</h2>
            <p class="inspector-meta">
              {{ selectedCandidate.authors.map((author) => author.name).join("、") || "作者待补全"
              }}<br />{{ selectedCandidate.published_year ?? "年份待补全"
              }}<template v-if="selectedCandidate.venue"> · {{ selectedCandidate.venue }}</template
              ><br /><span
                class="candidate-language-tag"
                :class="`language-${normalizeCandidateLanguage(selectedCandidate.language)}`"
                >{{ candidateLanguageLabel(selectedCandidate.language) }}</span
              >
            </p>

            <section class="inspector-section candidate-reason-section">
              <div class="inspector-section-heading">
                <h3>为什么保留这篇候选</h3>
                <span
                  class="candidate-relevance-tier"
                  :class="`tier-${selectedCandidateReason.tier}`"
                  >{{ selectedCandidateReason.tierLabel }}</span
                >
              </div>
              <p class="candidate-reason-summary">{{ selectedCandidateReason.relevanceSummary }}</p>
              <div class="candidate-reason-plain">
                <div>
                  <strong>它主要研究什么</strong>
                  <p>{{ selectedCandidateReason.studyFocus }}</p>
                </div>
                <div>
                  <strong>对当前研究有什么帮助</strong>
                  <p>{{ selectedCandidateReason.helpfulAspect }}</p>
                </div>
                <div>
                  <strong>需要留意</strong>
                  <ul>
                    <li v-for="limitation in selectedCandidateReason.limitations" :key="limitation">
                      {{ limitation }}
                    </li>
                  </ul>
                </div>
                <div class="candidate-recommendation">
                  <strong>建议</strong>
                  <p>{{ selectedCandidateReason.recommendation }}</p>
                </div>
              </div>
              <details
                v-if="selectedCandidateReason.evidence.length"
                class="candidate-evidence-details"
              >
                <summary>查看标题和摘要依据</summary>
                <div class="candidate-evidence-list">
                  <article
                    v-for="evidence in selectedCandidateReason.evidence"
                    :key="`${evidence.label}:${evidence.quote}`"
                  >
                    <span>{{ evidence.label }}</span>
                    <p>{{ evidence.quote }}</p>
                  </article>
                </div>
              </details>
              <p class="candidate-evidence-boundary">
                <strong>说明</strong>{{ selectedCandidateReason.evidenceBoundary }}
              </p>
              <button
                v-if="isRelevanceAnalyzing"
                class="candidate-retry-button"
                type="button"
                :disabled="relevanceCancelMutation.isPending.value"
                @click="relevanceCancelMutation.mutate()"
              >
                <LoaderCircle
                  :size="14"
                  :class="{ 'is-spinning': relevanceCancelMutation.isPending.value }"
                /><span>取消相关性分析</span>
              </button>
              <button
                v-else-if="canRetryRelevanceRun"
                class="candidate-retry-button"
                type="button"
                :disabled="relevanceRetryMutation.isPending.value"
                @click="relevanceRetryMutation.mutate()"
              >
                <LoaderCircle
                  :size="14"
                  :class="{ 'is-spinning': relevanceRetryMutation.isPending.value }"
                /><span>重新分析全部候选理由</span>
              </button>
            </section>

            <details class="inspector-section inspector-processing">
              <summary><span>处理记录</span><small>查看技术状态</small></summary>
              <p>
                {{ candidateProcessingSummary(selectedCandidate, fulltextOf(selectedReviewItem)) }}
              </p>
              <div class="provenance-list">
                <div>
                  <span>身份确认</span
                  ><strong>{{ selectedCandidate.doi ? "DOI 已提供" : "缺少 DOI" }}</strong>
                </div>
                <div>
                  <span>题录核验</span
                  ><strong>{{
                    selectedCandidate.citation?.status === "ready"
                      ? "可生成正式引用"
                      : citationStatusLabel(selectedCandidate.citation)
                  }}</strong>
                </div>
                <div>
                  <span>全文获取</span
                  ><strong>{{ fulltextStatusLabel(fulltextOf(selectedReviewItem)) }}</strong>
                </div>
                <div><span>向量索引</span><strong>加入待确认集合后开始</strong></div>
              </div>
            </details>

            <div class="inspector-actions">
              <button
                v-if="isCandidateSelectable(selectedReviewItem) && !selectedReviewItem.is_selected"
                class="secondary-button"
                type="button"
                :disabled="selectionMutation.isPending.value"
                @click="toggleCandidate(selectedReviewItem, true)"
              >
                <ListChecks :size="15" />加入本次准备清单
              </button>
              <button
                v-if="canRequestFulltext(selectedCandidate, fulltextOf(selectedReviewItem))"
                class="secondary-button"
                type="button"
                :disabled="fulltextMutation.isPending.value"
                @click="fulltextMutation.mutate(selectedCandidate.candidate_id)"
              >
                <FileDown :size="15" />准备单篇核验
              </button>
              <button
                v-else-if="
                  fulltextOf(selectedReviewItem) &&
                  !isFulltextTerminal(fulltextOf(selectedReviewItem)?.status)
                "
                class="secondary-button"
                type="button"
                disabled
              >
                <FileDown :size="15" />{{ fulltextStatusLabel(fulltextOf(selectedReviewItem)) }}
              </button>
              <button
                v-else-if="fulltextOf(selectedReviewItem)?.status === 'available'"
                class="primary-button"
                type="button"
                @click="openVerificationTask"
              >
                <Layers2 :size="15" />前往核验任务加入集合
              </button>
              <button
                class="secondary-button"
                type="button"
                @click="
                  router.push({
                    name: 'paper-detail',
                    params: { workspaceId, candidateId: selectedCandidate.candidate_id },
                    query: { run: runId },
                  })
                "
              >
                <FileSearch :size="15" />查看完整记录
              </button>
            </div>
          </div>
        </template>
        <div v-else class="inspector-empty">
          <FileSearch :size="20" />
          <p>点击一条候选文献，查看它的处理与准入状态。</p>
        </div>
      </aside>
    </div>

    <div class="results-note">
      <Check :size="15" /><span
        >准备清单只保存在当前检索会话中。未通过
        DOI、题录与正文准入前，候选不会写入长期文献库。</span
      >
    </div>

    <Teleport to="body">
      <section
        v-if="collectionConfirmOpen"
        class="collection-confirm-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="collection-confirm-title"
        data-testid="collection-confirm-dialog"
      >
        <div class="collection-confirm-surface">
          <button
            class="icon-button close-dialog"
            type="button"
            title="关闭"
            @click="collectionConfirmOpen = false"
          >
            <X :size="16" />
          </button>
          <span class="eyebrow">待确认集合</span>
          <h2 id="collection-confirm-title">构建这 {{ pendingCount }} 篇文献的可问答集合？</h2>
          <p>
            系统将依次解析全文、切分可引用片段并写入当前工作区的向量索引。构建完成前，研究对话不会使用这些文献。
          </p>
          <dl class="collection-confirm-summary">
            <div>
              <dt>纳入数量</dt>
              <dd>{{ pendingCount }} 篇全文</dd>
            </div>
            <div>
              <dt>固定条件</dt>
              <dd>DOI、题录、全文均已核验</dd>
            </div>
          </dl>
          <div class="collection-confirm-actions">
            <button class="secondary-button" type="button" @click="collectionConfirmOpen = false">
              返回审核</button
            ><button
              class="primary-button"
              type="button"
              :disabled="buildMutation.isPending.value"
              @click="buildMutation.mutate()"
            >
              {{ buildMutation.isPending.value ? "正在启动构建…" : "确认并开始构建"
              }}<ArrowRight :size="16" />
            </button>
          </div>
        </div>
      </section>
    </Teleport>
    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </section>
</template>
