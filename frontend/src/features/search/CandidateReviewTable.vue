<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  ListChecks,
  LoaderCircle,
  Search,
  ShieldCheck,
  X,
} from "@lucide/vue";

import { candidateLanguageLabel, normalizeCandidateLanguage } from "./candidate-language";
import { presentCandidateRelevance } from "./candidate-relevance";
import { candidatePdfAvailabilityLabel, citationStatusLabel } from "./search-run-state";
import type {
  CandidateReviewFilter,
  CandidateReviewItem,
  CandidateSelectionSummary,
  SearchCandidatePageResponse,
} from "@/api/types";

const PAGE_SIZE_OPTIONS = [20, 50] as const;

const props = defineProps<{
  items: CandidateReviewItem[];
  selection: CandidateSelectionSummary;
  page: SearchCandidatePageResponse["page"];
  currentPageNumber: number;
  cursorDepth: number;
  loading: boolean;
  error: boolean;
  searchRunActive: boolean;
  selectionPending: boolean;
  clearPending: boolean;
  citationPending: boolean;
}>();

const emit = defineEmits<{
  toggleSelection: [candidateIds: string[], selected: boolean];
  clearSelection: [];
  admitSelection: [];
  resetPage: [];
  previousPage: [];
  nextPage: [];
  openDetail: [candidateId: string];
  copyCitation: [candidateId: string];
}>();

const searchInput = defineModel<string>("searchInput", { required: true });
const selectedFilter = defineModel<CandidateReviewFilter>("selectedFilter", { required: true });
const pageSize = defineModel<number>("pageSize", { required: true });
const selectedCandidateId = defineModel<string | null>("selectedCandidateId", { required: true });

const selectablePageItems = computed(() => props.items.filter(isCandidateSelectable));
const allCurrentPageSelected = computed(
  () =>
    selectablePageItems.value.length > 0 &&
    selectablePageItems.value.every((item) => item.is_selected),
);

function isCandidateSelectable(item: CandidateReviewItem): boolean {
  return Boolean(item.candidate.triage?.included);
}

function candidateSelectionHint(item: CandidateReviewItem): string {
  if (!item.candidate.triage?.included) return "未通过基础筛选，不能进入研究集合。";
  return "加入研究集合选择。";
}

function toggleCurrentPageSelection(): void {
  const candidateIds = selectablePageItems.value.map((item) => item.candidate.candidate_id);
  if (!candidateIds.length) return;
  emit("toggleSelection", candidateIds, !allCurrentPageSelected.value);
}
</script>

