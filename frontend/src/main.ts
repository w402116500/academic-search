import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import { createApp } from "vue";

import App from "@/App.vue";
import router from "@/router";
import { pinia } from "@/stores/pinia";
import "@/styles.css";
import "@/features/research/research-chat.css";
import "@/features/research/research-answer.css";
import "@/features/research/research-scope-drawer.css";
import "@/assets/styles/reset.css";
import "@/assets/styles/components.css";
import "@/features/research/research-entry.css";
import "@/features/research/research-runner.css";
import "@/features/auth/auth.css";
import "@/features/research/workspace.css";
import "@/features/research/plan-review.css";
import "@/assets/styles/feedback.css";
import "@/features/search/search-progress.css";
import "@/features/search/verification.css";
import "@/features/search/results.css";
import "@/features/literature/paper-detail.css";
import "@/features/research/collection.css";
import "@/assets/styles/overlays.css";
import "@/assets/styles/responsive.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 },
  },
});

createApp(App).use(pinia).use(router).use(VueQueryPlugin, { queryClient }).mount("#app");
