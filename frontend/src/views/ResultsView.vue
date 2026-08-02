<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Clipboard,
  FileDown,
  FileSearch,
  Layers2,
  LoaderCircle,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import {
  admitFulltext,
  buildCollection,
  getCandidateCitation,
  getCollectionDocuments,
  getFulltext,
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
import { getCurrentSearchRun, getSearchCandidates, retryCandidateRelevance } from "@/api/workflow";
import type { Candidate, CandidateLanguage, FulltextResponse } from "@/api/types";

type CandidateFilter =
  | "all"
  | "zh"
  | "en"
  | "priority"
  | "background"
  | "needs_review"
  | "available"
  | "open_access"
  | "doi";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = ref(typeof route.query.run === "string" ? route.query.run : "");
const searchFilter = ref("");
const selectedFilter = ref<CandidateFilter>("all");
const selectedCandidateId = ref<string | null>(null);
const collectionConfirmOpen = ref(false);
const fulltextStates = reactive<Record<string, FulltextResponse>>({});
const toast = ref<string | null>(null);
const timers: number[] = [];

// 运行摘要提供处理台账。候选页从 URL 恢复时仍可通过当前运行补齐该摘要。
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
  queryKey: computed(() => ["candidates", workspaceId.value, runId.value]),
  queryFn: () => getSearchCandidates(workspaceId.value, runId.value),
  enabled: computed(() => Boolean(runId.value)),
  staleTime: 10_000,
});
const collectionQuery = useQuery({
  queryKey: computed(() => ["collection-documents", workspaceId.value]),
  queryFn: () => getCollectionDocuments(workspaceId.value),
});