<template>
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
        v-for="filter in [
          { key: 'all', label: '全部' },
          { key: 'zh', label: '中文文献' },
          { key: 'en', label: '英文文献' },
          { key: 'priority', label: '优先审核' },
          { key: 'background', label: '背景参考' },
          { key: 'available', label: '可自动获取 PDF' },
          { key: 'open_access', label: '开放获取' },
          { key: 'doi', label: '有 DOI' },
        ]"
        :key="filter.key"
        :class="{ active: selectedFilter === filter.key }"
        type="button"
        @click="selectedFilter = filter.key as CandidateReviewFilter"
      >
        {{ filter.label }}
      </button>
    </div>

    <section
      v-if="selection.selected_count"
      class="selection-action-bar"
      aria-label="本次准备清单操作"
    >
      <div class="selection-action-summary">
        <span>准备加入研究集合</span><strong>已选 {{ selection.selected_count }} 篇</strong>
        <small>加入后，已探测到公开 PDF 的文献会自动入库，其余保留为需上传 PDF。</small>
      </div>
      <div class="selection-action-buttons">
        <button
          class="compact-button"
          type="button"
          :disabled="selectionPending"
          @click="toggleCurrentPageSelection"
        >
          <Check :size="14" />{{ allCurrentPageSelected ? "取消本页选择" : "本页全选" }}
        </button>
        <button class="compact-button" type="button" @click="selectedFilter = 'selected'">
          <ListChecks :size="14" />只看已选
        </button>
        <button
          class="compact-button primary-compact"
          type="button"
          :disabled="selectionPending"
          @click="emit('admitSelection')"
        >
          <ListChecks :size="14" />加入研究集合（{{ selection.selected_count }}）
        </button>
        <button
          class="compact-button danger"
          type="button"
          :disabled="clearPending"
          @click="emit('clearSelection')"
        >
          <X :size="14" />清空选择
        </button>
      </div>
    </section>

    <div v-if="loading" class="loading-state">
      <LoaderCircle class="spin" :size="18" />正在读取候选文献…
    </div>
    <div v-else-if="error" class="failure-panel">
      <strong>候选会话不可用</strong>
      <p>候选结果可能已过期，或分页条件已经变化。</p>
      <button class="secondary-button" type="button" @click="emit('resetPage')">
        <ArrowLeft :size="15" />返回第一页
      </button>
    </div>
    <div v-else class="candidate-table-wrap">
      <table class="candidate-table candidate-review-table">
        <thead>
          <tr>
            <th class="selection-column">
              <input
                aria-label="选择当前页候选"
                type="checkbox"
                :checked="allCurrentPageSelected"
                :disabled="!selectablePageItems.length || selectionPending"
                @change="toggleCurrentPageSelection"
              />
            </th>
            <th>文献</th>
            <th>来源与年份</th>
            <th>题录与 PDF</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
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
                :aria-label="'选择 ' + item.candidate.title"
                type="checkbox"
                :checked="item.is_selected"
                :disabled="!isCandidateSelectable(item) || selectionPending"
                :title="candidateSelectionHint(item)"
                @click.stop
                @change="emit('toggleSelection', [item.candidate.candidate_id], !item.is_selected)"
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
                    :class="'language-' + normalizeCandidateLanguage(item.candidate.language)"
                    >{{ candidateLanguageLabel(item.candidate.language) }}</span
                  >
                </div>
                <div class="candidate-relevance-row" aria-label="候选理由摘要">
                  <span
                    class="candidate-relevance-tier"
                    :class="'tier-' + presentCandidateRelevance(item.candidate).tier"
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
              <div class="candidate-status-stack">
                <span
                  class="status-text"
                  :class="{ ok: item.candidate.citation?.status === 'ready' }"
                  ><ShieldCheck :size="14" />{{
                    citationStatusLabel(item.candidate.citation)
                  }}</span
                >
                <span
                  class="status-text"
                  :class="{ ok: item.candidate.pdf_availability?.status === 'available' }"
                  ><ShieldCheck :size="14" />{{
                    candidatePdfAvailabilityLabel(item.candidate)
                  }}</span
                >
              </div>
            </td>
            <td>
              <div class="table-actions">
                <button
                  class="icon-button"
                  type="button"
                  title="查看详情"
                  @click.stop="emit('openDetail', item.candidate.candidate_id)"
                >
                  <ArrowUpRight :size="16" />
                </button>
                <button
                  v-if="item.candidate.citation?.status === 'ready'"
                  class="icon-button"
                  type="button"
                  title="复制 GB/T 7714-2015 引用"
                  :disabled="citationPending"
                  @click.stop="emit('copyCitation', item.candidate.candidate_id)"
                >
                  <Clipboard :size="16" />
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="5" class="empty-row">
              {{ searchRunActive ? "正在筛选适合当前研究的文献…" : "没有匹配的候选文献。" }}
            </td>
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
          :disabled="cursorDepth <= 1"
          @click="emit('previousPage')"
        >
          <ChevronLeft :size="15" />上一页
        </button>
        <button
          class="compact-button"
          type="button"
          :disabled="!page.next_cursor"
          @click="emit('nextPage')"
        >
          下一页<ChevronRight :size="15" />
        </button>
      </div>
    </nav>
  </main>
</template>
