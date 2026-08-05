<script setup lang="ts">
import { computed } from "vue";
import {
  Check,
  FileText,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  RotateCcw,
  Sparkles,
  Trash2,
} from "@lucide/vue";
import { RouterLink, useRoute } from "vue-router";

import { useCollectionDocumentsQuery, useCollectionMutations } from "@/api/hooks/research";

const route = useRoute();
const workspaceId = computed(() => String(route.params.workspaceId));
const documentsQuery = useCollectionDocumentsQuery(workspaceId, true);
const {
  buildCollectionMutation: buildMutation,
  removePendingDocumentMutation: removeMutation,
  refreshDocuments,
} = useCollectionMutations(workspaceId);

const pendingCount = computed(
  () => documentsQuery.data.value?.summary.ingestion_status_counts?.pending ?? 0,
);
const readyCount = computed(
  () => documentsQuery.data.value?.summary.researchable_document_count ?? 0,
);
</script>

<template>
  <section class="stage-view collection-view">
    <div class="view-heading">
      <div>
        <div class="eyebrow">研究集合 / INDEX</div>
        <h1>把已确认的文献，变成可研究的范围。</h1>
        <p>集合管理和研究对话分开。只有完成解析、分块、嵌入和索引的文献，才会解锁后续证据研究。</p>
      </div>
      <span class="status-chip status-neutral">{{ readyCount }} 篇可研究</span>
    </div>
    <div v-if="documentsQuery.isPending.value" class="loading-state">
      <LoaderCircle class="spin" :size="18" />正在读取集合状态…
    </div>
    <div v-else-if="documentsQuery.isError.value" class="failure-panel">
      <strong>集合状态读取失败</strong>
      <p>刷新页面后会从 PostgreSQL 恢复最新入库状态。</p>
      <button class="secondary-button" type="button" @click="refreshDocuments">
        <RotateCcw :size="15" />重新读取
      </button>
    </div>
    <template v-else-if="documentsQuery.data.value">
      <div class="collection-summary">
        <div>
          <span>活动文献</span
          ><strong>{{ documentsQuery.data.value.summary.active_document_count }}</strong>
        </div>
        <div>
          <span>待确认构建</span><strong>{{ pendingCount }}</strong>
        </div>
        <div class="highlight">
          <span>可进入研究</span><strong>{{ readyCount }}</strong>
        </div>
      </div>
      <div class="collection-actions">
        <button
          class="primary-button"
          type="button"
          :disabled="!pendingCount || buildMutation.isPending.value"
          @click="buildMutation.mutate()"
        >
          <Sparkles :size="16" />{{
            buildMutation.isPending.value ? "正在投递…" : "确认并构建集合"
          }}</button
        ><span v-if="pendingCount">确认后会开始解析、切块、嵌入和 Milvus 建索引。</span>
      </div>
      <div class="document-list">
        <article
          v-for="document in documentsQuery.data.value.documents"
          :key="document.document_id"
          class="document-row"
        >
          <span class="document-icon"><FileText :size="17" /></span>
          <div class="document-copy">
            <strong>{{ document.title }}</strong
            ><small
              >{{ document.doi }} · {{ document.publication_year ?? "年份待补全" }} ·
              {{ document.original_filename }}</small
            >
          </div>
          <div class="document-status">
            <span :class="`ingestion-${document.latest_ingestion_run?.status ?? 'pending'}`">{{
              document.latest_ingestion_run?.status === "completed"
                ? "可研究"
                : document.latest_ingestion_run?.status === "running"
                  ? "处理中"
                  : document.latest_ingestion_run?.status === "queued"
                    ? "排队中"
                    : document.latest_ingestion_run?.status === "failed"
                      ? "失败"
                      : "待构建"
            }}</span
            ><small v-if="document.latest_ingestion_run?.error_message">{{
              document.latest_ingestion_run.error_message
            }}</small>
          </div>
          <button
            v-if="document.latest_ingestion_run?.status === 'pending'"
            class="icon-button"
            type="button"
            title="移出待确认集合"
            @click="removeMutation.mutate(document.document_id)"
          >
            <Trash2 :size="15" />
          </button>
        </article>
        <div v-if="!documentsQuery.data.value.documents.length" class="empty-state">
          <LockKeyhole :size="22" /><strong>集合还是空的</strong>
          <p>回到文献结果页，先加入通过全文核验的文献。</p>
        </div>
      </div>
      <div class="research-lock" :class="{ 'research-unlocked': readyCount > 0 }">
        <MessageSquareText v-if="readyCount" :size="18" />
        <LockKeyhole v-else :size="18" />
        <div>
          <strong>{{ readyCount ? "研究对话已解锁" : "研究对话暂未解锁" }}</strong>
          <p>
            {{
              readyCount
                ? "对话只会检索当前集合中已完成解析、切块和索引的全文。"
                : "至少完成一篇文献的解析和索引后，才会出现研究入口。"
            }}
          </p>
        </div>
        <RouterLink
          v-if="readyCount"
          class="primary-button research-chat-link"
          :to="{ name: 'workspace-research-chat', params: { workspaceId } }"
        >
          <MessageSquareText :size="15" />进入文献研究
        </RouterLink>
        <span v-else><Check :size="14" />证据边界由当前集合决定</span>
      </div>
    </template>
  </section>
</template>
