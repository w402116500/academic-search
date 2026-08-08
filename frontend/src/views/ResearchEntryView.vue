<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowRight, CircleAlert, LoaderCircle, RotateCcw } from "@lucide/vue";
import { useRouter } from "vue-router";

import AppHeader from "@/components/AppHeader.vue";
import {
  useStartResearchMutation,
  useWorkspaceDeletionMutation,
  useWorkspaceListQuery,
} from "@/api/hooks/research";
import type { Workspace } from "@/api/types";
import {
  isWorkspaceDeletionPending,
  workspaceDeletionIncompleteMessage,
} from "@/features/research/workspace-deletion-presentation";

const router = useRouter();
const rawRequest = ref("");
const requestError = ref<string | null>(null);
const submitMutation = useStartResearchMutation();
const workspaceListQuery = useWorkspaceListQuery();
const deleteWorkspaceMutation = useWorkspaceDeletionMutation();
const recoveryError = ref<string | null>(null);
const resumingWorkspaceId = ref<string | null>(null);
const pendingDeletionWorkspaces = computed(() =>
  (workspaceListQuery.data.value?.pages.flatMap((page) => page.items) ?? []).filter(
    isWorkspaceDeletionPending,
  ),
);

function submit(): void {
  requestError.value = null;
  if (rawRequest.value.trim().length < 8) {
    requestError.value = "请先描述你想研究的问题，至少写下一句完整要求。";
    return;
  }
  submitMutation.mutate(rawRequest.value.trim(), {
    onSuccess: (result) =>
      router.push({ name: "workspace-runner", params: { workspaceId: result.workspace_id } }),
    onError: (error) => {
      requestError.value = error instanceof Error ? error.message : "暂时无法开始分析。";
    },
  });
}

function resumeWorkspaceDeletion(workspace: Workspace): void {
  recoveryError.value = null;
  resumingWorkspaceId.value = workspace.id;
  deleteWorkspaceMutation.mutate(workspace.id, {
    onSuccess: () => {
      resumingWorkspaceId.value = null;
    },
    onError: () => {
      resumingWorkspaceId.value = null;
      recoveryError.value = workspaceDeletionIncompleteMessage;
    },
  });
}
</script>

<template>
  <div class="entry-page">
    <AppHeader />
    <main class="entry-main">
      <section class="entry-hero">
        <div class="eyebrow">开始一项研究</div>
        <h1>输入你的研究要求。</h1>
        <p>系统会先分析研究意图。确认方向后，再启动多源文献检索、题录规整和全文核验。</p>
        <form class="request-form" @submit.prevent="submit">
          <label class="sr-only" for="research-request">研究要求</label>
          <textarea
            id="research-request"
            v-model="rawRequest"
            placeholder="例如：我想研究睡眠质量与大学生心理健康的关系，重点关注实证研究和可获取全文的期刊论文。"
            :disabled="submitMutation.isPending.value"
          />
          <div class="request-footer">
            <span>支持中文或英文自然语言要求</span
            ><button
              class="primary-button"
              type="submit"
              :disabled="submitMutation.isPending.value"
            >
              <span>{{ submitMutation.isPending.value ? "正在建立工作区…" : "开始分析" }}</span
              ><ArrowRight :size="17" />
            </button>
          </div>
          <p v-if="requestError" class="form-error" role="alert">{{ requestError }}</p>
        </form>
        <section
          v-if="pendingDeletionWorkspaces.length"
          class="workspace-deletion-recovery"
          aria-labelledby="workspace-deletion-recovery-title"
        >
          <div class="workspace-deletion-recovery-heading">
            <CircleAlert :size="18" />
            <div>
              <h2 id="workspace-deletion-recovery-title">待完成删除</h2>
              <p>这些工作区已停止使用，继续删除会清理剩余的私有数据。</p>
            </div>
          </div>
          <ul>
            <li v-for="workspace in pendingDeletionWorkspaces" :key="workspace.id">
              <span>{{ workspace.name }}</span>
              <button
                class="secondary-button workspace-deletion-recovery-action"
                type="button"
                :disabled="resumingWorkspaceId === workspace.id"
                @click="resumeWorkspaceDeletion(workspace)"
              >
                <LoaderCircle v-if="resumingWorkspaceId === workspace.id" class="spin" :size="15" />
                <RotateCcw v-else :size="15" />
                <span>{{ resumingWorkspaceId === workspace.id ? "正在继续…" : "继续删除" }}</span>
              </button>
            </li>
          </ul>
          <p v-if="recoveryError" class="form-error" role="alert">{{ recoveryError }}</p>
        </section>
      </section>
    </main>
  </div>
</template>
