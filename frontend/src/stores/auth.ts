import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { getCurrentUser, login, register } from "@/api/auth";
import { ApiError, clearAccessToken, getAccessToken, setAccessToken } from "@/api/client";
import type { User } from "@/api/types";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const initialized = ref(false);
  const busy = ref(false);
  const errorMessage = ref<string | null>(null);

  const isAuthenticated = computed(() => Boolean(user.value && getAccessToken()));

  async function restore(): Promise<void> {
    if (initialized.value) return;
    if (!getAccessToken()) {
      initialized.value = true;
      return;
    }
    try {
      user.value = await getCurrentUser();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) clear();
      else errorMessage.value = "无法恢复登录状态，请检查 API 服务。";
    } finally {
      initialized.value = true;
    }
  }

  async function signIn(email: string, password: string): Promise<void> {
    busy.value = true;
    errorMessage.value = null;
    try {
      const response = await login(email, password);
      setAccessToken(response.access_token);
      user.value = response.user;
      initialized.value = true;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "登录失败，请稍后重试。";
      throw error;
    } finally {
      busy.value = false;
    }
  }

  async function signUp(email: string, password: string, displayName: string): Promise<void> {
    busy.value = true;
    errorMessage.value = null;
    try {
      const response = await register(email, password, displayName);
      setAccessToken(response.access_token);
      user.value = response.user;
      initialized.value = true;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "注册失败，请稍后重试。";
      throw error;
    } finally {
      busy.value = false;
    }
  }

  function clear(): void {
    clearAccessToken();
    user.value = null;
  }

  return { user, initialized, busy, errorMessage, isAuthenticated, restore, signIn, signUp, clear };
});
