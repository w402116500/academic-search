<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  FileSearch,
  LoaderCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  Trash2,
} from "@lucide/vue";

import {
  useWorkspaceDeletionMutation,
  useWorkspaceListQuery,
  useWorkspaceQuery,
} from "@/api/hooks/research";
import type { Workspace } from "@/api/types";
import AppHeader from "@/components/AppHeader.vue";
import {
  isWorkspaceDeletionPending,
  workspaceDeletionIncompleteMessage,
} from "@/features/research/workspace-deletion-presentation";
import StageRail from "@/components/StageRail.vue";
import { workspaceRouteForStage } from "@/router/workspace-route";

const route = useRoute();
const router = useRouter();
const workspaceId = computed(() => String(route.params.workspaceId));
const workspaceQuery = useWorkspaceQuery(workspaceId);
// 左侧栏展示真实工作区列表。即使用户有很多工作区，也可以继续按游标加载，
// 不把“切换工作区”的能力藏进单一的顶部菜单里。
const workspaceListQuery = useWorkspaceListQuery();
const workspaces = computed(
  () => workspaceListQuery.data.value?.pages.flatMap((page) => page.items) ?? [],
);
const railCollapsed = ref(false);
const workspacePendingDeletion = ref<Workspace | null>(null);
const deletionError = ref<string | null>(null);
const recoveryDeletionError = ref<string | null>(null);
const resumingWorkspaceId = ref<string | null>(null);
const deleteWorkspaceMutation = useWorkspaceDeletionMutation();

function loadMoreWorkspaces(): void {
  void workspaceListQuery.fetchNextPage();
}

function openWorkspaceDeletion(workspace: Workspace): void {
  workspacePendingDeletion.value = workspace;
  deletionError.value = null;
}

function closeWorkspaceDeletion(): void {
  if (deleteWorkspaceMutation.isPending.value) return;
  workspacePendingDeletion.value = null;
  deletionError.value = null;
}

function confirmWorkspaceDeletion(): void {
  const workspace = workspacePendingDeletion.value;
  if (!workspace) return;

  deletionError.value = null;
  deleteWorkspaceMutation.mutate(workspace.id, {
    onSuccess: async () => {
      const isCurrentWorkspace = workspace.id === workspaceId.value;
      workspacePendingDeletion.value = null;
      if (isCurrentWorkspace) await router.replace({ name: "research-entry" });
    },
    onError: () => {
      deletionError.value = workspaceDeletionIncompleteMessage;
    },
  });
}

function resumeWorkspaceDeletion(workspace: Workspace): void {
  recoveryDeletionError.value = null;
  resumingWorkspaceId.value = workspace.id;
  deleteWorkspaceMutation.mutate(workspace.id, {
    onSuccess: async () => {
      const isCurrentWorkspace = workspace.id === workspaceId.value;
      resumingWorkspaceId.value = null;
      if (isCurrentWorkspace) await router.replace({ name: "research-entry" });
    },
    onError: () => {
      resumingWorkspaceId.value = null;
      recoveryDeletionError.value = workspaceDeletionIncompleteMessage;
    },
  });
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
        <div v-for="workspace in workspaces" :key="workspace.id" class="side-workspace-entry">
          <div
            v-if="isWorkspaceDeletionPending(workspace)"
            class="side-workspace-item"
            :class="{ 'is-deleting': true }"
            :title="`${workspace.name}：删除未完成`"
          >
            <span class="side-workspace-icon"><FileSearch :size="15" /></span>
            <span class="side-workspace-copy">
              <strong>{{ workspace.name }}</strong>
              <small>删除未完成</small>
            </span>
            <button
              class="side-workspace-resume"
              type="button"
              :aria-label="`继续删除工作区 ${workspace.name}`"
              title="继续删除"
              :disabled="resumingWorkspaceId === workspace.id"
              @click="resumeWorkspaceDeletion(workspace)"
            >
              <LoaderCircle v-if="resumingWorkspaceId === workspace.id" class="spin" :size="15" />
              <RotateCcw v-else :size="15" />
            </button>
          </div>
          <RouterLink
            v-else
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
          <button
            v-if="!isWorkspaceDeletionPending(workspace)"
            class="side-workspace-delete"
            type="button"
            :aria-label="`删除工作区 ${workspace.name}`"
            title="删除工作区"
            @click="openWorkspaceDeletion(workspace)"
          >
            <Trash2 :size="15" />
          </button>
        </div>
        <p v-if="workspaceListQuery.isPending.value" class="side-workspace-empty">
          正在读取工作区…
        </p>
        <p v-else-if="!workspaces.length" class="side-workspace-empty">还没有已保存的工作区</p>
        <p v-if="recoveryDeletionError" class="side-workspace-recovery-error" role="alert">
          {{ recoveryDeletionError }}
        </p>
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
    <div
      v-if="workspacePendingDeletion"
      class="workspace-delete-backdrop"
      @click.self="closeWorkspaceDeletion"
    >
      <section
        class="workspace-delete-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="workspace-delete-title"
      >
        <div class="workspace-delete-dialog-heading">
          <span class="workspace-delete-dialog-icon"><Trash2 :size="18" /></span>
          <div>
            <p class="eyebrow">永久删除</p>
            <h2 id="workspace-delete-title">删除“{{ workspacePendingDeletion.name }}”</h2>
          </div>
        </div>
        <p class="workspace-delete-dialog-copy">
          此操作会删除该工作区的私有文献、全文、向量与研究记录，删除后无法恢复。
        </p>
        <p v-if="deletionError" class="workspace-delete-error" role="alert">
          {{ deletionError }}
        </p>
        <div class="workspace-delete-actions">
          <button
            class="ghost-button"
            type="button"
            :disabled="deleteWorkspaceMutation.isPending.value"
            @click="closeWorkspaceDeletion"
          >
            取消
          </button>
          <button
            class="workspace-delete-confirm"
            type="button"
            :disabled="deleteWorkspaceMutation.isPending.value"
            @click="confirmWorkspaceDeletion"
          >
            {{ deleteWorkspaceMutation.isPending.value ? "正在删除…" : "永久删除" }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
