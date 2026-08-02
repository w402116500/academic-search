<script setup lang="ts">
import { Check, FileSearch, LockKeyhole, Sparkles } from "@lucide/vue";
import type { WorkflowStage } from "@/api/types";

defineProps<{ current: WorkflowStage }>();

type ProductStage = "analysis" | "screening" | "research";

const stages: { key: ProductStage; label: string; detail: string; icon: typeof Sparkles }[] = [
  { key: "analysis", label: "任务解析", detail: "方向与范围", icon: Sparkles },
  { key: "screening", label: "文献筛选", detail: "检索与审核", icon: FileSearch },
  { key: "research", label: "证据研究", detail: "集合与问答", icon: LockKeyhole },
];

// 后端保留更细的执行阶段；这里仅把它们投影成用户理解的三个产品阶段。
function productStage(stage: WorkflowStage): ProductStage {
  if (["retrieving", "screening"].includes(stage)) return "screening";
  if (["collection_building", "researching"].includes(stage)) return "research";
  return "analysis";
}

const order: ProductStage[] = ["analysis", "screening", "research"];
const stageState = (current: WorkflowStage, key: ProductStage): "done" | "active" | "locked" => {
  const currentIndex = order.indexOf(productStage(current));
  const stageIndex = order.indexOf(key);
  return stageIndex < currentIndex ? "done" : stageIndex === currentIndex ? "active" : "locked";
};
</script>

<template>
  <nav class="stage-rail" aria-label="研究阶段">
    <div
      v-for="stage in stages"
      :key="stage.key"
      class="stage-item"
      :class="`stage-${stageState(current, stage.key)}`"
    >
      <span class="stage-icon"
        ><Check v-if="stageState(current, stage.key) === 'done'" :size="15" /><component
          :is="stage.icon"
          v-else
          :size="15"
      /></span>
      <span class="stage-copy"
        ><span class="stage-label">{{ stage.label }}</span
        ><small>{{ stage.detail }}</small></span
      >
    </div>
  </nav>
</template>
