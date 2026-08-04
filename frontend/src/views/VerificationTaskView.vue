<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileCheck2,
  FileDown,
  Layers2,
  LoaderCircle,
  RefreshCw,
  Upload,
  X,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import { getCollectionDocuments, requestFulltext } from "@/api/collections";
import {
  isFulltextTerminal,
  presentFulltextVerification,
} from "@/features/research/search-run-state";
import {
  admitCandidateSelection,
  getSearchCandidates,
  prepareCandidateSelection,
  updateCandidateSelection,
} from "@/api/workflow";
import type { CandidateReviewItem } from "@/api/types";

const PAGE_SIZE = 20;
const POLLING_INTERVAL_MS = 1_500;

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = computed(() => (typeof route.query.run === "string" ? route.query.run : ""));
const cursorHistory = ref<Array<string | null>>([null]);
const admissionConfirmOpen = ref(false);
const toast = ref<string | null>(null);
const lastAdmissionCount = ref<number | null>(null);
let refreshTimer: number | undefined;

const activeCursor = computed(() => cursorHistory.value.at(-1) ?? null);
const currentPageNumber = computed(() => cursorHistory.value.length);
const verificationQuery = useQuery({
  queryKey: computed(() => [
    "verification-candidates",
    workspaceId.value,
    runId.value,
    activeCursor.value,
  ]),
  queryFn: () =>
    getSearchCandidates(workspaceId.value, runId.value, {
      limit: PAGE_SIZE,
      cursor: activeCursor.value,
      filter: "selected",
    }),
  enabled: computed(() => Boolean(runId.value)),
  staleTime: 3_000,
});
const collectionQuery = useQuery({
  queryKey: computed(() => ["collection-documents", workspaceId.value]),
  queryFn: () => getCollectionDocuments(workspaceId.value),
});

const reviewItems = computed(() => verificationQuery.data.value?.items ?? []);
const selection = computed(
  () =>
    verificationQuery.data.value?.selection ?? {
      selected_count: 0,
      needs_fulltext_count: 0,
      fulltext_in_progress_count: 0,
      ready_for_admission_count: 0,
      blocked_count: 0,
    },
);
const page = computed(
  () => verificationQuery.data.value?.page ?? { limit: PAGE_SIZE, total: 0, next_cursor: null },
);
const pendingCollectionCount = computed(
  () => collectionQuery.data.value?.summary.ingestion_status_counts.pending ?? 0,
);
const hasActiveVerification = computed(() => selection.value.fulltext_in_progress_count > 0);
const canStartVerification = computed(
  () => selection.value.selected_count > 0 && selection.value.needs_fulltext_count > 0,
);
const verificationHeading = computed(() => {
  if (!selection.value.selected_count) return "本次核验任务已经交接。";
  if (hasActiveVerification.value) return `正在准备 ${selection.value.selected_count} 篇候选文献。`;
  if (selection.value.ready_for_admission_count) {
    return `${selection.value.ready_for_admission_count} 篇文献已经可以加入集合。`;
  }
  return `等待核验 ${selection.value.selected_count} 篇候选文献。`;
});
const verificationDescription = computed(() => {
  if (!selection.value.selected_count) {
    return "已加入的文献会在待确认集合中等待统一构建，其余候选可继续回到结果页审核。";
  }
  if (hasActiveVerification.value) {
    return "系统正在按文献逐篇获取和校验全文，已经通过的文献可以先加入待确认集合。";
  }
  if (selection.value.ready_for_admission_count) {
    return "只有题录与可处理全文均已通过核验的文献，才会出现在可加入范围。";
  }
  return "点击开始核验后，系统会对本次准备清单逐篇安排题录与全文核验。";
});
const pageHeading = computed(() => {
  if (verificationQuery.isPending.value) return "正在读取核验任务。";
  if (verificationQuery.isError.value) return "暂时无法打开核验任务。";
  return verificationHeading.value;
});
const pageDescription = computed(() => {
  if (verificationQuery.isPending.value)
    return "正在同步本次准备清单、全文任务和待确认集合的最新状态。";
  if (verificationQuery.isError.value) return "准备清单可能已经过期，或当前检索运行不再可访问。";
  return verificationDescription.value;
});
const commandHeading = computed(() => {
  if (hasActiveVerification.value) return "核验正在进行，已通过的文献不必等待。";
  if (selection.value.ready_for_admission_count) return "已通过核验的文献可以先加入集合。";
  return "先核验，再决定纳入。";
});
const commandDescription = computed(() => {
  if (hasActiveVerification.value) {
    return "本页会自动更新任务状态。加入动作只处理当前已通过核验的文献，不会中断其余候选。";
  }
  if (selection.value.ready_for_admission_count) {
    return "加入后，文献进入待确认集合，后续由集合构建任务解析与建立向量索引。";
  }
  return "开始核验后，系统会逐篇处理 DOI 题录与可公开处理的全文。";
});

