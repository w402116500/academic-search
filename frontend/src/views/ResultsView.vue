<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import {
  ArrowRight,
  Check,
  FileDown,
  FileSearch,
  Layers2,
  ListChecks,
  SlidersHorizontal,
  X,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import { useCandidateLiteratureMutations } from "@/api/hooks/literature";
import { useCollectionDocumentsQuery, useCollectionMutations } from "@/api/hooks/research";
import {
  useCurrentSearchRunQuery,
  useSearchCandidatesQuery,
  useSearchReviewMutations,
} from "@/api/hooks/search";
import {
  canRequestFulltext,
  citationStatusLabel,
  fulltextStatusLabel,
  isFulltextTerminal,
} from "@/features/search/search-run-state";
import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/search/candidate-language";
import { presentCandidateRelevance } from "@/features/search/candidate-relevance";
import { candidateProcessingSummary } from "@/features/search/candidate-review-presentation";
import { useReviewPolling } from "@/features/search/use-review-polling";
import CandidateReviewTable from "@/features/search/CandidateReviewTable.vue";
import type {
  CandidateCounts,
  CandidateReviewFilter,
  CandidateReviewItem,
  FulltextResponse,
} from "@/api/types";

const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = ref(typeof route.query.run === "string" ? route.query.run : "");
const searchInput = ref("");
const searchQuery = ref("");
const selectedFilter = ref<CandidateReviewFilter>("all");
const pageSize = ref<20 | 50>(20);
const cursorHistory = ref<Array<string | null>>([null]);
const selectedCandidateId = ref<string | null>(null);
const collectionConfirmOpen = ref(false);
const toast = ref<string | null>(null);
let searchDebounceTimer: number | undefined;

const activeCursor = computed(() => cursorHistory.value.at(-1) ?? null);
const currentPageNumber = computed(() => cursorHistory.value.length);

const runQuery = useCurrentSearchRunQuery(workspaceId);

watch(
  () => runQuery.data.value?.id,
  (id) => {
    if (id && !runId.value) runId.value = id;
  },
  { immediate: true },
);

const candidatesQuery = useSearchCandidatesQuery(workspaceId, runId, {
  limit: pageSize,
  cursor: activeCursor,
  query: searchQuery,
  filter: selectedFilter,
});
const collectionQuery = useCollectionDocumentsQuery(workspaceId);
const { selectionMutation, clearSelectionMutation, refreshCandidates } = useSearchReviewMutations(
  workspaceId,
  runId,
);
const { requestFulltextMutation: fulltextMutation, citationMutation } =
  useCandidateLiteratureMutations(workspaceId, runId);
const { buildCollectionMutation: buildMutation } = useCollectionMutations(workspaceId);

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
  () => collectionQuery.data.value?.summary.ingestion_status_counts?.pending ?? 0,
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
const isPreparing = computed(() =>
  reviewItems.value.some((item) => item.fulltext && !isFulltextTerminal(item.fulltext.status)),
);
const isSearchRunActive = computed(() =>
  ["queued", "running"].includes(runQuery.data.value?.status ?? ""),
);
const shouldPollReview = computed(() => isPreparing.value || isSearchRunActive.value);
const { restart: restartReviewPolling } = useReviewPolling(shouldPollReview, refreshCandidates);

watch(
  reviewItems,
  (items) => {
    if (!items.length) {
      selectedCandidateId.value = null;
      return;
    }
    if (!items.some((item) => item.candidate.candidate_id === selectedCandidateId.value)) {
      selectedCandidateId.value = items.at(0)?.candidate.candidate_id ?? null;
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

function resetPage(): void {
  cursorHistory.value = [null];
  selectedCandidateId.value = null;
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

function updateSelection(candidateIds: string[], selected: boolean): void {
  selectionMutation.mutate(
    { candidateIds, selected },
    {
      onError: (error) => {
        toast.value = error instanceof Error ? error.message : "候选选择无法同步。";
      },
    },
  );
}

function isCandidateSelectable(item: CandidateReviewItem | null): boolean {
  return Boolean(item?.candidate.doi && item.candidate.triage?.included);
}

function addSelectedCandidate(): void {
  if (selectedReviewItem.value) {
    updateSelection([selectedReviewItem.value.candidate.candidate_id], true);
  }
}

function clearSelection(): void {
  clearSelectionMutation.mutate(undefined, {
    onSuccess: () => {
      toast.value = "本次准备清单已清空。";
    },
    onError: (error) => {
      toast.value = error instanceof Error ? error.message : "准备清单无法清空。";
    },
  });
}

function requestCandidateFulltext(candidateId: string): void {
  fulltextMutation.mutate(candidateId, {
    onSuccess: () => {
      toast.value = "单篇题录与全文核验已安排。";
      restartReviewPolling();
    },
    onError: (error) => {
      toast.value = error instanceof Error ? error.message : "全文任务无法启动。";
    },
  });
}

function copyCandidateCitation(candidateId: string): void {
  citationMutation.mutate(candidateId, {
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
}

function startCollectionBuild(): void {
  buildMutation.mutate(undefined, {
    onSuccess: async () => {
      collectionConfirmOpen.value = false;
      toast.value = "集合构建任务已启动。";
      await router.push({
        name: "workspace-collection",
        params: { workspaceId: workspaceId.value },
      });
    },
    onError: (error) => {
      toast.value = error instanceof Error ? error.message : "集合构建无法启动。";
    },
  });
}

function openVerificationTask(): void {
  void router.push({
    name: "workspace-verification",
    params: { workspaceId: workspaceId.value },
    query: { run: runId.value },
  });
}

function openPaperDetail(candidateId: string): void {
  void router.push({
    name: "paper-detail",
    params: { workspaceId: workspaceId.value, candidateId },
    query: { run: runId.value },
  });
}

function count(key: string): number {
  return Number(
    countValue(candidatesQuery.data.value?.candidate_counts, key) ??
      countValue(runQuery.data.value?.candidate_counts, key) ??
      0,
  );
}

function countValue(counts: CandidateCounts | undefined, key: string): unknown {
  return counts ? (counts as Record<string, unknown>)[key] : undefined;
}

function fulltextOf(item: CandidateReviewItem | null): FulltextResponse | null {
  return item?.fulltext ?? null;
}

onUnmounted(() => {
  window.clearTimeout(searchDebounceTimer);
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
      <CandidateReviewTable
        v-model:search-input="searchInput"
        v-model:selected-filter="selectedFilter"
        v-model:page-size="pageSize"
        v-model:selected-candidate-id="selectedCandidateId"
        :items="reviewItems"
        :selection="selection"
        :page="page"
        :current-page-number="currentPageNumber"
        :cursor-depth="cursorHistory.length"
        :loading="candidatesQuery.isPending.value || runQuery.isPending.value"
        :error="candidatesQuery.isError.value"
        :search-run-active="isSearchRunActive"
        :selection-pending="selectionMutation.isPending.value"
        :clear-pending="clearSelectionMutation.isPending.value"
        :citation-pending="citationMutation.isPending.value"
        @toggle-selection="updateSelection"
        @clear-selection="clearSelection"
        @open-verification="openVerificationTask"
        @reset-page="resetPage"
        @previous-page="goToPreviousPage"
        @next-page="goToNextPage"
        @open-detail="openPaperDetail"
        @copy-citation="copyCandidateCitation"
      />

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
                @click="addSelectedCandidate"
              >
                <ListChecks :size="15" />加入本次准备清单
              </button>
              <button
                v-if="canRequestFulltext(selectedCandidate, fulltextOf(selectedReviewItem))"
                class="secondary-button"
                type="button"
                :disabled="fulltextMutation.isPending.value"
                @click="requestCandidateFulltext(selectedCandidate.candidate_id)"
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
              @click="startCollectionBuild"
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
