<script setup lang="ts">
import { computed } from "vue";

import AnswerMarkdownInline from "@/features/research/AnswerMarkdownInline.vue";
import { parseAnswerMarkdown } from "@/features/research/answer-markdown";

const props = withDefaults(
  defineProps<{
    content: string;
    citationIndexes?: number[];
  }>(),
  { citationIndexes: () => [] },
);

const emit = defineEmits<{
  inspectCitation: [index: number];
}>();

const blocks = computed(() => parseAnswerMarkdown(props.content, props.citationIndexes));
</script>

<template>
  <div class="research-answer-markdown">
    <template v-for="(block, index) in blocks" :key="`${block.kind}-${index}`">
      <component :is="`h${block.level}`" v-if="block.kind === 'heading'">
        <AnswerMarkdownInline
          :nodes="block.content"
          @inspect-citation="emit('inspectCitation', $event)"
        />
      </component>
      <p v-else-if="block.kind === 'paragraph'">
        <AnswerMarkdownInline
          :nodes="block.content"
          @inspect-citation="emit('inspectCitation', $event)"
        />
      </p>
      <blockquote v-else-if="block.kind === 'blockquote'">
        <AnswerMarkdownInline
          :nodes="block.content"
          @inspect-citation="emit('inspectCitation', $event)"
        />
      </blockquote>
      <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.kind === 'list'">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <AnswerMarkdownInline :nodes="item" @inspect-citation="emit('inspectCitation', $event)" />
        </li>
      </component>
      <div v-else class="research-answer-table-wrap">
        <table>
          <thead>
            <tr>
              <th v-for="(header, headerIndex) in block.headers" :key="headerIndex" scope="col">
                <AnswerMarkdownInline
                  :nodes="header"
                  @inspect-citation="emit('inspectCitation', $event)"
                />
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="rowIndex">
              <td v-for="(cell, cellIndex) in row" :key="cellIndex">
                <AnswerMarkdownInline
                  :nodes="cell"
                  @inspect-citation="emit('inspectCitation', $event)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
