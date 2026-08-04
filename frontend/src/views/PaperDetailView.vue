<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { ArrowLeft, Clipboard, FileDown, LoaderCircle, ShieldCheck, Upload } from "@lucide/vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import {
  getCandidateCitation,
  getFulltext,
  requestFulltext,
  uploadAuthorizedFulltext,
} from "@/api/collections";
import { updateCandidateSelection } from "@/api/workflow";
import {
  canRequestFulltext,
  citationReadinessMessage,
  fulltextStatusLabel,
  isFulltextTerminal,
  presentFulltextVerification,
} from "@/features/research/search-run-state";
import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/research/candidate-language";
import { getSearchCandidate } from "@/api/workflow";
import type { CitationFormat, FulltextResponse } from "@/api/types";

const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = computed(() => String(route.query.run ?? ""));
const candidateId = computed(() => String(route.params.candidateId));
const candidateQuery = useQuery({
  queryKey: computed(() => [
    "candidate-review-item",
    workspaceId.value,
    runId.value,
    candidateId.value,
  ]),
  queryFn: () => getSearchCandidate(workspaceId.value, runId.value, candidateId.value),
  enabled: computed(() => Boolean(runId.value) && Boolean(candidateId.value)),
});
const candidate = computed(() => candidateQuery.data.value?.candidate);
const fulltext = ref<FulltextResponse | null>(null);
const toast = ref<string | null>(null);
const uploadInput = ref<HTMLInputElement | null>(null);
const uploadFile = ref<File | null>(null);
const uploadAuthorized = ref(false);
const citationFormat = ref<CitationFormat>("gb_t_7714_2015_numeric");
const citationReady = computed(() => candidate.value?.citation?.status === "ready");
const canStartFulltext = computed(
  () => Boolean(candidate.value) && canRequestFulltext(candidate.value!, fulltext.value),
);
const fulltextIsProcessing = computed(
  () => Boolean(fulltext.value) && !isFulltextTerminal(fulltext.value?.status),
);
const canUploadAuthorizedPdf = computed(() => {
  const state = fulltext.value;
  return Boolean(
    state &&
    (state.status === "requires_upload" ||
      state.status === "rejected" ||
      (state.status === "failed" && state.error?.retryable === false)),
  );
});
const fulltextPresentation = computed(() => presentFulltextVerification(fulltext.value));
let timer: number | undefined;

/** 刷新详情页后恢复已有全文任务状态，而不是只等待本页新发起的操作。 */
watch(
  () => candidateQuery.data.value?.fulltext,
  (state) => {
    fulltext.value = state ?? null;
  },
  { immediate: true },
);

/** 已在后台运行的全文核验需要在详情页恢复轮询，终态则立即停止。 */
watch(
  fulltext,
  (state) => {
    if (state && !isFulltextTerminal(state.status)) poll();
    else if (timer) window.clearInterval(timer);
  },
  { immediate: true },
);
const citationQuery = useQuery({
  queryKey: computed(() => [
    "candidate-citation",
    workspaceId.value,
    runId.value,
    candidateId.value,
    citationFormat.value,
  ]),
  queryFn: () =>
    getCandidateCitation(workspaceId.value, runId.value, candidateId.value, citationFormat.value),
  enabled: computed(() => Boolean(runId.value) && citationReady.value),
});
const fulltextMutation = useMutation({
  mutationFn: async () => {
    // 详情页发起的单篇核验也必须进入本次准备清单，避免核验完成后脱离批量交接页面。
    await updateCandidateSelection(workspaceId.value, runId.value, [candidateId.value], true);
    return requestFulltext(workspaceId.value, runId.value, candidateId.value);
  },
  onSuccess: (result) => {
    fulltext.value = result;
  },
  onError: (error) => (toast.value = error instanceof Error ? error.message : "全文任务无法启动。"),
});
const uploadMutation = useMutation({
  mutationFn: () => {
    if (!uploadFile.value) throw new Error("请先选择要上传的 PDF。");
    return uploadAuthorizedFulltext(
      workspaceId.value,
      runId.value,
      candidateId.value,
      uploadFile.value,
    );
  },
  onSuccess: (result) => {
    fulltext.value = result;
    uploadFile.value = null;
    uploadAuthorized.value = false;
    if (uploadInput.value) uploadInput.value.value = "";
  },
  onError: (error) => {
    toast.value = error instanceof Error ? error.message : "PDF 上传或校验无法完成。";
  },
});

