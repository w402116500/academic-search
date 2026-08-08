<script setup lang="ts">
import { computed } from "vue";
import { BookOpenCheck, FileStack } from "@lucide/vue";

import type { ResearchEvidence, ResearchRun } from "@/api/types";
import {
  candidateEvidences,
  citedEvidences,
  evidenceAuthors,
  evidenceElementId,
  evidenceLocation,
} from "@/features/research/research-chat-presentation";

const props = defineProps<{
  run: ResearchRun;
  highlightedEvidenceId?: string | null;
  unavailableDocumentEvidenceId?: string | null;
}>();

const sourcesOpen = defineModel<boolean>("sourcesOpen", { default: false });
const emit = defineEmits<{
  openDocument: [evidence: ResearchEvidence];
}>();

const cited = computed(() => citedEvidences(props.run));
const candidates = computed(() => candidateEvidences(props.run));

function updateSourcesOpen(event: Event): void {
  if (event.currentTarget instanceof HTMLDetailsElement) {
    sourcesOpen.value = event.currentTarget.open;
  }
}
</script>

<template>
  <details
    v-if="cited.length"
    class="research-chat-evidence-details"
    :open="sourcesOpen"
    @toggle="updateSourcesOpen"
  >
    <summary>
      <span><BookOpenCheck :size="16" />引用来源</span>
      <small>{{ cited.length }} 条已引用证据</small>
    </summary>
    <ol class="research-chat-evidence-list">
      <li
        v-for="evidence in cited"
        :id="evidenceElementId(run.id, evidence.id)"
        :key="evidence.id"
        :class="{ 'is-highlighted': highlightedEvidenceId === evidence.id }"
        :tabindex="highlightedEvidenceId === evidence.id ? -1 : undefined"
      >
        <span class="research-chat-evidence-index">{{ evidence.display_index }}</span>
        <div>
          <button
            class="research-chat-evidence-title"
            type="button"
            :title="`查看《${evidence.title}》详情`"
            @click="emit('openDocument', evidence)"
          >
            {{ evidence.title }}
          </button>
          <span class="research-chat-evidence-meta"
            >{{ evidenceAuthors(evidence) }} · {{ evidence.publication_year ?? "年份待补全" }}</span
          >
          <p>{{ evidence.citation_excerpt ?? "该证据未返回可展示摘录。" }}</p>
          <small>{{ evidenceLocation(evidence) }}</small>
          <span
            v-if="unavailableDocumentEvidenceId === evidence.id"
            class="research-chat-evidence-notice"
            role="status"
          >
            当前研究范围未保存这篇文献，无法打开详情。
          </span>
        </div>
      </li>
    </ol>
  </details>

  <details
    v-if="candidates.length"
    class="research-chat-evidence-details research-chat-candidates-details"
  >
    <summary>
      <span><FileStack :size="16" />候选证据</span>
      <small>{{ candidates.length }} 条未引用证据</small>
    </summary>
    <ol class="research-chat-evidence-list">
      <li
        v-for="evidence in candidates"
        :id="evidenceElementId(run.id, evidence.id)"
        :key="evidence.id"
        :class="{ 'is-highlighted': highlightedEvidenceId === evidence.id }"
        :tabindex="highlightedEvidenceId === evidence.id ? -1 : undefined"
      >
        <span class="research-chat-evidence-index">候</span>
        <div>
          <button
            class="research-chat-evidence-title"
            type="button"
            :title="`查看《${evidence.title}》详情`"
            @click="emit('openDocument', evidence)"
          >
            {{ evidence.title }}
          </button>
          <span class="research-chat-evidence-meta"
            >{{ evidenceAuthors(evidence) }} · {{ evidence.publication_year ?? "年份待补全" }}</span
          >
          <p>{{ evidence.citation_excerpt ?? "该候选证据未返回可展示摘录。" }}</p>
          <small>{{ evidenceLocation(evidence) }}</small>
          <span
            v-if="unavailableDocumentEvidenceId === evidence.id"
            class="research-chat-evidence-notice"
            role="status"
          >
            当前研究范围未保存这篇文献，无法打开详情。
          </span>
        </div>
      </li>
    </ol>
  </details>
</template>
