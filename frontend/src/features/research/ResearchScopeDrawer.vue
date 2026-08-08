<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowLeft, ExternalLink, FileStack, LoaderCircle, X } from "@lucide/vue";

import type { CollectionDocument } from "@/api/types";

const props = defineProps<{
  documents: CollectionDocument[];
  loading: boolean;
  error: boolean;
  selectedDocumentId?: string | null;
}>();

const open = defineModel<boolean>("open", { required: true });
const activeDocumentId = ref<string | null>(null);
const mobileDetailOpen = ref(false);
const selectedDocument = computed(
  () => props.documents.find((document) => document.document_id === activeDocumentId.value) ?? null,
);

watch(
  [() => props.documents, () => props.selectedDocumentId],
  ([documents, selectedDocumentId]) => {
    if (
      selectedDocumentId &&
      documents.some((document) => document.document_id === selectedDocumentId)
    ) {
      activeDocumentId.value = selectedDocumentId;
      mobileDetailOpen.value = true;
      return;
    }
    if (!documents.some((document) => document.document_id === activeDocumentId.value)) {
      activeDocumentId.value = documents[0]?.document_id ?? null;
    }
  },
  { immediate: true },
);

watch(open, (visible) => {
  if (!visible) mobileDetailOpen.value = false;
});

function close(): void {
  open.value = false;
  mobileDetailOpen.value = false;
}

function selectDocument(documentId: string): void {
  activeDocumentId.value = documentId;
  mobileDetailOpen.value = true;
}

function authorNames(document: CollectionDocument): string {
  const names = document.authors
    .map((author) => {
      const literal = typeof author.literal === "string" ? author.literal.trim() : "";
      if (literal) return literal;
      return [author.given, author.family]
        .filter((part): part is string => typeof part === "string" && Boolean(part.trim()))
        .join(" ");
    })
    .filter(Boolean);
  return names.length ? names.join("、") : "作者待补全";
}

function ingestionStatus(document: CollectionDocument): string {
  switch (document.latest_ingestion_run?.status) {
    case "completed":
      return "可用于研究";
    case "running":
      return "正在入库";
    case "queued":
      return "等待入库";
    case "failed":
      return "入库失败";
    default:
      return "待构建";
  }
}
</script>

<template>
  <Teleport to="body">
    <section
      v-if="open"
      class="research-scope-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="research-scope-title"
    >
      <button
        class="research-scope-backdrop"
        type="button"
        aria-label="关闭研究范围遮罩"
        @click="close"
      />
      <aside
        class="research-scope-drawer"
        :class="{ 'research-scope-mobile-detail': mobileDetailOpen && selectedDocument }"
      >
        <header class="research-scope-header">
          <div>
            <span class="eyebrow">当前研究范围</span>
            <h2 id="research-scope-title">研究范围文献</h2>
          </div>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭研究范围"
            title="关闭研究范围"
            @click="close"
          >
            <X :size="17" />
          </button>
        </header>

        <div v-if="loading" class="research-scope-state">
          <LoaderCircle class="spin" :size="18" />正在读取研究范围…
        </div>
        <div v-else-if="error" class="research-scope-state research-scope-state-error">
          当前无法读取研究范围，请稍后重试。
        </div>
        <div v-else-if="!documents.length" class="research-scope-state">
          <FileStack :size="19" />当前范围还没有文献。
        </div>
        <div v-else class="research-scope-layout">
          <nav class="research-scope-list" aria-label="研究范围文献列表">
            <button
              v-for="document in documents"
              :key="document.document_id"
              class="research-scope-document-item"
              :class="{ active: document.document_id === selectedDocument?.document_id }"
              type="button"
              :aria-current="
                document.document_id === selectedDocument?.document_id ? 'true' : undefined
              "
              @click="selectDocument(document.document_id)"
            >
              <FileStack :size="16" />
              <span>
                <strong>{{ document.title }}</strong>
                <small
                  >{{ authorNames(document) }} ·
                  {{ document.publication_year ?? "年份待补全" }}</small
                >
              </span>
            </button>
          </nav>

          <article v-if="selectedDocument" class="research-scope-detail">
            <button
              class="research-scope-back-to-list"
              type="button"
              @click="mobileDetailOpen = false"
            >
              <ArrowLeft :size="15" />返回文献列表
            </button>
            <div class="research-scope-detail-heading">
              <span class="research-scope-ingestion-status">{{
                ingestionStatus(selectedDocument)
              }}</span>
              <h3>{{ selectedDocument.title }}</h3>
              <p>{{ authorNames(selectedDocument) }}</p>
            </div>

            <dl class="research-scope-metadata">
              <div>
                <dt>期刊 / 会议</dt>
                <dd>{{ selectedDocument.venue ?? "刊物待补全" }}</dd>
              </div>
              <div>
                <dt>发表年份</dt>
                <dd>{{ selectedDocument.publication_year ?? "年份待补全" }}</dd>
              </div>
              <div>
                <dt>DOI</dt>
                <dd>{{ selectedDocument.doi }}</dd>
              </div>
              <div>
                <dt>访问权限</dt>
                <dd>{{ selectedDocument.access_rights }}</dd>
              </div>
            </dl>

            <section
              class="research-scope-citation"
              aria-labelledby="research-scope-citation-title"
            >
              <span id="research-scope-citation-title">正式引用</span>
              <p>{{ selectedDocument.citation_text }}</p>
            </section>

            <section
              v-if="selectedDocument.tags.length || selectedDocument.note"
              class="research-scope-notes"
            >
              <div
                v-if="selectedDocument.tags.length"
                class="research-scope-tags"
                aria-label="文献标签"
              >
                <span v-for="tag in selectedDocument.tags" :key="tag">{{ tag }}</span>
              </div>
              <p v-if="selectedDocument.note">{{ selectedDocument.note }}</p>
            </section>

            <a
              v-if="selectedDocument.source_url"
              class="secondary-button research-scope-source-link"
              :href="selectedDocument.source_url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink :size="15" />打开来源页面
            </a>
          </article>
        </div>
      </aside>
    </section>
  </Teleport>
</template>
