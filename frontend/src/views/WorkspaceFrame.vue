<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { ArrowLeft, FileSearch, PanelLeftClose, PanelLeftOpen, Plus } from "@lucide/vue";

import { useWorkspaceListQuery, useWorkspaceQuery } from "@/api/hooks/research";
import AppHeader from "@/components/AppHeader.vue";
import StageRail from "@/components/StageRail.vue";
import { workspaceRouteForStage } from "@/router/workspace-route";

const route = useRoute();
const workspaceId = computed(() => String(route.params.workspaceId));
const workspaceQuery = useWorkspaceQuery(workspaceId);
// 左侧栏展示真实工作区列表。即使用户有很多工作区，也可以继续按游标加载，
// 不把“切换工作区”的能力藏进单一的顶部菜单里。
const workspaceListQuery = useWorkspaceListQuery();
const workspaces = computed(
  () => workspaceListQuery.data.value?.pages.flatMap((page) => page.items) ?? [],
);
const railCollapsed = ref(false);

function loadMoreWorkspaces(): void {
  void workspaceListQuery.fetchNextPage();
}
</script>

<template>
  <div class="app-shell" :class="{ 'rail-collapsed': railCollapsed }">
    <aside class="side-rail">
      <RouterLink class="side-brand" to="/"
        ><span class="brand-mark">AS</span><span>研究台</span></RouterLink
      >
      <div class="side-rail-heading">
        <span>工作区</span><small>{{ workspaces.length || "-" }}</small>
      </div>
      <nav class="side-workspace-list" aria-label="工作区切换">
        <RouterLink
          v-for="workspace in workspaces"
          :key="workspace.id"
          class="side-workspace-item"
          :class="{ current: workspace.id === workspaceId }"
          :to="{
            name: workspaceRouteForStage(workspace.workflow_stage),
            params: { workspaceId: workspace.id },
          }"
          :title="workspace.name"
        >
          <span class="side-workspace-icon"><FileSearch :size="15" /></span>
          <span class="side-workspace-copy">
            <strong>{{ workspace.name }}</strong>
            <small>{{ workspace.workflow_stage_display.label }}</small>
          </span>
          <span v-if="workspace.id === workspaceId" class="side-workspace-current">当前</span>
        </RouterLink>
        <p v-if="workspaceListQuery.isPending.value" class="side-workspace-empty">
          正在读取工作区…
        </p>
        <p v-else-if="!workspaces.length" class="side-workspace-empty">还没有已保存的工作区</p>
      </nav>
      <button
        v-if="workspaceListQuery.hasNextPage.value"
        class="side-load-more"
        type="button"
        :disabled="workspaceListQuery.isFetchingNextPage.value"
        @click="loadMoreWorkspaces"
      >
        {{ workspaceListQuery.isFetchingNextPage.value ? "正在加载…" : "加载更多" }}
      </button>
      <RouterLink class="side-new-research" to="/" title="开始一项新研究">
        <Plus :size="16" /><span>新建研究</span>
      </RouterLink>
      <div class="side-context">
        <span class="eyebrow">当前任务</span
        ><strong>{{ workspaceQuery.data.value?.name ?? "读取中…" }}</strong
        ><small>{{ workspaceQuery.data.value?.workflow_stage_display.label }}</small>
      </div>
      <div class="side-footer">
        <RouterLink class="icon-button" to="/" title="返回研究入口"
          ><ArrowLeft :size="17" /></RouterLink
        ><button
          class="icon-button"
          type="button"
          :aria-label="railCollapsed ? '展开侧栏' : '折叠侧栏'"
          :title="railCollapsed ? '展开侧栏' : '折叠侧栏'"
          @click="railCollapsed = !railCollapsed"
        >
          <PanelLeftOpen v-if="railCollapsed" :size="17" /><PanelLeftClose v-else :size="17" />
        </button>
      </div>
    </aside>
    <main class="content-area">
      <AppHeader
        :current-workspace-id="workspaceId"
        :workspace-name="workspaceQuery.data.value?.name"
      />
      <div class="workspace-content">
        <StageRail :current="workspaceQuery.data.value?.workflow_stage ?? 'draft'" />
        <div v-if="workspaceQuery.isError.value" class="error-banner">
          无法读取工作区，请刷新后重试。
        </div>
        <RouterView v-else />
      </div>
    </main>
  </div>
</template>
