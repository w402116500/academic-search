<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import {
  ArrowUp,
  BookOpenCheck,
  ChevronDown,
  CircleAlert,
  FileStack,
  LoaderCircle,
  LogOut,
  MessageSquareText,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "@lucide/vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { useResearchQueries } from "@/api/hooks/research";
import type { ResearchEvidence, ResearchQuestionMode, ResearchRun } from "@/api/types";
import ResearchAnswerMarkdown from "@/features/research/ResearchAnswerMarkdown.vue";
import ResearchEvidencePanel from "@/features/research/ResearchEvidencePanel.vue";
import ResearchRunAudit from "@/features/research/ResearchRunAudit.vue";
import ResearchScopeDrawer from "@/features/research/ResearchScopeDrawer.vue";
import {
  isResearchRunTerminal,
  useResearchProgress,
} from "@/features/research/use-research-progress";
import ConversationSidebar from "@/features/research/ConversationSidebar.vue";
import {
  cancellationRequested,
  citedEvidenceIndexes,
  citedEvidences,
  conversationTitle,
  evidenceElementId,
  governanceSummary,
  isEvidenceInsufficientRun,
  rerankerDisabled,
  researchRunForOutputMessage,
} from "@/features/research/research-chat-presentation";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const workspaceId = computed(() => String(route.params.workspaceId));
const selectedConversationId = ref("");
const question = ref("");
const questionMode = ref<ResearchQuestionMode>("fast");
const accountMenuOpen = ref(false);
const sidebarCollapsed = ref(false);
// 窄屏下侧栏以抽屉呈现，避免会话历史因布局压缩而不可访问。
const mobileSidebarOpen = ref(false);
const deleteConfirmId = ref<string | null>(null);
const researchScopeOpen = ref(false);
const selectedScopeDocumentId = ref<string | null>(null);
const sourceOpenByMessageId = ref<Record<string, boolean>>({});
const highlightedEvidence = ref<{ runId: string; evidenceId: string } | null>(null);
const unavailableDocumentEvidenceId = ref<string | null>(null);
const questionInput = ref<HTMLTextAreaElement | null>(null);
const researchQueries = useResearchQueries(workspaceId, selectedConversationId);
const {
  workspaceQuery,
  documentsQuery,
  conversationsQuery,
  conversationQuery,
  createConversationMutation,
  askQuestionMutation,
  retryRunMutation,
  cancelRunMutation,
  deleteConversationMutation,
  refreshConversations,
  refreshConversation: refreshResearchQueries,
} = researchQueries;
const operationError = ref<string | null>(null);
const researchProgress = useResearchProgress(workspaceId, selectedConversationId, {
  onRefresh: async () => refreshConversation(),
  onError: (message) => {
    if (message) operationError.value = message;
  },
});
const { activeRun, progressEvent, progressHistoryByRun, streamRun, clearProgressHistory } =
  researchProgress;

const readyCount = computed(
  () => documentsQuery.data.value?.summary.researchable_document_count ?? 0,
);
const conversations = computed(() => conversationsQuery.data.value ?? []);
const currentConversation = computed(
  () => conversations.value.find((item) => item.id === selectedConversationId.value) ?? null,
);
const conversationDetail = computed(() => conversationQuery.data.value);
const pendingRun = computed(() => {
  if (activeRun.value?.output_message_id === null) return activeRun.value;
  const latestUserMessage = [...(conversationDetail.value?.messages ?? [])]
    .reverse()
    .find((message) => message.role === "user");
  if (!latestUserMessage) return null;
  return (
    [...(conversationDetail.value?.runs ?? [])]
      .reverse()
      .find(
        (run) => run.input_message_id === latestUserMessage.id && run.output_message_id === null,
      ) ?? null
  );
});
const composerDisabled = computed(
  () => !readyCount.value || ["queued", "running"].includes(pendingRun.value?.status ?? ""),
);
const accountInitial = computed(() => (auth.user?.display_name ?? "研").slice(0, 1).toUpperCase());

const isTerminal = isResearchRunTerminal;

function runForOutputMessage(messageId: string) {
  return researchRunForOutputMessage(conversationDetail.value?.runs ?? [], messageId);
}

function sourcesOpen(messageId: string): boolean {
  return sourceOpenByMessageId.value[messageId] ?? false;
}

function setSourcesOpen(messageId: string, open: boolean): void {
  sourceOpenByMessageId.value = { ...sourceOpenByMessageId.value, [messageId]: open };
}

function isEvidenceHighlighted(run: ResearchRun, evidenceId: string): boolean {
  return (
    highlightedEvidence.value?.runId === run.id &&
    highlightedEvidence.value.evidenceId === evidenceId
  );
}

async function inspectCitation(messageId: string, citationIndex: number): Promise<void> {
  const run = runForOutputMessage(messageId);
  const evidence = citedEvidences(run).find((item) => item.display_index === citationIndex);
  if (!run || !evidence) return;

  unavailableDocumentEvidenceId.value = null;
  setSourcesOpen(messageId, true);
  highlightedEvidence.value = { runId: run.id, evidenceId: evidence.id };
  await nextTick();

  const target = document.getElementById(evidenceElementId(run.id, evidence.id));
  if (!(target instanceof HTMLElement)) return;
  target.scrollIntoView({ behavior: "smooth", block: "nearest" });
  target.focus({ preventScroll: true });
}

function openEvidenceDocument(evidence: ResearchEvidence): void {
  const document = (documentsQuery.data.value?.documents ?? []).find(
    (item) => item.paper_id === evidence.paper_id,
  );
  if (!document) {
    unavailableDocumentEvidenceId.value = evidence.id;
    return;
  }
  unavailableDocumentEvidenceId.value = null;
  selectedScopeDocumentId.value = document.document_id;
  researchScopeOpen.value = true;
}

function prepareDeepResearchRetry(run: ResearchRun): void {
  const originalQuestion = conversationDetail.value?.messages.find(
    (message) => message.id === run.input_message_id,
  )?.content;
  if (originalQuestion) question.value = originalQuestion;
  questionMode.value = "strict";
  operationError.value = null;
  void nextTick(() => questionInput.value?.focus());
}

async function refreshConversation(): Promise<void> {
  await refreshResearchQueries();
  if (activeRun.value && isTerminal(activeRun.value.status)) activeRun.value = null;
}

async function chooseConversation(conversationId: string): Promise<void> {
  if (selectedConversationId.value === conversationId) return;
  researchProgress.reset();
  selectedConversationId.value = conversationId;
  mobileSidebarOpen.value = false;
  sourceOpenByMessageId.value = {};
  highlightedEvidence.value = null;
  unavailableDocumentEvidenceId.value = null;
  selectedScopeDocumentId.value = null;
  await router.replace({ query: { conversation: conversationId } });
}

async function createAndSelectConversation(): Promise<string> {
  const conversation = await createConversationMutation.mutateAsync();
  await refreshConversations();
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
    const result = await askQuestionMutation.mutateAsync({
      conversationId,
      content,
      mode: questionMode.value,
    });
    question.value = "";
    activeRun.value = result.research_run;
    progressEvent.value = null;
    clearProgressHistory(result.research_run.id);
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
    clearProgressHistory(retried.id);
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
    const cancelled = await cancelRunMutation.mutateAsync({
      conversationId: selectedConversationId.value,
      runId: run.id,
    });
    activeRun.value = cancelled;
    if (cancelled.status === "cancelled") researchProgress.stop();
    await refreshConversation();
  } catch (error) {
    operationError.value = error instanceof Error ? error.message : "取消请求暂时无法提交。";
  }
}

