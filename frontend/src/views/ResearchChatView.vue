<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  ArrowLeft,
  ArrowUp,
  BookOpenCheck,
  ChevronDown,
  CircleAlert,
  FileStack,
  LoaderCircle,
  LogOut,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "@lucide/vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { getWorkspace, getCollectionDocuments } from "@/api/collections";
import { apiUrl, getAccessToken } from "@/api/client";
import {
  askResearchQuestion,
  cancelResearchRun,
  createConversation,
  deleteConversation,
  getConversation,
  getResearchRun,
  listConversations,
  retryResearchRun,
} from "@/api/research";
import type {
  Conversation,
  ResearchEvidence,
  ResearchProgressEvent,
  ResearchRun,
} from "@/api/types";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const auth = useAuthStore();

const workspaceId = computed(() => String(route.params.workspaceId));
const selectedConversationId = ref("");
const question = ref("");
const operationError = ref<string | null>(null);
const accountMenuOpen = ref(false);
const sidebarCollapsed = ref(false);
// 窄屏下侧栏以抽屉呈现，避免会话历史因布局压缩而不可访问。
const mobileSidebarOpen = ref(false);
const deleteConfirmId = ref<string | null>(null);
const activeRun = ref<ResearchRun | null>(null);
const progressEvent = ref<ResearchProgressEvent | null>(null);
const eventController = ref<AbortController | null>(null);
const streamedRunId = ref<string | null>(null);
let reconnectTimer: number | null = null;

const workspaceQuery = useQuery({
  queryKey: computed(() => ["workspace", workspaceId.value]),
  queryFn: () => getWorkspace(workspaceId.value),
});
const documentsQuery = useQuery({
  queryKey: computed(() => ["collection-documents", workspaceId.value]),
  queryFn: () => getCollectionDocuments(workspaceId.value),
});
const conversationsQuery = useQuery({
  queryKey: computed(() => ["research-conversations", workspaceId.value]),
  queryFn: () => listConversations(workspaceId.value),
});
const conversationQuery = useQuery({
  queryKey: computed(() => [
    "research-conversation",
    workspaceId.value,
    selectedConversationId.value,
  ]),
  queryFn: () => getConversation(workspaceId.value, selectedConversationId.value),
  enabled: computed(() => Boolean(selectedConversationId.value)),
});

const readyCount = computed(
  () => documentsQuery.data.value?.summary.researchable_document_count ?? 0,
);
const conversations = computed(() => conversationsQuery.data.value ?? []);
const currentConversation = computed(
  () => conversations.value.find((item) => item.id === selectedConversationId.value) ?? null,
);
const conversationDetail = computed(() => conversationQuery.data.value);
const pendingRun = computed(() => {
  const runs = conversationDetail.value?.runs ?? [];
  return (
    activeRun.value ?? [...runs].reverse().find((run) => run.output_message_id === null) ?? null
  );
});
const composerDisabled = computed(
  () => !readyCount.value || ["queued", "running"].includes(pendingRun.value?.status ?? ""),
);
const accountInitial = computed(() => (auth.user?.display_name ?? "研").slice(0, 1).toUpperCase());

const createConversationMutation = useMutation({
  mutationFn: () => createConversation(workspaceId.value),
});
const askQuestionMutation = useMutation({
  mutationFn: ({ conversationId, content }: { conversationId: string; content: string }) =>
    askResearchQuestion(workspaceId.value, conversationId, content),
});
const retryRunMutation = useMutation({
  mutationFn: ({ conversationId, runId }: { conversationId: string; runId: string }) =>
    retryResearchRun(workspaceId.value, conversationId, runId),
});
const cancelRunMutation = useMutation({
  mutationFn: ({ conversationId, runId }: { conversationId: string; runId: string }) =>
    cancelResearchRun(workspaceId.value, conversationId, runId),
});
const deleteConversationMutation = useMutation({
  mutationFn: (conversationId: string) => deleteConversation(workspaceId.value, conversationId),
});

function isTerminal(status: ResearchRun["status"]): boolean {
  return ["awaiting_clarification", "completed", "failed", "cancelled"].includes(status);
}

function conversationTitle(conversation: Conversation | null): string {
  return conversation?.title?.trim() || "新建研究对话";
}

function conversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function evidenceAuthors(evidence: ResearchEvidence): string {
  const authors = evidence.authors
    .map((author) => (typeof author.name === "string" ? author.name : ""))
    .filter(Boolean);
  return authors.length ? authors.slice(0, 3).join("、") : "作者信息待补全";
}

function evidenceLocation(evidence: ResearchEvidence): string {
  const locator = evidence.locator_snapshot ?? {};
  const pageStart = typeof locator.page_start === "number" ? locator.page_start : null;
  const pageEnd = typeof locator.page_end === "number" ? locator.page_end : null;
  const pages = pageStart
    ? pageEnd && pageEnd !== pageStart
      ? `第 ${pageStart}-${pageEnd} 页`
      : `第 ${pageStart} 页`
    : null;
  const sectionPath = Array.isArray(locator.section_path)
    ? locator.section_path.filter((item): item is string => typeof item === "string").join(" / ")
    : "";
  return [pages, sectionPath || null].filter(Boolean).join(" · ") || "原文定位已保存";
}

function runForOutputMessage(messageId: string): ResearchRun | null {
  return conversationDetail.value?.runs.find((run) => run.output_message_id === messageId) ?? null;
}

async function refreshConversation(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["research-conversations", workspaceId.value] }),
    queryClient.invalidateQueries({
      queryKey: ["research-conversation", workspaceId.value, selectedConversationId.value],
    }),
  ]);
  if (selectedConversationId.value) await conversationQuery.refetch();
  if (activeRun.value && isTerminal(activeRun.value.status)) activeRun.value = null;
}

async function chooseConversation(conversationId: string): Promise<void> {
  if (selectedConversationId.value === conversationId) return;
  eventController.value?.abort();
  selectedConversationId.value = conversationId;
  mobileSidebarOpen.value = false;
  activeRun.value = null;
  progressEvent.value = null;
  await router.replace({ query: { conversation: conversationId } });
}

async function createAndSelectConversation(): Promise<string> {
  const conversation = await createConversationMutation.mutateAsync();
  await queryClient.invalidateQueries({ queryKey: ["research-conversations", workspaceId.value] });
  await chooseConversation(conversation.id);
  return conversation.id;
}

async function newConversation(): Promise<void> {
  operationError.value = null;
  try {
    await createAndSelectConversation();
    mobileSidebarOpen.value = false;
    await nextTick();
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : "无法创建研究会话。";
  }
}

async function handleSubmit(): Promise<void> {
  const content = question.value.trim();
  if (!content || composerDisabled.value) return;
  operationError.value = null;
  try {
    const conversationId = selectedConversationId.value || (await createAndSelectConversation());
    const result = await askQuestionMutation.mutateAsync({ conversationId, content });
    question.value = "";
    activeRun.value = result.research_run;
    progressEvent.value = null;
    await refreshConversation();
    await streamRun(result.research_run);
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : "研究问题无法提交。";
  }
}

function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void handleSubmit();
  }
}

async function updateRunFromDatabase(runId: string): Promise<ResearchRun | null> {
  if (!selectedConversationId.value) return null;
  const run = await getResearchRun(workspaceId.value, selectedConversationId.value, runId);
  activeRun.value = run;
  return run;
}

async function streamRun(run: ResearchRun): Promise<void> {
  if (isTerminal(run.status) || streamedRunId.value === run.id) return;
  eventController.value?.abort();
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const controller = new AbortController();
  eventController.value = controller;
  streamedRunId.value = run.id;
  try {
    const response = await fetch(
      apiUrl(
        `/api/v1/collections/${workspaceId.value}/conversations/${run.conversation_id}/research-runs/${run.id}/events`,
      ),
      {
        headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
        signal: controller.signal,
      },
    );
    if (!response.ok || !response.body) throw new Error("无法建立研究进度流。");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    // SSE 分片不保证与事件边界对齐，必须先缓冲到空行再解析。
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split("\n\n");
      buffer = packets.pop() ?? "";
      for (const packet of packets) {
        const dataLine = packet.split("\n").find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        try {
          const event = JSON.parse(dataLine.slice(5).trim()) as ResearchProgressEvent;
          progressEvent.value = event;
          activeRun.value = {
            ...run,
            ...activeRun.value,
            status: event.status,
            stage: event.stage,
          };
          if (isTerminal(event.status)) {
            await refreshConversation();
            return;
          }
        } catch {
          // 心跳和不完整事件不应中断已建立的进度流，数据库轮询会负责恢复最终状态。
        }
      }
    }
  } catch (error) {
    if (controller.signal.aborted) return;
    operationError.value = error instanceof Error ? error.message : "研究进度连接中断。";
    try {
      const persisted = await updateRunFromDatabase(run.id);
      if (persisted && !isTerminal(persisted.status)) {
        reconnectTimer = window.setTimeout(() => void streamRun(persisted), 1_500);
      } else {
        await refreshConversation();
      }
    } catch {
      // 下一次用户操作或页面刷新仍会从 PostgreSQL 恢复，不保留浏览器内存作为状态真相。
    }
  } finally {
    if (streamedRunId.value === run.id) streamedRunId.value = null;
  }
}

