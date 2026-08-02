import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import { createApp } from "vue";

import App from "@/App.vue";
import router from "@/router";
import { pinia } from "@/stores/pinia";
import "@/styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 },
  },
});

createApp(App).use(pinia).use(router).use(VueQueryPlugin, { queryClient }).mount("#app");
