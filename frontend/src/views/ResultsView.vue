<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { Check, FileSearch, Layers2, ListChecks, SlidersHorizontal } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import { useCandidateLiteratureMutations } from "@/api/hooks/literature";
import { useCollectionDocumentsQuery } from "@/api/hooks/research";
import {
  useCurrentSearchRunQuery,
  useSearchCandidatesQuery,
  useSearchReviewMutations,
} from "@/api/hooks/search";
import {
  candidatePdfAvailabilityLabel,
  citationStatusLabel,
} from "@/features/search/search-run-state";
import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/search/candidate-language";
import { presentCandidateRelevance } from "@/features/search/candidate-relevance";
import { useReviewPolling } from "@/features/search/use-review-polling";
import CandidateReviewTable from "@/features/search/CandidateReviewTable.vue";
import type { CandidateCounts, CandidateReviewFilter, CandidateReviewItem } from "@/api/types";

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
const { selectionMutation, clearSelectionMutation, admitSelectionMutation, refreshCandidates } =
  useSearchReviewMutations(workspaceId, runId);
const { citationMutation } = useCandidateLiteratureMutations(workspaceId, runId);

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
const isSearchRunActive = computed(() =>
  ["queued", "running"].includes(runQuery.data.value?.status ?? ""),
);
const shouldPollReview = computed(() => isSearchRunActive.value);
useReviewPolling(shouldPollReview, refreshCandidates);

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
  return Boolean(item?.candidate.triage?.included);
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

function admitSelectedCandidates(): void {
  admitSelectionMutation.mutate(undefined, {
    onSuccess: async (response) => {
      const admittedCount = response.admitted_count + response.already_joined_count;
      toast.value = `已加入研究集合 ${admittedCount} 篇。`;
      await router.push({
        name: "workspace-collection",
        params: { workspaceId: workspaceId.value },
      });
    },
    onError: (error) => {
      toast.value = error instanceof Error ? error.message : "候选文献无法加入研究集合。";
    },
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
          检索完成后系统会自动核验题录与公开 PDF 可得性。你只需要选择要保留的文献并加入研究集合。
        </p>
      </div>
      <button
        class="collection-entry-button"
        type="button"
        @click="router.push({ name: 'workspace-collection', params: { workspaceId } })"
      >
        <Layers2 :size="17" /><span>研究集合</span><strong>{{ indexedCount }} 篇可研究</strong>
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
        ><span>已选 {{ selection.selected_count }} 篇，可直接加入研究集合</span>
      </div>
      <div class="process-item">
        <strong>RAG 研究范围</strong
        ><span>{{ pendingCount }} 篇正在入库，{{ indexedCount }} 篇已可研究</span>
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
        :selection-pending="
          selectionMutation.isPending.value || admitSelectionMutation.isPending.value
        "
        :clear-pending="clearSelectionMutation.isPending.value"
        :citation-pending="citationMutation.isPending.value"
        @toggle-selection="updateSelection"
        @clear-selection="clearSelection"
        @admit-selection="admitSelectedCandidates"
        @reset-page="resetPage"
        @previous-page="goToPreviousPage"
        @next-page="goToNextPage"
        @open-detail="openPaperDetail"
        @copy-citation="copyCandidateCitation"
      />

      <aside class="selection-inspector" aria-label="文献详情">
        <template v-if="selectedCandidate && selectedReviewItem && selectedCandidateReason">
          <div class="inspector-head">
            <strong>文献详情</strong><SlidersHorizontal :size="16" />
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

            <section class="inspector-section">
              <div class="inspector-section-heading">
                <h3>正式引用</h3>
                <span
                  class="status-text"
                  :class="{ ok: selectedCandidate.citation?.status === 'ready' }"
                  ><Check :size="14" />{{ citationStatusLabel(selectedCandidate.citation) }}</span
                >
              </div>
              <p>
                {{
                  selectedCandidate.citation?.status === "ready"
                    ? "该题录已核验，可复制正式引用。"
                    : "该题录暂不可用，加入研究集合不受影响。"
                }}
              </p>
            </section>

            <section class="inspector-section">
              <div class="inspector-section-heading">
                <h3>PDF 可用性</h3>
                <span
                  class="status-text"
                  :class="{ ok: selectedCandidate.pdf_availability?.status === 'available' }"
                  ><Layers2 :size="14" />{{
                    candidatePdfAvailabilityLabel(selectedCandidate)
                  }}</span
                >
              </div>
              <p>
                {{
                  selectedCandidate.pdf_availability?.status === "available"
                    ? "加入研究集合后，系统会自动获取 PDF 并尝试入库。"
                    : "加入研究集合后会保留书目，并在集合页显示上传入口。"
                }}
              </p>
            </section>

            <section class="inspector-section candidate-reason-section">
              <div class="inspector-section-heading">
                <h3>相关性依据</h3>
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

            <div class="inspector-actions">
              <button
                v-if="isCandidateSelectable(selectedReviewItem) && !selectedReviewItem.is_selected"
                class="secondary-button"
                type="button"
                :disabled="selectionMutation.isPending.value"
                @click="addSelectedCandidate"
              >
                <ListChecks :size="15" />加入选择
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
        >候选选择会保存为研究集合书目；只有 PDF 已获取、解析和入库完成的文献会进入 RAG
        研究范围。</span
      >
    </div>

    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </section>
</template>
