import { createRouter, createWebHistory } from "vue-router";

import { pinia } from "@/stores/pinia";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/AuthView.vue"),
      meta: { auth: false },
    },
    {
      path: "/register",
      name: "register",
      component: () => import("@/views/AuthView.vue"),
      meta: { auth: false },
    },
    {
      path: "/",
      name: "research-entry",
      component: () => import("@/views/ResearchEntryView.vue"),
      meta: { auth: true },
    },
    {
      // 工作区创建后保留输入驱动的连续画布，只在 URL 中补充可恢复的工作区标识。
      path: "/workspace/:workspaceId/run",
      name: "workspace-runner",
      component: () => import("@/views/ResearchRunnerView.vue"),
      meta: { auth: true },
    },
    {
      // 证据研究是独立的对话工作台，不与工作区的连续阶段侧栏共用布局。
      path: "/research/:workspaceId",
      name: "workspace-research-chat",
      component: () => import("@/views/ResearchChatView.vue"),
      meta: { auth: true },
    },
    {
      path: "/workspace/:workspaceId",
      component: () => import("@/views/WorkspaceFrame.vue"),
      meta: { auth: true },
      children: [
        {
          path: "plan",
          redirect: (to) => ({
            name: "workspace-runner",
            params: { workspaceId: to.params.workspaceId },
          }),
        },
        {
          path: "search",
          redirect: (to) => ({
            name: "workspace-runner",
            params: { workspaceId: to.params.workspaceId },
            query: to.query,
          }),
        },
        {
          path: "results",
          name: "workspace-results",
          component: () => import("@/views/ResultsView.vue"),
        },
        {
          // 批量核验是独立的长任务画布，避免把逐篇任务状态挤在候选审核表中。
          path: "verification",
          name: "workspace-verification",
          component: () => import("@/views/VerificationTaskView.vue"),
        },
        {
          path: "paper/:candidateId",
          name: "paper-detail",
          component: () => import("@/views/PaperDetailView.vue"),
        },
        {
          path: "collection",
          name: "workspace-collection",
          component: () => import("@/views/CollectionView.vue"),
        },
      ],
    },
  ],
});

router.beforeEach(async (to) => {
  const auth = useAuthStore(pinia);
  await auth.restore();
  if (to.meta.auth === true && !auth.isAuthenticated)
    return { name: "login", query: { redirect: to.fullPath } };
  if (to.meta.auth === false && auth.isAuthenticated) return { name: "research-entry" };
  return true;
});

export default router;
