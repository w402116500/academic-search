<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useWorkspaceQuery } from "@/api/hooks/research";
import { useCurrentSearchRunQuery } from "@/api/hooks/search";
import AppHeader from "@/components/AppHeader.vue";
import {
  routeForRecoveredSearchRun,
  shouldRestoreCurrentSearchRun,
} from "@/features/search/search-run-state";
import PlanReviewView from "@/views/PlanReviewView.vue";
import SearchRunView from "@/views/SearchRunView.vue";

const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.workspaceId));
const workspaceQuery = useWorkspaceQuery(workspaceId);
const shouldRestoreSearchRun = computed(() =>
  shouldRestoreCurrentSearchRun(workspaceQuery.data.value?.workflow_stage),
);
const currentSearchRunQuery = useCurrentSearchRunQuery(workspaceId, shouldRestoreSearchRun);

// URL 中的运行标识优先恢复进度画布；没有标识时根据服务端阶段找回当前运行。
const showSearchRunner = computed(
  () => Boolean(route.query.run) || workspaceQuery.data.value?.workflow_stage === "retrieving",
);
const restoringSearchRun = computed(
  () => shouldRestoreSearchRun.value && !currentSearchRunQuery.data.value,
);

watch(
  () => ({
    workflowStage: workspaceQuery.data.value?.workflow_stage,
    searchRun: currentSearchRunQuery.data.value,
  }),
  ({ workflowStage, searchRun }) => {
    if (route.query.run || !workflowStage || !searchRun) return;
    const targetRoute = routeForRecoveredSearchRun(workflowStage, searchRun.status);
    if (targetRoute === "workspace-runner") return;
    void router.replace({
      name: targetRoute,
      params: { workspaceId: workspaceId.value },
      query: targetRoute === "workspace-results" ? { run: searchRun.id } : undefined,
    });
  },
  { immediate: true },
);
</script>

<template>
  <div class="entry-page runner-page">
    <AppHeader :current-workspace-id="workspaceId" />
    <main class="runner-main">
      <div v-if="workspaceQuery.isError.value" class="runner-error" role="alert">
        无法读取这个工作区，请返回首页或刷新后重试。
      </div>
      <div v-else class="runner-canvas">
        <div v-if="workspaceQuery.data.value" class="runner-workspace-context">
          <span>当前研究</span><strong>{{ workspaceQuery.data.value.name }}</strong>
        </div>
        <Transition name="runner-surface" mode="out-in">
          <SearchRunView v-if="showSearchRunner" key="search" />
          <div v-else-if="restoringSearchRun" key="restore" class="loading-state">
            正在恢复这个工作区的检索进度…
          </div>
          <div
            v-else-if="currentSearchRunQuery.isError.value"
            key="restore-error"
            class="failure-panel"
          >
            <strong>无法恢复检索运行</strong>
            <p>请刷新后重试；系统不会要求你重新确认已经完成的研究计划。</p>
          </div>
          <PlanReviewView v-else key="plan" />
        </Transition>
      </div>
    </main>
  </div>
</template>