const prepareMutation = useMutation({
  mutationFn: () => prepareCandidateSelection(workspaceId.value, runId.value),
  onSuccess: async (result) => {
    toast.value =
      result.queued_count > 0
        ? `已安排 ${result.queued_count} 篇候选进行题录与全文核验。`
        : "已同步本次准备清单的核验状态。";
    await refreshVerification();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "批量核验无法启动。";
  },
});
const retryMutation = useMutation({
  mutationFn: (candidateId: string) => requestFulltext(workspaceId.value, runId.value, candidateId),
  onSuccess: async () => {
    toast.value = "已重新安排该篇文献的核验。";
    await refreshVerification();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "该篇文献暂时无法重新核验。";
  },
});
const removeMutation = useMutation({
  mutationFn: (candidateId: string) =>
    updateCandidateSelection(workspaceId.value, runId.value, [candidateId], false),
  onSuccess: async () => {
    toast.value = "已从本次准备清单移除。";
    await refreshVerification();
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "准备清单无法更新。";
  },
});
const admissionMutation = useMutation({
  mutationFn: () => admitCandidateSelection(workspaceId.value, runId.value),
  onSuccess: async (result) => {
    admissionConfirmOpen.value = false;
    lastAdmissionCount.value = result.admitted_count;
    toast.value =
      result.admitted_count > 0
        ? `已将 ${result.admitted_count} 篇文献加入待确认集合。`
        : "当前准备清单中没有可立即加入集合的文献。";
    await Promise.all([refreshVerification(), refreshCollectionDocuments()]);
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "批量加入集合无法完成。";
  },
});

function fulltextPresentation(item: CandidateReviewItem) {
  return presentFulltextVerification(item.fulltext);
}

function canRetry(item: CandidateReviewItem): boolean {
  return fulltextPresentation(item).retryable;
}

function canRemove(item: CandidateReviewItem): boolean {
  return !item.fulltext || isFulltextTerminal(item.fulltext.status);
}

function requiresAuthorizedUpload(item: CandidateReviewItem): boolean {
  return item.fulltext?.status === "requires_upload";
}

function openCandidateUpload(candidateId: string): void {
  void router.push({
    name: "paper-detail",
    params: { workspaceId: workspaceId.value, candidateId },
    query: { run: runId.value },
  });
}

async function refreshVerification(): Promise<void> {
  await queryClient.invalidateQueries({
    queryKey: ["verification-candidates", workspaceId.value, runId.value],
  });
}

async function refreshCollectionDocuments(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ["collection-documents", workspaceId.value] });
}

function restartPolling(): void {
  window.clearInterval(refreshTimer);
  if (!hasActiveVerification.value) return;
  refreshTimer = window.setInterval(() => void refreshVerification(), POLLING_INTERVAL_MS);
}

function goToPreviousPage(): void {
  if (cursorHistory.value.length <= 1) return;
  cursorHistory.value = cursorHistory.value.slice(0, -1);
}

