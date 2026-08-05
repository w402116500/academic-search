<script setup lang="ts">
import { ref } from "vue";
import { ArrowRight } from "@lucide/vue";
import { useRouter } from "vue-router";

import AppHeader from "@/components/AppHeader.vue";
import { useStartResearchMutation } from "@/api/hooks/research";

const router = useRouter();
const rawRequest = ref("");
const requestError = ref<string | null>(null);
const submitMutation = useStartResearchMutation();

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
      </section>
    </main>
  </div>
</template>
