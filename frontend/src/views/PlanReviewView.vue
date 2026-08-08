<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  ArrowRight,
  Check,
  Clock3,
  Database,
  FileSearch,
  Languages,
  Search,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import { useResearchPlanMutations, useResearchPlanQuery } from "@/api/hooks/research";
import { buildResearchScope, type ResearchTimePreset } from "@/features/research/scope";
import {
  providerNamesForDirection,
  researchLanguageLabel,
  researchTimeScopeLabel,
  resolvePlanScope,
  resolveTimePreset,
} from "@/features/research/plan-presentation";

const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.workspaceId));
const currentYear = new Date().getFullYear();
const planQuery = useResearchPlanQuery(workspaceId);
const { confirmMutation, regenerateMutation } = useResearchPlanMutations(workspaceId);
const selectedDirectionId = ref("");
const timePreset = ref<ResearchTimePreset>("any");
const startYear = ref<number | null>(null);
const endYear = ref<number | null>(null);
const languages = ref<Array<"zh" | "en">>(["zh", "en"]);
const localError = ref<string | null>(null);

// 右侧研究摘要只读取当前已选择的方向，避免用户确认前就把其他方向的检索词混入视图。
const selectedDirection = computed(
  () =>
    planQuery.data.value?.direction_options.find(
      (direction) => direction.id === selectedDirectionId.value,
    ) ?? null,
);

const selectedProviderNames = computed(() => {
  return providerNamesForDirection(
    planQuery.data.value?.query_plan?.by_direction,
    selectedDirectionId.value,
  );
});

const selectedScopeSummary = computed(() =>
  researchTimeScopeLabel(timePreset.value, startYear.value, endYear.value),
);

const selectedLanguageSummary = computed(() => researchLanguageLabel(languages.value));

watch(
  () => planQuery.data.value,
  (plan) => {
    if (!plan) return;
    selectedDirectionId.value = plan.selected_direction_id ?? plan.direction_options[0]?.id ?? "";
    const scope = resolvePlanScope(plan.scope);
    languages.value = scope?.languages?.length ? [...scope.languages] : ["zh", "en"];
    startYear.value = scope?.start_year ?? null;
    endYear.value = scope?.end_year ?? null;
    timePreset.value = scope ? resolveTimePreset(scope, currentYear) : "any";
  },
  { immediate: true },
);

function confirmResearchPlan(): void {
  confirmMutation.mutate(
    { selectedDirectionId: selectedDirectionId.value, scope: buildScope() },
    {
      onSuccess: ({ run }) => {
        void router.push({
          name: "workspace-runner",
          params: { workspaceId: workspaceId.value },
          query: { run: run.id },
        });
      },
      onError: (error) => {
        localError.value = error instanceof Error ? error.message : "计划确认失败，请检查后重试。";
      },
    },
  );
}

function regenerateResearchPlan(): void {
  const plan = planQuery.data.value;
  if (!plan) {
    localError.value = "当前研究计划尚未加载，无法重新生成。";
    return;
  }
  regenerateMutation.mutate(plan.raw_request, {
    onSuccess: () => {
      localError.value = null;
    },
    onError: (error) => {
      localError.value = error instanceof Error ? error.message : "计划重新生成失败，请稍后重试。";
    },
  });
}

function buildScope() {
  return buildResearchScope({
    timePreset: timePreset.value,
    startYear: startYear.value,
    endYear: endYear.value,
    languages: languages.value,
    currentYear,
  });
}

function toggleLanguage(language: "zh" | "en"): void {
  languages.value = languages.value.includes(language)
    ? languages.value.filter((item) => item !== language)
    : [...languages.value, language];
}
</script>