async function confirmDeleteConversation(): Promise<void> {
  const conversationId = deleteConfirmId.value;
  if (!conversationId) return;
  operationError.value = null;
  try {
    await deleteConversationMutation.mutateAsync(conversationId);
    deleteConfirmId.value = null;
    researchProgress.reset();
    selectedConversationId.value = "";
    await refreshConversations();
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

watch(researchScopeOpen, (open) => {
  if (!open) selectedScopeDocumentId.value = null;
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
    <ConversationSidebar
      v-model:collapsed="sidebarCollapsed"
      :workspace-name="workspaceQuery.data.value?.name ?? '正在读取工作区…'"
      :ready-count="readyCount"
      :conversations="conversations"
      :selected-conversation-id="selectedConversationId"
      :loading="conversationsQuery.isPending.value"
      @choose="chooseConversation"
      @create="newConversation"
      @delete="deleteConfirmId = $event"
    />

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
          <button
            class="icon-button research-chat-header-scope"
            type="button"
            aria-label="打开研究范围"
            title="打开研究范围"
            @click="researchScopeOpen = true"
          >
            <FileStack :size="17" />
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
                      rerankerDisabled(runForOutputMessage(message.id))
                    "
                    >未启用模型重排</span
                  >
                  <span v-if="message.status !== 'completed'">处理中</span>
                </div>
                <section
                  v-if="
                    message.role === 'assistant' &&
                    isEvidenceInsufficientRun(runForOutputMessage(message.id))
                  "
                  class="research-answer-insufficient"
                  role="status"
                >
                  <CircleAlert :size="17" />
                  <div>
                    <strong>当前集合的证据不足</strong>
                    <p>这次问题无法由已保存的原文片段可靠支持，请补充研究范围或改用深度研究。</p>
                  </div>
                </section>
                <ResearchAnswerMarkdown
                  v-else-if="message.role === 'assistant'"
                  :content="message.content"
                  :citation-indexes="citedEvidenceIndexes(runForOutputMessage(message.id))"
                  @inspect-citation="inspectCitation(message.id, $event)"
                />
                <p v-else class="research-chat-message-body">{{ message.content }}</p>
                <ResearchRunAudit
                  v-if="
                    message.role === 'assistant' &&
                    runForOutputMessage(message.id) &&
                    !isEvidenceInsufficientRun(runForOutputMessage(message.id))
                  "
                  :run="runForOutputMessage(message.id)!"
                  :progress-history="
                    progressHistoryByRun[runForOutputMessage(message.id)!.id] ?? []
                  "
                />
                <ResearchEvidencePanel
                  v-if="
                    message.role === 'assistant' &&
                    runForOutputMessage(message.id) &&
                    !isEvidenceInsufficientRun(runForOutputMessage(message.id))
                  "
                  :run="runForOutputMessage(message.id)!"
                  :sources-open="sourcesOpen(message.id)"
                  :highlighted-evidence-id="
                    isEvidenceHighlighted(
                      runForOutputMessage(message.id)!,
                      highlightedEvidence?.evidenceId ?? '',
                    )
                      ? highlightedEvidence?.evidenceId
                      : null
                  "
                  :unavailable-document-evidence-id="unavailableDocumentEvidenceId"
                  @update:sources-open="setSourcesOpen(message.id, $event)"
                  @open-document="openEvidenceDocument"
                />
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
                <strong>{{
                  isEvidenceInsufficientRun(pendingRun)
                    ? "当前集合的证据不足"
                    : cancellationRequested(pendingRun)
                      ? "已请求停止，正在等待当前调用返回。"
                      : (progressEvent?.message ?? pendingRun.stage_display.label)
                }}</strong>
                <p>
                  {{
                    isEvidenceInsufficientRun(pendingRun)
                      ? "这次问题没有得到可引用的原文支持。可补充研究范围，或将原问题带入深度研究继续探索。"
                      : cancellationRequested(pendingRun)
                        ? "任务会在当前模型或检索调用结束后的安全边界停止，不会生成回答或新的引用证据。"
                        : (pendingRun.error_message ?? pendingRun.stage_display.description)
                  }}
                </p>
                <small v-if="governanceSummary(pendingRun)">{{
                  governanceSummary(pendingRun)
                }}</small>
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
                <div
                  v-else-if="isEvidenceInsufficientRun(pendingRun)"
                  class="research-chat-run-actions"
                >
                  <button
                    class="secondary-button"
                    type="button"
                    @click="prepareDeepResearchRetry(pendingRun)"
                  >
                    <ShieldCheck :size="15" />切换为深度研究
                  </button>
                </div>
                <div
                  v-else-if="
                    pendingRun.status === 'queued' ||
                    (pendingRun.status === 'running' && !cancellationRequested(pendingRun))
                  "
                  class="research-chat-run-actions"
                >
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="cancelRunMutation.isPending.value"
                    @click="cancelCurrentRun"
                  >
                    <X :size="15" />{{ pendingRun.status === "running" ? "请求停止" : "取消任务" }}
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
        <div class="research-chat-mode-selector" role="radiogroup" aria-label="研究模式">
          <button
            class="research-chat-mode-option"
            :class="{ active: questionMode === 'fast' }"
            type="button"
            role="radio"
            :aria-checked="questionMode === 'fast'"
            :disabled="composerDisabled"
            title="快速问答"
            @click="questionMode = 'fast'"
          >
            <Sparkles :size="14" /><span>快速问答</span>
          </button>
          <button
            class="research-chat-mode-option"
            :class="{ active: questionMode === 'strict' }"
            type="button"
            role="radio"
            :aria-checked="questionMode === 'strict'"
            :disabled="composerDisabled"
            title="深度研究"
            @click="questionMode = 'strict'"
          >
            <ShieldCheck :size="14" /><span>深度研究</span>
          </button>
        </div>
        <form class="research-chat-composer" @submit.prevent="handleSubmit">
          <textarea
            ref="questionInput"
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

    <ResearchScopeDrawer
      v-model:open="researchScopeOpen"
      :documents="documentsQuery.data.value?.documents ?? []"
      :loading="documentsQuery.isPending.value"
      :error="documentsQuery.isError.value"
      :selected-document-id="selectedScopeDocumentId"
    />

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