async function retryCurrentRun(): Promise<void> {
  const run = pendingRun.value;
  if (!run || !selectedConversationId.value) return;
  operationError.value = null;
  try {
    const retried = await retryRunMutation.mutateAsync({
      conversationId: selectedConversationId.value,
      runId: run.id,
    });
    activeRun.value = retried;
    progressEvent.value = null;
    await refreshConversation();
    await streamRun(retried);
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : "研究任务无法重新投递。";
  }
}

async function cancelCurrentRun(): Promise<void> {
  const run = pendingRun.value;
  if (!run || !selectedConversationId.value) return;
  operationError.value = null;
  try {
    activeRun.value = await cancelRunMutation.mutateAsync({
      conversationId: selectedConversationId.value,
      runId: run.id,
    });
    eventController.value?.abort();
    await refreshConversation();
  } catch (error) {
    operationError.value =
      error instanceof Error ? error.message : "任务已被 Worker 领取，无法取消。";
  }
}

async function confirmDeleteConversation(): Promise<void> {
  const conversationId = deleteConfirmId.value;
  if (!conversationId) return;
  operationError.value = null;
  try {
    await deleteConversationMutation.mutateAsync(conversationId);
    deleteConfirmId.value = null;
    eventController.value?.abort();
    selectedConversationId.value = "";
    activeRun.value = null;
    await queryClient.invalidateQueries({
      queryKey: ["research-conversations", workspaceId.value],
    });
    const nextConversation = conversations.value.find((item) => item.id !== conversationId);
    if (nextConversation) await chooseConversation(nextConversation.id);
    else await router.replace({ query: {} });
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : "无法删除研究会话。";
  }
}

function signOut(): void {
  auth.clear();
  void router.push({ name: "login" });
}

watch(
  () => route.query.conversation,
  (value) => {
    if (typeof value === "string") selectedConversationId.value = value;
  },
  { immediate: true },
);

watch(
  conversations,
  (items) => {
    if (!items.length || items.some((item) => item.id === selectedConversationId.value)) return;
    void chooseConversation(items[0].id);
  },
  { immediate: true },
);

watch(
  () => conversationDetail.value?.runs,
  (runs) => {
    const resumable = [...(runs ?? [])]
      .reverse()
      .find((run) => !isTerminal(run.status) && run.output_message_id === null);
    if (!resumable) return;
    activeRun.value = resumable;
    void streamRun(resumable);
  },
  { deep: true },
);

onBeforeUnmount(() => {
  eventController.value?.abort();
  if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
});
</script>

