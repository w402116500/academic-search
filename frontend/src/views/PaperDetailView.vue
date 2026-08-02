<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { ArrowLeft, Clipboard, FileDown, LoaderCircle, Plus, ShieldCheck } from "@lucide/vue";
import { RouterLink, useRoute } from "vue-router";

import {
  admitFulltext,
  getCandidateCitation,
  getFulltext,
  requestFulltext,
} from "@/api/collections";
import {
  canRequestFulltext,
  citationReadinessMessage,
  fulltextStatusLabel,
  isFulltextTerminal,
} from "@/features/research/search-run-state";
import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/research/candidate-language";
import { getSearchCandidates } from "@/api/workflow";
import type { CitationFormat, FulltextResponse } from "@/api/types";

const route = useRoute();
const workspaceId = computed(() => String(route.params.workspaceId));
const runId = computed(() => String(route.query.run ?? ""));
const candidateId = computed(() => String(route.params.candidateId));
const candidatesQuery = useQuery({
  queryKey: computed(() => ["candidates", workspaceId.value, runId.value]),
  queryFn: () => getSearchCandidates(workspaceId.value, runId.value),
  enabled: computed(() => Boolean(runId.value)),
});
const candidate = computed(() =>
  candidatesQuery.data.value?.candidates.find((item) => item.candidate_id === candidateId.value),
);
const fulltext = ref<FulltextResponse | null>(null);
const toast = ref<string | null>(null);
const citationFormat = ref<CitationFormat>("gb_t_7714_2015_numeric");
const citationReady = computed(() => candidate.value?.citation?.status === "ready");
const canStartFulltext = computed(
  () => Boolean(candidate.value) && canRequestFulltext(candidate.value!, fulltext.value),
);
const fulltextIsProcessing = computed(
  () => Boolean(fulltext.value) && !isFulltextTerminal(fulltext.value?.status),
);
let timer: number | undefined;
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
  mutationFn: () => requestFulltext(workspaceId.value, runId.value, candidateId.value),
  onSuccess: (result) => {
    fulltext.value = result;
    if (!isFulltextTerminal(result.status)) poll();
  },
  onError: (error) => (toast.value = error instanceof Error ? error.message : "全文任务无法启动。"),
});
const admissionMutation = useMutation({
  mutationFn: () => admitFulltext(workspaceId.value, runId.value, candidateId.value),
  onSuccess: () => (toast.value = "已加入待确认研究集合。"),
  onError: (error) =>
    (toast.value = error instanceof Error ? error.message : "当前文献还不能加入集合。"),
});

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
    <div v-if="candidatesQuery.isPending.value" class="loading-state">
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
          <FileDown :size="15" />获取开放全文</button
        ><button v-else-if="fulltextIsProcessing" class="primary-button" type="button" disabled>
          <FileDown :size="15" />{{ fulltextStatusLabel(fulltext) }}</button
        ><button
          v-else-if="fulltext?.status === 'available'"
          class="primary-button"
          type="button"
          :disabled="admissionMutation.isPending.value"
          @click="admissionMutation.mutate()"
        >
          <Plus :size="15" />加入研究集合
        </button>
      </div>
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
                fulltext?.status === "available"
                  ? "全文已核验"
                  : fulltext?.status === "rejected"
                    ? "未通过全文准入"
                    : !candidate.doi
                      ? "缺少 DOI"
                      : !citationReady
                        ? "题录未核验"
                        : "尚未进入研究集合"
              }}</strong>
              <p>
                {{
                  fulltext?.status === "available"
                    ? "可以加入待确认集合。"
                    : fulltext?.status === "rejected"
                      ? fulltext.error?.message || "该文献不满足全文准入条件。"
                      : !candidate.doi
                        ? "缺少 DOI，不能进入后续研究集合。"
                        : !citationReady
                          ? citationReadinessMessage(candidate.citation)
                          : "有 DOI 不等于系统已经拥有可研究正文。"
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