function goToNextPage(): void {
  const nextCursor = page.value.next_cursor;
  if (!nextCursor) return;
  cursorHistory.value = [...cursorHistory.value, nextCursor];
}

watch(hasActiveVerification, restartPolling, { immediate: true });
onUnmounted(() => window.clearInterval(refreshTimer));
</script>

<template>
  <section class="stage-view verification-view">
    <div class="view-heading verification-heading">
      <div>
        <div class="eyebrow">本次准备清单</div>
        <h1>{{ pageHeading }}</h1>
        <p>{{ pageDescription }}</p>
      </div>
      <div class="verification-heading-actions">
        <button
          v-if="pendingCollectionCount"
          class="secondary-button"
          type="button"
          @click="router.push({ name: 'workspace-collection', params: { workspaceId } })"
        >
          <Layers2 :size="15" />待确认集合 {{ pendingCollectionCount }} 篇
        </button>
        <button
          class="secondary-button"
          type="button"
          @click="
            router.push({
              name: 'workspace-results',
              params: { workspaceId },
              query: { run: runId },
            })
          "
        >
          <ArrowLeft :size="15" />返回候选审核
        </button>
      </div>
    </div>

    <section
      v-if="!verificationQuery.isPending.value && !verificationQuery.isError.value"
      class="verification-summary-band"
      aria-label="核验任务汇总"
    >
      <div class="verification-summary-lead">
        <span>本次准备清单</span>
        <strong>{{ selection.selected_count }}</strong
        ><small>篇</small>
        <p>只保存当前检索会话中的临时选择，不等同于已入集合。</p>
      </div>
      <dl class="verification-summary-facts">
        <div>
          <dt>可加入</dt>
          <dd>{{ selection.ready_for_admission_count }} 篇</dd>
        </div>
        <div>
          <dt>核验中</dt>
          <dd>{{ selection.fulltext_in_progress_count }} 篇</dd>
        </div>
        <div>
          <dt>暂时受阻</dt>
          <dd>{{ selection.blocked_count }} 篇</dd>
        </div>
        <div>
          <dt>待确认集合</dt>
          <dd>{{ pendingCollectionCount }} 篇</dd>
        </div>
      </dl>
    </section>

    <ol
      v-if="
        !verificationQuery.isPending.value &&
        !verificationQuery.isError.value &&
        selection.selected_count
      "
      class="verification-handoff"
      aria-label="文献核验与集合交接"
    >
      <li class="verification-handoff-step is-complete">
        <span class="verification-handoff-icon"><Check :size="15" /></span>
        <div>
          <strong>准备清单</strong>
          <small>{{ selection.selected_count }} 篇候选正在本次任务中</small>
        </div>
      </li>
      <li
        class="verification-handoff-step"
        :class="{
          'is-active': hasActiveVerification,
          'is-ready': !hasActiveVerification && selection.ready_for_admission_count > 0,
        }"
      >
        <span class="verification-handoff-icon"><FileCheck2 :size="15" /></span>
        <div>
          <strong>题录与全文核验</strong>
          <small v-if="hasActiveVerification"
            >正在处理 {{ selection.fulltext_in_progress_count }} 篇</small
          >
          <small v-else-if="selection.ready_for_admission_count"
            >{{ selection.ready_for_admission_count }} 篇已通过</small
          >
          <small v-else>等待开始</small>
        </div>
      </li>
      <li
        class="verification-handoff-step"
        :class="{
          'is-active': selection.ready_for_admission_count > 0,
          'is-ready': pendingCollectionCount > 0,
        }"
      >
        <span class="verification-handoff-icon"><Layers2 :size="15" /></span>
        <div>
          <strong>待确认集合</strong>
          <small v-if="selection.ready_for_admission_count"
            >可先加入 {{ selection.ready_for_admission_count }} 篇</small
          >
          <small v-else-if="pendingCollectionCount"
            >已有 {{ pendingCollectionCount }} 篇等待构建</small
          >
          <small v-else>核验通过后在此交接</small>
        </div>
      </li>
    </ol>

    <section
      v-if="
        !verificationQuery.isPending.value &&
        !verificationQuery.isError.value &&
        lastAdmissionCount !== null
      "
      class="verification-admission-feedback"
      role="status"
      aria-live="polite"
    >
      <Check :size="16" />
      <p>
        本次已加入 {{ lastAdmissionCount }} 篇文献。其余仍在核验或暂不可用的候选会保留在此清单中。
      </p>
    </section>

    <section
      v-if="verificationQuery.isPending.value"
      class="verification-loading-shell"
      aria-busy="true"
    >
      <div class="verification-loading-copy">
        <LoaderCircle class="spin" :size="18" />
        <div>
          <h2>正在载入准备清单</h2>
          <p>会同步每篇文献的全文任务状态，随后显示可以执行的下一步操作。</p>
        </div>
      </div>
      <div class="verification-loading-skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
    </section>
    <section v-else-if="verificationQuery.isError.value" class="failure-panel">
      <CircleAlert :size="18" />
      <div>
        <strong>核验任务不可用</strong>
        <p>准备清单可能已经过期，或当前检索运行不再可访问。</p>
      </div>
      <button
        class="secondary-button"
        type="button"
        :disabled="verificationQuery.isFetching.value"
        @click="verificationQuery.refetch()"
      >
        <RefreshCw :class="{ spin: verificationQuery.isFetching.value }" :size="15" />重新读取
      </button>
      <button
        class="secondary-button"
        type="button"
        @click="
          router.push({ name: 'workspace-results', params: { workspaceId }, query: { run: runId } })
        "
      >
        <ArrowLeft :size="15" />返回候选审核
      </button>
    </section>
    <template v-else>
      <section v-if="selection.selected_count" class="verification-command-bar">
        <div>
          <strong>{{ commandHeading }}</strong>
          <p>{{ commandDescription }}</p>
          <p v-if="hasActiveVerification" class="verification-live-note" role="status">
            <LoaderCircle class="spin" :size="13" />本页正在自动刷新任务状态。
          </p>
        </div>
        <div class="verification-command-actions">
          <button
            v-if="canStartVerification"
            class="secondary-button"
            type="button"
            :disabled="prepareMutation.isPending.value"
            @click="prepareMutation.mutate()"
          >
            <LoaderCircle v-if="prepareMutation.isPending.value" class="spin" :size="15" />
            <FileDown v-else :size="15" />开始核验
          </button>
          <button
            class="primary-button verification-admit-button"
            type="button"
            :disabled="!selection.ready_for_admission_count"
            @click="admissionConfirmOpen = true"
          >
            <Layers2 :size="15" />加入待确认集合（{{ selection.ready_for_admission_count }}）
          </button>
        </div>
      </section>

      <section
        v-if="selection.selected_count"
        class="verification-list-panel"
        aria-labelledby="verification-list-heading"
      >
        <div class="verification-list-heading">
          <div>
            <span class="section-kicker">核验明细</span>
            <h2 id="verification-list-heading">每篇候选当前所处的真实状态</h2>
          </div>
          <span>第 {{ currentPageNumber }} 页，共 {{ page.total }} 篇</span>
        </div>

        <div class="verification-item-list">
          <article
            v-for="item in reviewItems"
            :key="item.candidate.candidate_id"
            class="verification-item"
            :class="`verification-${fulltextPresentation(item).tone}`"
          >
            <div class="verification-item-main">
              <div class="verification-item-title-row">
                <h3>{{ item.candidate.title }}</h3>
                <span class="verification-state-label">
                  <LoaderCircle
                    v-if="fulltextPresentation(item).tone === 'processing'"
                    class="spin"
                    :size="14"
                  />
                  <FileCheck2 v-else-if="fulltextPresentation(item).tone === 'ready'" :size="14" />
                  <CircleAlert
                    v-else-if="fulltextPresentation(item).tone === 'blocked'"
                    :size="14"
                  />
                  <FileDown v-else :size="14" />
                  {{ fulltextPresentation(item).label }}
                </span>
              </div>
              <p class="verification-item-meta">
                <span>{{
                  item.candidate.authors.map((author) => author.name).join("、") || "作者信息待补全"
                }}</span>
                <span>{{ item.candidate.published_year ?? "年份待补全" }}</span>
                <span>{{ item.candidate.venue || "来源待补全" }}</span>
              </p>
              <p class="verification-item-detail">{{ fulltextPresentation(item).detail }}</p>
            </div>
            <div class="verification-item-actions">
              <button
                v-if="requiresAuthorizedUpload(item)"
                class="compact-button"
                type="button"
                @click="openCandidateUpload(item.candidate.candidate_id)"
              >
                <Upload :size="14" />上传 PDF
              </button>
              <button
                v-if="canRetry(item)"
                class="compact-button"
                type="button"
                :disabled="retryMutation.isPending.value"
                @click="retryMutation.mutate(item.candidate.candidate_id)"
              >
                <RefreshCw :class="{ spin: retryMutation.isPending.value }" :size="14" />重新核验
              </button>
              <button
                v-if="canRemove(item)"
                class="compact-button quiet-danger"
                type="button"
                :disabled="removeMutation.isPending.value"
                @click="removeMutation.mutate(item.candidate.candidate_id)"
              >
                <X :size="14" />移出清单
              </button>
            </div>
          </article>
        </div>

        <nav v-if="page.total > PAGE_SIZE" class="candidate-pagination" aria-label="核验文献分页">
          <span>每页 {{ PAGE_SIZE }} 篇</span>
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
      </section>

      <section v-else class="verification-empty-state">
        <FileCheck2 :size="22" />
        <div>
          <h2>当前没有待核验文献。</h2>
          <p>你可以返回候选审核继续建立准备清单，或前往待确认集合处理已加入的文献。</p>
        </div>
        <button
          class="primary-button"
          type="button"
          @click="
            router.push({
              name: 'workspace-results',
              params: { workspaceId },
              query: { run: runId },
            })
          "
        >
          返回候选审核<ArrowRight :size="16" />
        </button>
      </section>
    </template>

    <Teleport to="body">
      <section
        v-if="admissionConfirmOpen"
        class="collection-confirm-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="verification-admission-title"
        data-testid="verification-admission-dialog"
      >
        <div class="collection-confirm-surface verification-admission-dialog">
          <button
            class="icon-button close-dialog"
            type="button"
            aria-label="关闭加入待确认集合确认窗口"
            title="关闭"
            @click="admissionConfirmOpen = false"
          >
            <X :size="16" />
          </button>
          <span class="eyebrow">待确认集合</span>
          <h2 id="verification-admission-title">
            将 {{ selection.ready_for_admission_count }} 篇已核验文献加入待确认集合？
          </h2>
          <p>
            只有题录与全文均已通过核验的文献会被加入。仍在处理或未通过的候选会保留在本次准备清单中继续等待。
          </p>
          <dl class="collection-confirm-summary">
            <div>
              <dt>本次加入</dt>
              <dd>{{ selection.ready_for_admission_count }} 篇</dd>
            </div>
            <div>
              <dt>核验中</dt>
              <dd>{{ selection.fulltext_in_progress_count }} 篇</dd>
            </div>
          </dl>
          <div class="collection-confirm-actions">
            <button class="secondary-button" type="button" @click="admissionConfirmOpen = false">
              暂不加入
            </button>
            <button
              class="primary-button"
              type="button"
              :disabled="admissionMutation.isPending.value"
              @click="admissionMutation.mutate()"
            >
              {{ admissionMutation.isPending.value ? "正在加入…" : "确认加入" }}
              <Layers2 :size="16" />
            </button>
          </div>
        </div>
      </section>
    </Teleport>
    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </section>
</template>