<template>
  <div
    class="research-chat-shell"
    :class="{
      'research-chat-sidebar-collapsed': sidebarCollapsed,
      'research-chat-sidebar-mobile-open': mobileSidebarOpen,
    }"
  >
    <button
      v-if="mobileSidebarOpen"
      class="research-chat-mobile-backdrop"
      type="button"
      aria-label="关闭研究会话侧栏"
      @click="mobileSidebarOpen = false"
    />
    <aside
      id="research-chat-sidebar"
      class="research-chat-sidebar"
      aria-label="当前工作区的研究会话"
    >
      <RouterLink class="research-chat-brand" to="/" aria-label="返回研究入口">
        <span class="brand-mark">AS</span
        ><span class="research-chat-brand-copy">Academic Search</span>
      </RouterLink>
      <div class="research-chat-sidebar-context">
        <span class="eyebrow">当前工作区</span>
        <strong>{{ workspaceQuery.data.value?.name ?? "正在读取工作区…" }}</strong>
        <small>{{ readyCount }} 篇全文可问答</small>
      </div>
      <div class="research-chat-sidebar-actions">
        <button
          class="primary-button research-chat-new-button"
          type="button"
          aria-label="新建研究对话"
          @click="newConversation"
        >
          <Plus :size="16" /><span>新建研究对话</span>
        </button>
        <RouterLink
          class="research-chat-collection-link"
          :to="{ name: 'workspace-collection', params: { workspaceId } }"
          aria-label="打开研究集合"
          title="打开研究集合"
        >
          <FileStack :size="16" />
          <span><strong>研究集合</strong><small>查看全文、索引与范围</small></span>
        </RouterLink>
      </div>
      <nav class="research-chat-session-list" aria-label="会话记录">
        <span class="research-chat-session-label">当前工作区</span>
        <div
          v-for="conversation in conversations"
          :key="conversation.id"
          class="research-chat-session-row"
          :class="{ active: conversation.id === selectedConversationId }"
        >
          <button
            class="research-chat-session-item"
            type="button"
            :aria-current="conversation.id === selectedConversationId ? 'page' : undefined"
            @click="chooseConversation(conversation.id)"
          >
            <MessageSquareText :size="16" />
            <span class="research-chat-session-copy">
              <strong>{{ conversationTitle(conversation) }}</strong>
              <small
                >{{
                  conversation.message_count ? `${conversation.message_count} 条消息` : "尚未提问"
                }}
                · {{ conversationTime(conversation.updated_at) }}</small
              >
            </span>
          </button>
          <button
            class="research-chat-delete-session"
            type="button"
            title="删除会话"
            aria-label="删除会话"
            @click="deleteConfirmId = conversation.id"
          >
            <Trash2 :size="14" />
          </button>
        </div>
        <div v-if="conversationsQuery.isPending.value" class="research-chat-sidebar-empty">
          <LoaderCircle class="spin" :size="16" />正在读取会话…
        </div>
        <div v-else-if="!conversations.length" class="research-chat-sidebar-empty">
          <MessageSquareText :size="17" />从一个问题开始研究
        </div>
      </nav>
      <div class="research-chat-sidebar-footer">
        <RouterLink class="icon-button" to="/" title="返回研究入口" aria-label="返回研究入口"
          ><ArrowLeft :size="17"
        /></RouterLink>
        <button
          class="icon-button"
          type="button"
          :aria-label="sidebarCollapsed ? '展开研究会话侧栏' : '收起研究会话侧栏'"
          :title="sidebarCollapsed ? '展开研究会话侧栏' : '收起研究会话侧栏'"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <PanelLeftOpen v-if="sidebarCollapsed" :size="17" /><PanelLeftClose v-else :size="17" />
        </button>
      </div>
    </aside>

    <main class="research-chat-main">
      <header class="research-chat-header">
        <div class="research-chat-header-copy">
          <RouterLink
            class="research-chat-workspace-link"
            :to="{ name: 'workspace-collection', params: { workspaceId } }"
          >
            <FileStack :size="16" />{{ workspaceQuery.data.value?.name ?? "当前研究集合" }}
          </RouterLink>
          <h1>{{ conversationTitle(currentConversation) }}</h1>
          <p>
            <span><ShieldCheck :size="14" />当前集合 · {{ readyCount }} 篇全文可问答</span>
            <span>仅使用已核验并完成索引的正文</span>
          </p>
        </div>
        <div class="research-chat-header-actions">
          <button
            class="icon-button research-chat-mobile-sidebar-toggle"
            type="button"
            aria-controls="research-chat-sidebar"
            :aria-expanded="mobileSidebarOpen"
            :aria-label="mobileSidebarOpen ? '关闭研究会话侧栏' : '打开研究会话侧栏'"
            :title="mobileSidebarOpen ? '关闭研究会话侧栏' : '打开研究会话侧栏'"
            @click="mobileSidebarOpen = !mobileSidebarOpen"
          >
            <MessageSquareText :size="17" />
          </button>
          <RouterLink
            class="secondary-button research-chat-header-collection"
            :to="{ name: 'workspace-collection', params: { workspaceId } }"
            aria-label="打开研究集合"
            title="打开研究集合"
          >
            <BookOpenCheck :size="16" /><span>研究集合</span>
          </RouterLink>
          <div class="menu-wrap">
            <button
              class="account-button"
              type="button"
              aria-haspopup="menu"
              :aria-expanded="accountMenuOpen"
              :aria-label="`打开 ${auth.user?.display_name ?? '当前用户'} 的账号菜单`"
              title="账号菜单"
              @click="accountMenuOpen = !accountMenuOpen"
            >
              <span class="avatar">{{ accountInitial }}</span
              ><span class="account-name">{{ auth.user?.display_name }}</span
              ><ChevronDown :size="14" />
            </button>
            <div v-if="accountMenuOpen" class="popover account-popover">
              <div class="identity">
                <strong>{{ auth.user?.display_name }}</strong
                ><span>{{ auth.user?.email }}</span>
              </div>
              <button class="popover-action" type="button" @click="signOut">
                <LogOut :size="15" />退出登录
              </button>
            </div>
          </div>
        </div>
      </header>

      <section class="research-chat-thread-region" aria-live="polite">
        <div
          v-if="workspaceQuery.isError.value || documentsQuery.isError.value"
          class="failure-panel"
        >
          <CircleAlert :size="18" />
          <div>
            <strong>研究集合无法读取</strong>
            <p>请返回集合页确认当前工作区的全文索引状态。</p>
          </div>
          <RouterLink
            class="secondary-button"
            :to="{ name: 'workspace-collection', params: { workspaceId } }"
            >查看集合</RouterLink
          >
        </div>
        <template v-else>
          <article class="research-chat-welcome">
            <span class="research-chat-assistant-avatar"><Sparkles :size="18" /></span>
            <div>
              <span class="eyebrow">证据研究助手</span>
              <h2>在当前集合中提问。</h2>
              <p>回答只会引用已完成索引的原文片段；证据不足时会明确说明，不会用模型记忆补全。</p>
            </div>
          </article>

          <div
            v-if="conversationQuery.isPending.value && selectedConversationId"
            class="loading-state research-chat-loading"
          >
            <LoaderCircle class="spin" :size="18" />正在恢复研究会话…
          </div>
          <div v-else-if="conversationQuery.isError.value" class="failure-panel">
            <CircleAlert :size="18" />
            <div>
              <strong>会话读取失败</strong>
              <p>刷新后将从服务端恢复消息和运行状态。</p>
            </div>
            <button class="secondary-button" type="button" @click="conversationQuery.refetch()">
              <RotateCcw :size="15" />重新读取
            </button>
          </div>
          <div v-else class="research-chat-thread">
            <article
              v-for="message in conversationDetail?.messages ?? []"
              :key="message.id"
              class="research-chat-message"
              :class="`research-chat-message-${message.role}`"
            >
              <span v-if="message.role !== 'user'" class="research-chat-assistant-avatar"
                ><Sparkles :size="17"
              /></span>
              <div class="research-chat-message-content">
                <div class="research-chat-message-meta">
                  <strong>{{ message.role === "user" ? "你" : "研究助手" }}</strong>
                  <span
                    v-if="
                      message.role === 'assistant' &&
                      runForOutputMessage(message.id)?.evidences.length
                    "
                    ><ShieldCheck :size="13" />{{
                      runForOutputMessage(message.id)?.evidences.length
                    }}
                    条引用已核验</span
                  >
                  <span v-else>{{ message.status === "completed" ? "已保存" : "处理中" }}</span>
                </div>
                <p class="research-chat-message-body">{{ message.content }}</p>
                <details
                  v-if="runForOutputMessage(message.id)?.evidences.length"
                  class="research-chat-evidence-details"
                >
                  <summary>
                    <span><BookOpenCheck :size="16" />引用来源</span
                    ><small
                      >{{ runForOutputMessage(message.id)?.evidences.length }} 个证据片段</small
                    >
                  </summary>
                  <ol class="research-chat-evidence-list">
                    <li
                      v-for="(evidence, index) in runForOutputMessage(message.id)?.evidences"
                      :key="evidence.id"
                    >
                      <span class="research-chat-evidence-index">{{ index + 1 }}</span>
                      <div>
                        <strong>{{ evidence.title }}</strong>
                        <a
                          v-if="evidence.source_url"
                          :href="evidence.source_url"
                          target="_blank"
                          rel="noreferrer"
                          >{{ evidenceAuthors(evidence) }} ·
                          {{ evidence.publication_year ?? "年份待补全" }}</a
                        >
                        <span v-else
                          >{{ evidenceAuthors(evidence) }} ·
                          {{ evidence.publication_year ?? "年份待补全" }}</span
                        >
                        <p>{{ evidence.citation_excerpt ?? "该证据未返回可展示摘录。" }}</p>
                        <small>{{ evidenceLocation(evidence) }}</small>
                      </div>
                    </li>
                  </ol>
                </details>
              </div>
            </article>

            <article
              v-if="pendingRun"
              class="research-chat-run-status"
              :class="`run-${pendingRun.status}`"
            >
              <span class="research-chat-assistant-avatar"
                ><LoaderCircle
                  v-if="!isTerminal(pendingRun.status)"
                  class="spin"
                  :size="17" /><CircleAlert v-else :size="17"
              /></span>
              <div>
                <strong>{{ progressEvent?.message ?? pendingRun.stage_display.label }}</strong>
                <p>{{ pendingRun.error_message ?? pendingRun.stage_display.description }}</p>
                <small v-if="progressEvent"
                  >已确认 {{ progressEvent.evidence_count }} 个候选证据片段</small
                >
                <div v-if="pendingRun.status === 'failed'" class="research-chat-run-actions">
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="retryRunMutation.isPending.value"
                    @click="retryCurrentRun"
                  >
                    <RotateCcw :size="15" />重新投递
                  </button>
                </div>
                <div v-else-if="pendingRun.status === 'queued'" class="research-chat-run-actions">
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="cancelRunMutation.isPending.value"
                    @click="cancelCurrentRun"
                  >
                    <X :size="15" />取消任务
                  </button>
                </div>
              </div>
            </article>

            <div
              v-if="!selectedConversationId && !conversationQuery.isPending.value"
              class="research-chat-empty-thread"
            >
              <MessageSquareText :size="22" /><strong>新建一段研究对话</strong>
              <p>输入问题后会自动建立会话，并将问题排入研究任务。</p>
            </div>
          </div>
        </template>
      </section>

      <footer class="research-chat-composer-wrap">
        <p v-if="operationError" class="research-chat-operation-error" role="alert">
          {{ operationError }}
        </p>
        <form class="research-chat-composer" @submit.prevent="handleSubmit">
          <textarea
            v-model="question"
            rows="1"
            :disabled="composerDisabled"
            :placeholder="readyCount ? '在当前集合中继续提问…' : '等待至少一篇全文完成索引…'"
            aria-label="继续研究当前集合"
            @keydown="handleComposerKeydown"
          />
          <button
            class="research-chat-send"
            type="submit"
            :disabled="!question.trim() || composerDisabled"
            aria-label="发送研究问题"
            title="发送研究问题"
          >
            <ArrowUp :size="18" />
          </button>
        </form>
        <div class="research-chat-composer-note">
          <span>回答将附带原文定位；证据不足时会明确说明。</span
          ><span><kbd>Enter</kbd> 发送 · <kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span>
        </div>
      </footer>
    </main>

    <Teleport to="body">
      <section
        v-if="deleteConfirmId"
        class="research-chat-delete-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-conversation-title"
      >
        <div class="research-chat-delete-dialog">
          <button
            class="icon-button research-chat-dialog-close"
            type="button"
            title="关闭"
            @click="deleteConfirmId = null"
          >
            <X :size="16" />
          </button>
          <span class="eyebrow">删除研究会话</span>
          <h2 id="delete-conversation-title">删除这段会话？</h2>
          <p>会话会从列表中移除。已有研究运行和引用审计记录仍由服务端保留，不会影响其他会话。</p>
          <div>
            <button class="secondary-button" type="button" @click="deleteConfirmId = null">
              保留会话</button
            ><button
              class="danger-button"
              type="button"
              :disabled="deleteConversationMutation.isPending.value"
              @click="confirmDeleteConversation"
            >
              <Trash2 :size="15" />删除会话
            </button>
          </div>
        </div>
      </section>
    </Teleport>
  </div>
</template>
