<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { ChevronDown, LogOut, Search, Workflow } from "@lucide/vue";

import { useWorkspaceSearchQuery } from "@/api/hooks/research";
import { workspaceRouteForStage } from "@/router/workspace-route";
import { useAuthStore } from "@/stores/auth";

const props = withDefaults(
  defineProps<{ currentWorkspaceId?: string; workspaceName?: string; compact?: boolean }>(),
  {
    currentWorkspaceId: undefined,
    workspaceName: undefined,
    compact: false,
  },
);
const auth = useAuthStore();
const router = useRouter();
const workspaceMenuOpen = ref(false);
const accountMenuOpen = ref(false);
const search = ref("");
const workspaceQuery = useWorkspaceSearchQuery(search, workspaceMenuOpen);
const workspaces = computed(
  () => workspaceQuery.data.value?.pages.flatMap((page) => page.items) ?? [],
);

const initials = computed(() => (auth.user?.display_name ?? "研").slice(0, 1).toUpperCase());

function signOut(): void {
  auth.clear();
  void router.push({ name: "login" });
}

function loadMoreWorkspaces(): void {
  void workspaceQuery.fetchNextPage();
}
</script>

<template>
  <header class="topbar" :class="{ 'topbar-contextual': props.workspaceName }">
    <RouterLink v-if="!props.workspaceName" class="brand" to="/" aria-label="Academic Search 首页">
      <span class="brand-mark">AS</span><span>Academic Search</span>
    </RouterLink>
    <div v-else class="topbar-context" :title="props.workspaceName">
      <Workflow :size="16" /><span>{{ props.workspaceName }}</span>
    </div>
    <div class="topbar-actions">
      <div v-if="!props.compact" class="menu-wrap">
        <button class="ghost-button" type="button" @click="workspaceMenuOpen = !workspaceMenuOpen">
          <Workflow :size="16" /><span>工作区</span><ChevronDown :size="14" />
        </button>
        <div v-if="workspaceMenuOpen" class="popover workspace-popover">
          <div class="popover-search">
            <Search :size="14" /><input v-model="search" placeholder="搜索工作区或阶段" />
          </div>
          <div class="popover-list">
            <RouterLink
              v-for="workspace in workspaces"
              :key="workspace.id"
              class="workspace-option"
              :class="{ current: workspace.id === props.currentWorkspaceId }"
              :to="{
                name: workspaceRouteForStage(workspace.workflow_stage),
                params: { workspaceId: workspace.id },
              }"
              @click="workspaceMenuOpen = false"
            >
              <strong>{{ workspace.name }}</strong
              ><small>{{ workspace.workflow_stage_display.label }}</small>
            </RouterLink>
            <div v-if="workspaceQuery.isPending.value" class="popover-empty">正在读取工作区…</div>
            <div v-else-if="!workspaces.length" class="popover-empty">还没有匹配的工作区</div>
            <button
              v-if="workspaceQuery.hasNextPage.value"
              class="load-more-button"
              type="button"
              :disabled="workspaceQuery.isFetchingNextPage.value"
              @click="loadMoreWorkspaces"
            >
              {{ workspaceQuery.isFetchingNextPage.value ? "正在加载…" : "加载更多工作区" }}
            </button>
          </div>
        </div>
      </div>
      <div v-if="auth.isAuthenticated" class="menu-wrap">
        <button class="account-button" type="button" @click="accountMenuOpen = !accountMenuOpen">
          <span class="avatar">{{ initials }}</span
          ><span class="account-name">{{ auth.user?.display_name }}</span
          ><ChevronDown :size="14" />
        </button>
        <div v-if="accountMenuOpen" class="popover account-popover">
          <div class="identity">
            <strong>{{ auth.user?.display_name }}</strong
            ><span>{{ auth.user?.email }}</span>
          </div>
          <button class="popover-action" type="button" @click="signOut">
            <LogOut :size="15" />退出登录
          </button>
        </div>
      </div>
    </div>
  </header>
</template>
