<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { ArrowRight, KeyRound, ShieldCheck } from "@lucide/vue";

import AppHeader from "@/components/AppHeader.vue";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const isRegister = computed(() => route.name === "register");
const email = ref("");
const password = ref("");
const displayName = ref("");
const localError = ref<string | null>(null);

async function submit(): Promise<void> {
  localError.value = null;
  if (isRegister.value && displayName.value.trim().length < 1) {
    localError.value = "请填写你的显示名称。";
    return;
  }
  try {
    if (isRegister.value) await auth.signUp(email.value, password.value, displayName.value);
    else await auth.signIn(email.value, password.value);
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";
    await router.push(redirect);
  } catch (error) {
    localError.value = error instanceof Error ? error.message : "操作失败，请稍后重试。";
  }
}
</script>

<template>
  <div class="auth-page">
    <AppHeader compact />
    <main class="auth-main">
      <section class="auth-card">
        <div class="eyebrow">ACADEMIC SEARCH / ACCOUNT</div>
        <div class="auth-title-row">
          <span class="auth-icon"><KeyRound :size="19" /></span>
          <h1>{{ isRegister ? "创建研究账号" : "回到研究台" }}</h1>
        </div>
        <p class="auth-intro">
          {{
            isRegister
              ? "保存你的工作区、文献集合和后续研究记录。"
              : "继续上次的研究任务，状态会从服务端恢复。"
          }}
        </p>
        <form class="form-stack" @submit.prevent="submit">
          <label v-if="isRegister" class="field"
            ><span>显示名称</span
            ><input v-model="displayName" autocomplete="name" placeholder="例如：林同学"
          /></label>
          <label class="field"
            ><span>邮箱</span
            ><input
              v-model="email"
              type="email"
              autocomplete="email"
              placeholder="name@example.com"
              required
          /></label>
          <label class="field"
            ><span>密码</span
            ><input
              v-model="password"
              type="password"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              :minlength="isRegister ? 12 : 1"
              placeholder="至少 12 位"
              required
          /></label>
          <p v-if="localError || auth.errorMessage" class="form-error" role="alert">
            {{ localError || auth.errorMessage }}
          </p>
          <button class="primary-button" type="submit" :disabled="auth.busy">
            <span>{{ auth.busy ? "正在连接…" : isRegister ? "创建账号" : "登录" }}</span
            ><ArrowRight :size="17" />
          </button>
        </form>
        <div class="auth-note">
          <ShieldCheck :size="15" /><span>仅使用于本地开发与面试演示，暂不接入付费功能。</span>
        </div>
        <p class="switch-auth">
          {{ isRegister ? "已经有账号？" : "还没有账号？"
          }}<RouterLink :to="{ name: isRegister ? 'login' : 'register', query: route.query }">{{
            isRegister ? "登录" : "注册"
          }}</RouterLink>
        </p>
      </section>
    </main>
  </div>
</template>
