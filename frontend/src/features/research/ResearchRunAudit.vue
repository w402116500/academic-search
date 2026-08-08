<script setup lang="ts">
import { computed } from "vue";
import { ShieldCheck } from "@lucide/vue";

import type { ResearchProgressEvent, ResearchRun } from "@/api/types";
import {
  citationAuditLabel,
  governanceSummary,
  isStrictResearch,
  researchExecutionModeLabel,
} from "@/features/research/research-chat-presentation";

const props = defineProps<{
  run: ResearchRun;
  progressHistory: ResearchProgressEvent[];
}>();

const modeLabel = computed(() => researchExecutionModeLabel(props.run));
const auditLabel = computed(() => citationAuditLabel(props.run));
const summary = computed(() => governanceSummary(props.run));
const stages = computed(() => (isStrictResearch(props.run) ? props.progressHistory : []));
const visible = computed(() =>
  Boolean(modeLabel.value || auditLabel.value || summary.value || stages.value.length),
);
</script>

<template>
  <aside v-if="visible" class="research-run-audit" aria-label="回答运行摘要">
    <span v-if="modeLabel" class="research-run-audit-mode">{{ modeLabel }}</span>
    <span v-if="auditLabel" class="research-run-audit-check"
      ><ShieldCheck :size="13" />{{ auditLabel }}</span
    >
    <small v-if="summary">{{ summary }}</small>
    <details v-if="stages.length" class="research-run-stage-details">
      <summary>本次运行轨迹</summary>
      <ol>
        <li v-for="(stage, index) in stages" :key="`${stage.stage}-${index}`">
          {{ stage.message || stage.stage }}
        </li>
      </ol>
    </details>
  </aside>
</template>