function openVerificationTask(): void {
  void router.push({
    name: "workspace-verification",
    params: { workspaceId: workspaceId.value },
    query: { run: runId.value },
  });
}

function chooseUpload(): void {
  uploadInput.value?.click();
}

function selectUpload(event: Event): void {
  const target = event.target as HTMLInputElement;
  uploadFile.value = target.files?.[0] ?? null;
}

function poll(): void {
  if (timer) window.clearInterval(timer);
  timer = window.setInterval(async () => {
    try {
      fulltext.value = await getFulltext(workspaceId.value, runId.value, candidateId.value);
      if (isFulltextTerminal(fulltext.value.status) && timer) window.clearInterval(timer);
    } catch {
      if (timer) window.clearInterval(timer);
    }
  }, 1_500);
}

async function copyCitation(): Promise<void> {
  try {
    const result = await citationQuery.refetch();
    if (!result.data) throw result.error ?? new Error("当前题录无法生成正式引用。");
    await navigator.clipboard.writeText(result.data.text);
    toast.value = "已复制正式引用。";
  } catch (error) {
    toast.value = error instanceof Error ? error.message : "浏览器未授予剪贴板权限。";
  }
}

onUnmounted(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <section class="stage-view detail-view">
    <RouterLink
      class="back-link"
      :to="{ name: 'workspace-results', params: { workspaceId }, query: { run: runId } }"
      ><ArrowLeft :size="15" />返回候选文献</RouterLink
    >
    <div v-if="candidateQuery.isPending.value" class="loading-state">
      <LoaderCircle class="spin" :size="18" />正在读取论文详情…
    </div>
    <div v-else-if="!candidate" class="failure-panel">
      <strong>找不到这篇候选文献</strong>
      <p>候选会话可能已过期，返回结果页重新检索即可恢复。</p>
    </div>
    <article v-else class="paper-detail">
      <div class="eyebrow">PAPER DETAIL / REVIEW</div>
      <h1>{{ candidate.title }}</h1>
      <p class="paper-authors">
        {{ candidate.authors.map((author) => author.name).join("、") || "作者信息待补全" }}
      </p>
      <div class="paper-meta">
        <span>{{ candidate.venue || "来源待补全" }}</span
        ><span>{{ candidate.published_year ?? "年份待补全" }}</span
        ><span
          class="candidate-language-tag"
          :class="`language-${normalizeCandidateLanguage(candidate.language)}`"
          >{{ candidateLanguageLabel(candidate.language) }}</span
        >
        ><span v-if="candidate.doi">DOI {{ candidate.doi }}</span>
      </div>
      <div class="paper-actions">
        <label v-if="citationReady" class="citation-format-field"
          ><span>引用格式</span
          ><select v-model="citationFormat">
            <option value="gb_t_7714_2015_numeric">GB/T 7714-2015</option>
            <option value="apa_7">APA 7</option>
            <option value="mla_9">MLA 9</option>
            <option value="chicago_author_date">Chicago Author-Date</option>
            <option value="bibtex">BibTeX</option>
          </select></label
        ><button
          v-if="citationReady"
          class="secondary-button"
          type="button"
          :disabled="citationQuery.isFetching.value"
          @click="copyCitation"
        >
          <Clipboard :size="15" />复制引用</button
        ><a
          v-if="candidate.links.landing_url"
          class="secondary-button"
          :href="candidate.links.landing_url"
          target="_blank"
          rel="noreferrer"
          >打开来源</a
        ><button
          v-if="canStartFulltext"
          class="primary-button"
          type="button"
          :disabled="fulltextMutation.isPending.value"
          @click="fulltextMutation.mutate()"
        >
          <FileDown :size="15" />准备全文核验</button
        ><button v-else-if="fulltextIsProcessing" class="primary-button" type="button" disabled>
          <FileDown :size="15" />{{ fulltextStatusLabel(fulltext) }}</button
        ><button
          v-else-if="fulltext?.status === 'available'"
          class="primary-button"
          type="button"
          @click="openVerificationTask"
        >
          <ShieldCheck :size="15" />前往核验任务加入集合
        </button>
        <button
          v-else-if="canUploadAuthorizedPdf"
          class="primary-button"
          type="button"
          @click="chooseUpload"
        >
          <Upload :size="15" />选择有权处理的 PDF
        </button>
      </div>
      <section
        v-if="canUploadAuthorizedPdf"
        class="upload-authorization-panel"
        aria-label="上传有权处理的 PDF"
      >
        <input
          ref="uploadInput"
          class="visually-hidden"
          type="file"
          accept="application/pdf,.pdf"
          @change="selectUpload"
        />
        <div>
          <strong>上传有权处理的 PDF</strong>
          <p>文件会先校验类型、PDF 签名、大小和哈希，再进入本次核验任务。</p>
        </div>
        <p v-if="uploadFile" class="upload-selected-file">{{ uploadFile.name }}</p>
        <label class="upload-authorization-check">
          <input v-model="uploadAuthorized" type="checkbox" />
          <span>我确认有权处理并上传这篇文献的 PDF。</span>
        </label>
        <div class="upload-authorization-actions">
          <button class="secondary-button" type="button" @click="chooseUpload">
            <Upload :size="15" />{{ uploadFile ? "更换 PDF" : "选择 PDF" }}
          </button>
          <button
            class="primary-button"
            type="button"
            :disabled="!uploadFile || !uploadAuthorized || uploadMutation.isPending.value"
            @click="uploadMutation.mutate()"
          >
            <LoaderCircle v-if="uploadMutation.isPending.value" class="spin" :size="15" />
            <Upload v-else :size="15" />上传并核验
          </button>
        </div>
      </section>
      <div class="paper-grid">
        <div class="paper-main">
          <section>
            <div class="detail-section-heading">
              <span>摘要</span><span class="eyebrow">ABSTRACT</span>
            </div>
            <p class="abstract">
              {{
                candidate.abstract ||
                "当前来源没有返回摘要。可以先查看正式来源，或在后续集合中通过可处理全文完成研究。"
              }}
            </p>
          </section>
          <section class="citation-preview">
            <div class="detail-section-heading">
              <span>正式引用</span><span class="eyebrow">CITATION</span>
            </div>
            <template v-if="citationReady">
              <pre v-if="citationQuery.data.value">{{ citationQuery.data.value.text }}</pre>
              <p v-else-if="citationQuery.isFetching.value">正在按所选格式渲染题录…</p>
              <p v-else-if="citationQuery.isError.value">正式引用预览暂时无法读取，请稍后重试。</p>
              <p v-else>正在读取题录预览。</p>
            </template>
            <p v-else>{{ citationReadinessMessage(candidate.citation) }}</p>
          </section>
        </div>
        <aside class="detail-aside">
          <div class="evidence-status">
            <ShieldCheck :size="17" />
            <div>
              <strong>{{
                fulltext
                  ? fulltextPresentation.label
                  : !candidate.doi
                    ? "缺少 DOI"
                    : "待准备全文核验"
              }}</strong>
              <p>
                {{
                  fulltext
                    ? fulltextPresentation.detail
                    : !candidate.doi
                      ? "缺少 DOI，不能进入后续研究集合。"
                      : `${citationReadinessMessage(candidate.citation)} 可以开始全文核验，系统会先按 DOI 尝试补齐题录。`
                }}
              </p>
            </div>
          </div>
          <dl>
            <div>
              <dt>文献类型</dt>
              <dd>{{ candidate.document_type || "未标注" }}</dd>
            </div>
            <div>
              <dt>文献语言</dt>
              <dd>{{ candidateLanguageLabel(candidate.language) }}</dd>
            </div>
            <div>
              <dt>引用信号</dt>
              <dd>
                {{
                  Object.values(candidate.citation_counts_by_source).reduce(
                    (sum, count) => sum + count,
                    0,
                  ) || "暂无"
                }}
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </article>
    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </section>
</template>