<template>
  <section class="stage-view plan-view">
    <div v-if="planQuery.isPending.value" class="loading-state">
      <span class="loading-dot" />正在读取研究计划…
    </div>
    <div v-else-if="planQuery.isError.value" class="error-banner">
      研究计划读取失败，请刷新页面重试。
    </div>
    <template v-else-if="planQuery.data.value">
      <div class="request-echo">
        <Sparkles :size="17" />
        <p>{{ planQuery.data.value.raw_request }}</p>
      </div>
      <div class="view-heading">
        <div>
          <div class="eyebrow">
            任务解析 / {{ planQuery.data.value.status === "generating" ? "RUNNING" : "READY" }}
          </div>
          <h1>
            {{
              planQuery.data.value.status === "generating"
                ? "正在理解这项研究。"
                : "确认这次研究的主线。"
            }}
          </h1>
          <p>
            {{
              planQuery.data.value.status === "generating"
                ? "系统正在识别研究对象、关系和可检索概念。页面会自动刷新。"
                : "确认方向后，系统才会启动多源文献检索。"
            }}
          </p>
        </div>
        <span class="revision-chip">计划 v{{ planQuery.data.value.revision }}</span>
      </div>
      <div v-if="planQuery.data.value.status === 'generating'" class="analysis-list">
        <div class="analysis-row active">
          <span class="pulse-icon"><Sparkles :size="15" /></span>
          <div>
            <strong>识别研究对象与边界</strong><small>把自然语言要求整理成可确认的研究计划</small>
          </div>
          <span class="row-state">进行中</span>
        </div>
        <div class="analysis-row">
          <span class="step-icon"><Clock3 :size="15" /></span>
          <div><strong>生成候选研究方向</strong><small>完成后会在这里提供 2-3 个方向</small></div>
          <span class="row-state">等待</span>
        </div>
        <div class="analysis-row">
          <span class="step-icon"><Languages :size="15" /></span>
          <div><strong>准备时间与语言范围</strong><small>由你确认本次检索的范围</small></div>
          <span class="row-state">等待</span>
        </div>
      </div>
      <div v-else-if="planQuery.data.value.status === 'failed'" class="failure-panel">
        <strong>这次解析没有完成</strong>
        <p>{{ planQuery.data.value.error_message ?? "模型没有返回可用的研究计划。" }}</p>
        <p v-if="localError" class="form-error">{{ localError }}</p>
        <button
          class="secondary-button"
          type="button"
          :disabled="regenerateMutation.isPending.value"
          @click="regenerateResearchPlan"
        >
          <RotateCcw :class="{ spin: regenerateMutation.isPending.value }" :size="15" />
          {{ regenerateMutation.isPending.value ? "正在重新生成计划…" : "重新生成计划" }}
        </button>
      </div>
      <form v-else class="plan-form" @submit.prevent="confirmResearchPlan">
        <div class="plan-decision-layout">
          <div class="plan-decision-main">
            <section class="plan-section" aria-labelledby="direction-heading">
              <div class="section-heading">
                <div>
                  <span class="section-kicker">研究主线</span>
                  <h2 id="direction-heading">选择最接近你问题的一条路径</h2>
                  <p>每个方向都有独立检索表达式。选择后，系统只会执行这一条路径。</p>
                </div>
              </div>
              <div class="direction-list" role="radiogroup" aria-label="研究方向">
                <button
                  v-for="direction in planQuery.data.value.direction_options"
                  :key="direction.id"
                  class="direction-card"
                  :class="{ selected: selectedDirectionId === direction.id }"
                  type="button"
                  role="radio"
                  :aria-checked="selectedDirectionId === direction.id"
                  @click="selectedDirectionId = direction.id"
                >
                  <span class="radio-dot"
                    ><Check v-if="selectedDirectionId === direction.id" :size="12" /></span
                  ><span class="direction-copy"
                    ><span class="direction-title-row"
                      ><strong>{{ direction.title }}</strong
                      ><span
                        v-if="selectedDirectionId === direction.id"
                        class="direction-active-label"
                        >当前主线</span
                      ></span
                    ><small>{{ direction.summary }}</small
                    ><span class="direction-topics">
                      <span v-for="topic in direction.subtopics" :key="topic">{{ topic }}</span>
                    </span></span
                  >
                </button>
              </div>
            </section>

            <section class="plan-section scope-section" aria-labelledby="scope-heading">
              <div class="section-heading">
                <div>
                  <span class="section-kicker">检索边界</span>
                  <h2 id="scope-heading">限定本次文献范围</h2>
                  <p>
                    你只需要确认时间和语言；题录与 PDF 可得性会自动探测，不阻塞后续加入研究集合。
                  </p>
                </div>
              </div>
              <div class="scope-panel">
                <label class="field"
                  ><span>发表时间</span
                  ><select v-model="timePreset">
                    <option value="any">不限时间</option>
                    <option value="last3">近 3 年</option>
                    <option value="last5">近 5 年</option>
                    <option value="custom">自定义年份</option>
                  </select></label
                >
                <div v-if="timePreset === 'custom'" class="year-fields">
                  <label class="field"
                    ><span>起始年份</span
                    ><input
                      v-model.number="startYear"
                      type="number"
                      min="1900"
                      :max="currentYear"
                      placeholder="2019" /></label
                  ><label class="field"
                    ><span>结束年份</span
                    ><input
                      v-model.number="endYear"
                      type="number"
                      min="1900"
                      :max="currentYear"
                      placeholder="2024"
                  /></label>
                </div>
                <div class="language-field">
                  <span>文献语言</span>
                  <div class="language-options">
                    <button
                      type="button"
                      :class="{ selected: languages.includes('zh') }"
                      :aria-pressed="languages.includes('zh')"
                      @click="toggleLanguage('zh')"
                    >
                      <span>中文</span><Check v-if="languages.includes('zh')" :size="14" /></button
                    ><button
                      type="button"
                      :class="{ selected: languages.includes('en') }"
                      :aria-pressed="languages.includes('en')"
                      @click="toggleLanguage('en')"
                    >
                      <span>英文</span><Check v-if="languages.includes('en')" :size="14" />
                    </button>
                  </div>
                </div>
              </div>
            </section>
          </div>

          <aside class="research-brief" aria-label="本次检索摘要">
            <div class="research-brief-head">
              <span>本次检索摘要</span><span class="research-brief-status">待确认</span>
            </div>
            <div class="research-brief-focus">
              <span class="brief-icon"><FileSearch :size="18" /></span>
              <div>
                <small>研究主线</small>
                <strong>{{ selectedDirection?.title ?? "正在选择研究方向" }}</strong>
              </div>
            </div>
            <p class="research-brief-summary">
              {{ selectedDirection?.summary ?? "请先从系统生成的方向中选择一条研究路径。" }}
            </p>
            <div class="brief-topic-list" aria-label="当前方向子议题">
              <span v-for="topic in selectedDirection?.subtopics ?? []" :key="topic">{{
                topic
              }}</span>
            </div>
            <dl class="research-brief-details">
              <div>
                <dt><Clock3 :size="14" />时间范围</dt>
                <dd>{{ selectedScopeSummary }}</dd>
              </div>
              <div>
                <dt><Languages :size="14" />文献语言</dt>
                <dd>{{ selectedLanguageSummary }}</dd>
              </div>
              <div>
                <dt><Database :size="14" />计划来源</dt>
                <dd>
                  {{
                    selectedProviderNames.length ? selectedProviderNames.join("、") : "准备检索来源"
                  }}
                </dd>
              </div>
            </dl>
            <div class="research-brief-rule">
              <ShieldCheck :size="16" />
              <p>
                <strong>集合与研究范围</strong
                ><span>研究集合保留你选择的书目；RAG 只使用已完成全文入库的文献。</span>
              </p>
            </div>
          </aside>
        </div>

        <div class="plan-footer">
          <div class="plan-footer-copy">
            <span><Search :size="15" />下一步</span>
            <strong>启动多源文献检索并进入候选筛选</strong>
          </div>
          <div class="plan-footer-action">
            <p v-if="localError" class="form-error">{{ localError }}</p>
            <button
              class="primary-button"
              type="submit"
              :disabled="confirmMutation.isPending.value"
            >
              <span>{{ confirmMutation.isPending.value ? "正在启动检索…" : "确认并开始检索" }}</span
              ><ArrowRight :size="17" />
            </button>
          </div>
        </div>
      </form>
    </template>
  </section>
</template>