const allCandidates = computed(() => candidatesQuery.data.value?.candidates ?? []);
const candidates = computed(() => {
  const keyword = searchFilter.value.trim().toLowerCase();
  return allCandidates.value.filter((candidate) => {
    const matchesKeyword =
      !keyword ||
      candidate.title.toLowerCase().includes(keyword) ||
      candidate.authors.some((author) => author.name.toLowerCase().includes(keyword));
    if (!matchesKeyword) return false;
    if (selectedFilter.value === "zh" || selectedFilter.value === "en") {
      return normalizeCandidateLanguage(candidate.language) === selectedFilter.value;
    }
    if (selectedFilter.value === "priority") return isPriorityCandidate(candidate);
    if (selectedFilter.value === "background") {
      return candidate.relevance_assessment?.level === "background";
    }
    if (selectedFilter.value === "needs_review") return needsManualRelevanceReview(candidate);
    if (selectedFilter.value === "available") {
      return fulltextStates[candidate.candidate_id]?.status === "available";
    }
    if (selectedFilter.value === "open_access") return candidate.is_open_access === true;
    if (selectedFilter.value === "doi") return Boolean(candidate.doi);
    return true;
  });
});
const selectedCandidate = computed(
  () =>
    allCandidates.value.find((candidate) => candidate.candidate_id === selectedCandidateId.value) ??
    null,
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
const selectedCandidateReason = computed(() =>
  selectedCandidate.value ? presentCandidateRelevance(selectedCandidate.value) : null,
);

// 筛选条件改变时，检查器只跟随当前可见候选，避免仍展示已被语言标签隐藏的旧记录。
watch(
  candidates,
  (items) => {
    if (!items.length) {
      selectedCandidateId.value = null;
      return;
    }
    if (!items.some((item) => item.candidate_id === selectedCandidateId.value)) {
      selectedCandidateId.value = items[0].candidate_id;
    }
  },
  { immediate: true },
);

const fulltextMutation = useMutation({
  mutationFn: (candidateId: string) => requestFulltext(workspaceId.value, runId.value, candidateId),
  onSuccess: (result) => {
    fulltextStates[result.candidate_id] = result;
    if (!isFulltextTerminal(result.status)) pollFulltext(result.candidate_id);
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "全文任务无法启动。";
  },
});
const relevanceRetryMutation = useMutation({
  mutationFn: (candidateId: string) =>
    retryCandidateRelevance(workspaceId.value, runId.value, candidateId),
  onSuccess: (result, candidateId) => {
    queryClient.setQueryData(["candidates", workspaceId.value, runId.value], result);
    const retried = result.candidates.find((candidate) => candidate.candidate_id === candidateId);
    toast.value =
      retried?.relevance_state === "pending" ? "候选理由正在重新分析。" : "候选理由已重新分析。";
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "候选理由暂时无法重新分析。";
  },
});
const admissionMutation = useMutation({
  mutationFn: (candidateId: string) => admitFulltext(workspaceId.value, runId.value, candidateId),
  onSuccess: () => {
    toast.value = "已加入待确认研究集合。";
    void queryClient.invalidateQueries({ queryKey: ["collection-documents", workspaceId.value] });
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "当前文献还不能加入集合。";
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
    await queryClient.invalidateQueries({ queryKey: ["collection-documents", workspaceId.value] });
    await router.push({ name: "workspace-collection", params: { workspaceId: workspaceId.value } });
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "集合构建无法启动。";
  },
});

function count(key: string): number {
  return Number(runQuery.data.value?.candidate_counts[key] ?? 0);
}

/** 语言筛选按钮展示全量候选计数，不受当前关键词或其他标签影响。 */
function languageCount(language: Extract<CandidateLanguage, "zh" | "en">): number {
  return allCandidates.value.filter(
    (candidate) => normalizeCandidateLanguage(candidate.language) === language,
  ).length;
}

/** 相关性筛选只读取服务端评估状态，不以标题关键词推断候选等级。 */
function isPriorityCandidate(candidate: Candidate): boolean {
  const level = candidate.relevance_assessment?.level;
  return level === "core" || level === "related";
}

/** 未完成、信息不足或低优先级候选保留给用户人工决定，不会被自动删除。 */
function needsManualRelevanceReview(candidate: Candidate): boolean {
  const level = candidate.relevance_assessment?.level;
  return !isPriorityCandidate(candidate) && level !== "background";
}

/** 相关性筛选按钮展示全量计数，和语言筛选保持一致。 */
function relevanceCount(
  filter: Extract<CandidateFilter, "priority" | "background" | "needs_review">,
): number {
  if (filter === "priority") return allCandidates.value.filter(isPriorityCandidate).length;
  if (filter === "background") {
    return allCandidates.value.filter(
      (candidate) => candidate.relevance_assessment?.level === "background",
    ).length;
  }
  return allCandidates.value.filter(needsManualRelevanceReview).length;
}

function pollFulltext(candidateId: string): void {
  const timer = window.setInterval(async () => {
    try {
      const result = await getFulltext(workspaceId.value, runId.value, candidateId);
      fulltextStates[candidateId] = result;
      if (isFulltextTerminal(result.status)) window.clearInterval(timer);
    } catch {
      window.clearInterval(timer);
    }
  }, 1_500);
  timers.push(timer);
}

function fulltextLabel(candidateId: string): string {
  return fulltextStatusLabel(fulltextStates[candidateId]);
}

function candidateState(candidate: Candidate): string {
  const fulltext = fulltextStates[candidate.candidate_id]?.status;
  if (!candidate.doi) return "缺少 DOI";
  if (candidate.citation?.status !== "ready") return citationStatusLabel(candidate.citation);
  if (fulltext === "available") return "可加入集合";
  if (fulltext === "rejected") return "未通过全文准入";
  if (fulltext === "failed") return "全文不可用";
  if (["downloading", "validating", "queued"].includes(fulltext ?? "")) return "全文处理中";
  return "需要全文核验";
}

function candidateProcessingSummary(candidate: Candidate): string {
  if (!candidate.doi) return "该记录缺少 DOI，不能进入后续研究集合。";
  if (candidate.citation?.status !== "ready") return citationReadinessMessage(candidate.citation);
  const fulltext = fulltextStates[candidate.candidate_id];
  if (fulltext?.status === "rejected") {
    return fulltext.error?.message || "该文献不满足全文准入条件，不能进入研究集合。";
  }
  if (fulltextStates[candidate.candidate_id]?.status === "available") {
    return "DOI、正式题录与可处理全文均已核验，可以加入待确认研究集合。";
  }
  return "题录已通过核验。下一步需要获取并验证可处理的全文。";
}

onUnmounted(() => timers.forEach((timer) => window.clearInterval(timer)));
</script>

<template>
  <section class="stage-view results-view">
    <div class="view-heading results-heading">
      <div>
        <div class="eyebrow">候选文献</div>
        <h1>把候选记录收敛成可研究的文献集合。</h1>
        <p>审核题录与全文状态。只有已加入待确认集合的文献，才会在你确认后被解析、切块和索引。</p>
      </div>
      <button
        class="primary-button"
        type="button"
        :disabled="!pendingCount"
        @click="collectionConfirmOpen = true"
      >
        <Layers2 :size="16" /><span>确认 {{ pendingCount }} 篇入集合</span>
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
        <strong>题录与全文</strong
        ><span>{{ count("citation_enriched_count") }} 条题录补全，等待全文核验</span>
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
          <div class="search-input">
            <Search :size="15" /><input v-model="searchFilter" placeholder="按标题或作者筛选" />
          </div>
          <span class="result-count"
            >{{ candidates.length }} / {{ allCandidates.length }} 条候选</span
          >
        </div>
        <div class="filter-row" aria-label="候选文献筛选">
          <button
            :class="{ active: selectedFilter === 'all' }"
            type="button"
            @click="selectedFilter = 'all'"
          >
            全部 {{ allCandidates.length }}
          </button>
          <button
            :class="{ active: selectedFilter === 'zh' }"
            type="button"
            @click="selectedFilter = 'zh'"
          >
            中文文献 {{ languageCount("zh") }}
          </button>
          <button
            :class="{ active: selectedFilter === 'en' }"
            type="button"
            @click="selectedFilter = 'en'"
          >
            英文文献 {{ languageCount("en") }}
          </button>
          <button
            :class="{ active: selectedFilter === 'priority' }"
            type="button"
            @click="selectedFilter = 'priority'"
          >
            优先审核 {{ relevanceCount("priority") }}
          </button>
          <button
            :class="{ active: selectedFilter === 'background' }"
            type="button"
            @click="selectedFilter = 'background'"
          >
            背景参考 {{ relevanceCount("background") }}
          </button>
          <button
            :class="{ active: selectedFilter === 'needs_review' }"
            type="button"
            @click="selectedFilter = 'needs_review'"
          >
            需人工核对 {{ relevanceCount("needs_review") }}
          </button>
          <button
            :class="{ active: selectedFilter === 'available' }"
            type="button"
            @click="selectedFilter = 'available'"
          >
            全文已核验
          </button>
          <button
            :class="{ active: selectedFilter === 'open_access' }"
            type="button"
            @click="selectedFilter = 'open_access'"
          >
            开放获取
          </button>
          <button
            :class="{ active: selectedFilter === 'doi' }"
            type="button"
            @click="selectedFilter = 'doi'"
          >
            有 DOI
          </button>
        </div>
        <div
          v-if="candidatesQuery.isPending.value || runQuery.isPending.value"
          class="loading-state"
        >
          <LoaderCircle class="spin" :size="18" />正在读取候选文献…
        </div>
        <div v-else-if="candidatesQuery.isError.value" class="failure-panel">
          <strong>候选会话不可用</strong>
          <p>候选结果可能已过期，请返回研究入口重新执行检索。</p>
        </div>
        <div v-else class="candidate-table-wrap">
          <table class="candidate-table">
            <thead>
              <tr>
                <th>文献</th>
                <th>来源与年份</th>
                <th>准入状态</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="candidate in candidates"
                :key="candidate.candidate_id"
                :class="{ selected: selectedCandidateId === candidate.candidate_id }"
                tabindex="0"
                @click="selectedCandidateId = candidate.candidate_id"
                @keydown.enter="selectedCandidateId = candidate.candidate_id"
              >
                <td>
                  <div class="candidate-title">
                    <strong>{{ candidate.title }}</strong>
                    <div class="candidate-title-footer">
                      <small
                        >{{
                          candidate.authors
                            .slice(0, 3)
                            .map((author) => author.name)
                            .join("、") || "作者信息待补全"
                        }}<span v-if="candidate.authors.length > 3"> 等</span></small
                      ><span
                        class="candidate-language-tag"
                        :class="`language-${normalizeCandidateLanguage(candidate.language)}`"
                        >{{ candidateLanguageLabel(candidate.language) }}</span
                      >
                    </div>
                    <div class="candidate-relevance-row" aria-label="候选理由摘要">
                      <span
                        class="candidate-relevance-tier"
                        :class="`tier-${presentCandidateRelevance(candidate).tier}`"
                        >{{ presentCandidateRelevance(candidate).tierLabel }}</span
                      >
                      <span
                        class="candidate-relevance-summary-inline"
                        :title="presentCandidateRelevance(candidate).relevanceSummary"
                        >{{ presentCandidateRelevance(candidate).relevanceSummary }}</span
                      >
                    </div>
                  </div>
                </td>
                <td>
                  <span>{{ candidate.venue || "未标注来源" }}</span
                  ><small
                    >{{ candidate.published_year ?? "年份待补全" }} ·
                    {{ candidate.doi ? "DOI 已有" : "无 DOI" }}</small
                  >
                </td>
                <td>
                  <span
                    class="status-text"
                    :class="{ ok: fulltextStates[candidate.candidate_id]?.status === 'available' }"
                    ><ShieldCheck :size="14" />{{ candidateState(candidate) }}</span
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
                          params: { workspaceId, candidateId: candidate.candidate_id },
                          query: { run: runId },
                        })
                      "
                    >
                      <ArrowUpRight :size="16" />
                    </button>
                    <button
                      v-if="candidate.citation?.status === 'ready'"
                      class="icon-button"
                      type="button"
                      title="复制 GB/T 7714-2015 引用"
                      :disabled="citationMutation.isPending.value"
                      @click.stop="citationMutation.mutate(candidate.candidate_id)"
                    >
                      <Clipboard :size="16" />
                    </button>
                  </div>
                </td>
              </tr>
              <tr v-if="!candidates.length">
                <td colspan="4" class="empty-row">没有匹配的候选文献。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </main>

      <aside class="selection-inspector" aria-label="候选文献检查器">
        <template v-if="selectedCandidate && selectedCandidateReason">
          <div class="inspector-head">
            <strong>候选文献检查器</strong><SlidersHorizontal :size="16" />
          </div>
          <div class="inspector-body">
            <span class="eyebrow">当前选择</span>
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
                v-if="selectedCandidateReason.canRetry"
                class="candidate-retry-button"
                type="button"
                :disabled="relevanceRetryMutation.isPending.value"
                @click="relevanceRetryMutation.mutate(selectedCandidate.candidate_id)"
              >
                <LoaderCircle
                  :size="14"
                  :class="{ 'is-spinning': relevanceRetryMutation.isPending.value }"
                />
                <span>重新分析候选理由</span>
              </button>
            </section>
            <details class="inspector-section inspector-processing">
              <summary><span>处理记录</span><small>查看技术状态</small></summary>
              <p>{{ candidateProcessingSummary(selectedCandidate) }}</p>
              <div class="provenance-list">
                <div>
                  <span>身份确认</span
                  ><strong>{{ selectedCandidate.doi ? "DOI 已提供" : "缺少 DOI" }}</strong>
                </div>
                <div>
                  <span>文献语言</span
                  ><strong>{{ candidateLanguageLabel(selectedCandidate.language) }}</strong>
                </div>
                <div>
                  <span>题录核验</span
                  ><strong>{{
                    selectedCandidate.citation?.status === "ready"
                      ? "可生成正式引用"
                      : selectedCandidate.citation?.status === "conflict"
                        ? "题录存在冲突"
                        : "尚未完成核验"
                  }}</strong>
                </div>
                <div>
                  <span>全文获取</span
                  ><strong>{{ fulltextLabel(selectedCandidate.candidate_id) }}</strong>
                </div>
                <div><span>向量索引</span><strong>确认集合后开始</strong></div>
              </div>
            </details>
            <div class="inspector-actions">
              <button
                v-if="
                  canRequestFulltext(
                    selectedCandidate,
                    fulltextStates[selectedCandidate.candidate_id],
                  )
                "
                class="secondary-button"
                type="button"
                :disabled="fulltextMutation.isPending.value"
                @click="fulltextMutation.mutate(selectedCandidate.candidate_id)"
              >
                <FileDown :size="15" />{{ fulltextLabel(selectedCandidate.candidate_id) }}
              </button>
              <button
                v-else-if="
                  fulltextStates[selectedCandidate.candidate_id] &&
                  !isFulltextTerminal(fulltextStates[selectedCandidate.candidate_id]?.status)
                "
                class="secondary-button"
                type="button"
                disabled
              >
                <FileDown :size="15" />{{ fulltextLabel(selectedCandidate.candidate_id) }}
              </button>
              <button
                v-else-if="fulltextStates[selectedCandidate.candidate_id]?.status === 'available'"
                class="primary-button"
                type="button"
                :disabled="admissionMutation.isPending.value"
                @click="admissionMutation.mutate(selectedCandidate.candidate_id)"
              >
                <Plus :size="15" />加入待确认集合
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
          <p>选择一条候选文献，查看它的处理与准入状态。</p>
        </div>
      </aside>
    </div>

    <div class="results-note">
      <Check :size="15" /><span
        >候选只保存在当前检索会话中，未通过 DOI、题录与正文准入前不会写入长期文献库。</span
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
          <span class="eyebrow">确认研究集合</span>
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
