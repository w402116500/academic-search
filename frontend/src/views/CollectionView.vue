<script setup lang="ts">
import { computed } from "vue";
import {
  Check,
  FileText,
  LoaderCircle,
  LockKeyhole,
  MessageSquareText,
  RotateCcw,
} from "@lucide/vue";
import { RouterLink, useRoute } from "vue-router";

import { useCollectionDocumentsQuery, useCollectionMutations } from "@/api/hooks/research";
import type { CollectionBibliographyEntry, CollectionDocument } from "@/api/types";

const route = useRoute();
const workspaceId = computed(() => String(route.params.workspaceId));
const documentsQuery = useCollectionDocumentsQuery(workspaceId, true);
const { refreshDocuments } = useCollectionMutations(workspaceId);

const bibliographyEntries = computed(() => documentsQuery.data.value?.bibliography_entries ?? []);
const documents = computed(() => documentsQuery.data.value?.documents ?? []);
const documentByEntryId = computed(
  () => new Map(documents.value.map((document) => [document.bibliography_entry_id, document])),
);
const collectionCount = computed(
  () =>
    documentsQuery.data.value?.summary.bibliography_entry_count ?? bibliographyEntries.value.length,
);
const readyCount = computed(
  () => documentsQuery.data.value?.summary.researchable_document_count ?? 0,
);
const needsUploadCount = computed(
  () =>
    bibliographyEntries.value.filter((entry) => entry.content_status === "requires_upload").length,
);
const ingestingCount = computed(
  () =>
    bibliographyEntries.value.filter((entry) =>
      ["pending_auto_download", "document_ready", "ingesting"].includes(entry.content_status),
    ).length,
);
const bibliographyRows = computed(() =>
  bibliographyEntries.value.map((entry) => {
    const document = documentByEntryId.value.get(entry.id);
    const status = bibliographyStatus(entry, document);
    return {
      entry,
      document,
      meta: bibliographyMeta(entry, document),
      citationLabel: entry.citation_status === "ready" ? "题录已核验" : "该题录暂不可用",
      pdfLabel: entry.pdf_status === "available" ? "可自动获取 PDF" : "需上传 PDF",
      ...status,
    };
  }),
);

function bibliographyMeta(
  entry: CollectionBibliographyEntry,
  document: CollectionDocument | undefined,
): string {
  const parts = [
    entry.doi ?? "DOI 待补全",
    entry.publication_year ? String(entry.publication_year) : "年份待补全",
    document?.original_filename ?? entry.venue ?? "PDF 待上传",
  ];
  return parts.join(" · ");
}

function bibliographyStatus(
  entry: CollectionBibliographyEntry,
  document: CollectionDocument | undefined,
): { statusClass: string; statusLabel: string; statusHint: string } {
  if (
    entry.content_status === "researchable" ||
    document?.latest_ingestion_run?.status === "completed"
  ) {
    return {
      statusClass: "entry-status-rag",
      statusLabel: "已进入 RAG",
      statusHint: "当前全文已完成入库",
    };
  }
  if (
    entry.content_status === "requires_upload" ||
    entry.content_status === "failed" ||
    entry.content_status === "cancelled"
  ) {
    return {
      statusClass: "entry-status-upload",
      statusLabel: "需上传 PDF",
      statusHint: "等待补充可读取全文",
    };
  }
  return {
    statusClass: "entry-status-progress",
    statusLabel: "正在入库",
    statusHint: "系统正在获取或处理全文",
  };
}
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
          <span>研究集合</span><strong>{{ collectionCount }}</strong>
        </div>
        <div>
          <span>RAG 研究范围</span><strong>{{ readyCount }}</strong>
        </div>
        <div class="highlight">
          <span>需上传 PDF</span><strong>{{ needsUploadCount }}</strong>
        </div>
      </div>
      <div class="collection-actions">
        <span>{{ ingestingCount }} 篇正在入库；RAG 问答只使用已完成入库的全文证据。</span>
      </div>
      <div class="document-list">
        <article v-for="row in bibliographyRows" :key="row.entry.id" class="document-row">
          <span class="document-icon"><FileText :size="17" /></span>
          <div class="document-copy">
            <strong>{{ row.entry.title }}</strong>
            <small>{{ row.meta }}</small>
            <div class="document-badges">
              <span>{{ row.citationLabel }}</span>
              <span>{{ row.pdfLabel }}</span>
            </div>
          </div>
          <div class="document-status">
            <span :class="row.statusClass">{{ row.statusLabel }}</span>
            <small>{{ row.statusHint }}</small>
          </div>
        </article>
        <div v-if="!bibliographyRows.length" class="empty-state">
          <LockKeyhole :size="22" /><strong>集合还是空的</strong>
          <p>从候选审核页加入想保留的文献。</p>
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
                ? "对话只会检索当前 RAG 研究范围内的全文。"
                : "至少一篇文献进入 RAG 研究范围后，才会出现研究入口。"
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
        <span v-else><Check :size="14" />{{ ingestingCount }} 篇正在入库</span>
      </div>
    </template>
  </section>
</template>
