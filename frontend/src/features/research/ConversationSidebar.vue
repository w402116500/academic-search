<script setup lang="ts">
import {
  ArrowLeft,
  LoaderCircle,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
} from "@lucide/vue";
import { RouterLink } from "vue-router";

import type { Conversation } from "@/api/types";

defineProps<{
  workspaceName: string;
  readyCount: number;
  conversations: Conversation[];
  selectedConversationId: string;
  loading: boolean;
}>();

const sidebarCollapsed = defineModel<boolean>("collapsed", { required: true });

const emit = defineEmits<{
  choose: [conversationId: string];
  create: [];
  delete: [conversationId: string];
}>();

function conversationTitle(conversation: Conversation): string {
  return conversation.title?.trim() || "新建研究对话";
}

function conversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
}
</script>

<template>
  <aside id="research-chat-sidebar" class="research-chat-sidebar" aria-label="当前工作区的研究会话">
    <RouterLink class="research-chat-brand" to="/" aria-label="返回研究入口">
      <span class="brand-mark">AS</span
      ><span class="research-chat-brand-copy">Academic Search</span>
    </RouterLink>
    <div class="research-chat-sidebar-context">
      <span class="eyebrow">当前工作区</span>
      <strong>{{ workspaceName }}</strong>
      <small>{{ readyCount }} 篇全文可问答</small>
    </div>
    <div class="research-chat-sidebar-actions">
      <button
        class="primary-button research-chat-new-button"
        type="button"
        aria-label="新建研究对话"
        @click="emit('create')"
      >
        <Plus :size="16" /><span>新建研究对话</span>
      </button>
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
          @click="emit('choose', conversation.id)"
        >
          <MessageSquareText :size="16" />
          <span class="research-chat-session-copy">
            <strong>{{ conversationTitle(conversation) }}</strong>
            <small
              >{{
                conversation.message_count ? conversation.message_count + " 条消息" : "尚未提问"
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
          @click="emit('delete', conversation.id)"
        >
          <Trash2 :size="14" />
        </button>
      </div>
      <div v-if="loading" class="research-chat-sidebar-empty">
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
</template>
