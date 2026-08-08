<script setup lang="ts">
import type { AnswerInlineNode } from "@/features/research/answer-markdown";

defineOptions({ name: "AnswerMarkdownInline" });

defineProps<{
  nodes: AnswerInlineNode[];
}>();

const emit = defineEmits<{
  inspectCitation: [index: number];
}>();
</script>

<template>
  <template v-for="(node, index) in nodes" :key="`${node.kind}-${index}`">
    <span v-if="node.kind === 'text'">{{ node.text }}</span>
    <strong v-else-if="node.kind === 'strong'">
      <AnswerMarkdownInline
        :nodes="node.children"
        @inspect-citation="emit('inspectCitation', $event)"
      />
    </strong>
    <em v-else-if="node.kind === 'emphasis'">
      <AnswerMarkdownInline
        :nodes="node.children"
        @inspect-citation="emit('inspectCitation', $event)"
      />
    </em>
    <code v-else-if="node.kind === 'code'">{{ node.text }}</code>
    <button
      v-else
      class="research-answer-citation"
      type="button"
      :aria-label="`查看引用 ${node.index}`"
      @click="emit('inspectCitation', node.index)"
    >
      [{{ node.index }}]
    </button>
  </template>
</template>
