import { defineConfig } from "@playwright/test";

const testPort = process.env.PLAYWRIGHT_VITE_PORT ?? "5173";
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${testPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${testPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
});
