// End-to-end tests against a production build (`next build && next start`, as Vercel runs
// it) with the agent replaced by e2e/mock-agent.mjs, so no Qloo or Claude call is ever
// made. Run with `npm run test:e2e`.
import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = 3100;
const AGENT_PORT = 8787;
const INTERNAL_API_KEY = "e2e-internal-key";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "node e2e/mock-agent.mjs",
      url: `http://127.0.0.1:${AGENT_PORT}/health`,
      env: { MOCK_AGENT_PORT: String(AGENT_PORT), INTERNAL_API_KEY },
      reuseExistingServer: false,
    },
    {
      command: `npx next build && npx next start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/api/health`,
      env: { AGENT_URL: `http://127.0.0.1:${AGENT_PORT}`, INTERNAL_API_KEY },
      reuseExistingServer: false,
      timeout: 240_000,
    },
  ],
});
