// End-to-end tests against a production build (`next build && next start`, as Vercel runs
// it) with the agent replaced by e2e/mock-agent.mjs and Upstash by e2e/mock-redis.mjs, so
// no Qloo, Claude or Redis call is ever made. Run with `npm run test:e2e`.
import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = 3100;
/** The same build served with HACKATHON_MODE=false, for the free-plan spec. */
const FREE_PORT = 3102;
const AGENT_PORT = 8787;
const REDIS_PORT = 8788;
const INTERNAL_API_KEY = "e2e-internal-key";
const UPSTASH_REDIS_REST_TOKEN = "e2e-redis-token";

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
  expect: {
    // Anti-aliasing drifts between Chromium builds; a real change moves far more than this.
    toHaveScreenshot: { maxDiffPixelRatio: 0.01 },
  },
  projects: [
    {
      name: "mobile",
      use: { ...devices["Pixel 7"] },
      testIgnore: ["visual.spec.ts", "plan-free.spec.ts", "plan-limit.spec.ts"],
    },
    // Hackathon mode off (T46, T48): the free plan's gates and its daily run allowance,
    // against the second server.
    {
      name: "free",
      testMatch: ["plan-free.spec.ts", "plan-limit.spec.ts"],
      use: { ...devices["Pixel 7"], baseURL: `http://localhost:${FREE_PORT}` },
    },
    // Visual snapshots and the axe check (T43), at scale 1 so a snapshot is CSS pixels.
    {
      name: "visual",
      testMatch: "visual.spec.ts",
      use: { ...devices["Desktop Chrome"], deviceScaleFactor: 1 },
    },
  ],
  webServer: [
    {
      command: "node e2e/mock-agent.mjs",
      url: `http://127.0.0.1:${AGENT_PORT}/health`,
      env: { MOCK_AGENT_PORT: String(AGENT_PORT), INTERNAL_API_KEY },
      reuseExistingServer: false,
    },
    {
      command: "node e2e/mock-redis.mjs",
      url: `http://127.0.0.1:${REDIS_PORT}/health`,
      env: { MOCK_REDIS_PORT: String(REDIS_PORT), UPSTASH_REDIS_REST_TOKEN },
      reuseExistingServer: false,
    },
    {
      command: `npx next build && npx next start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/api/health`,
      env: {
        AGENT_URL: `http://127.0.0.1:${AGENT_PORT}`,
        INTERNAL_API_KEY,
        UPSTASH_REDIS_REST_URL: `http://127.0.0.1:${REDIS_PORT}`,
        UPSTASH_REDIS_REST_TOKEN,
        // As deployed for judging (T46): every session Pro, the banner on.
        HACKATHON_MODE: "true",
      },
      reuseExistingServer: false,
      timeout: 240_000,
    },
    {
      // Started after the build above (servers start in order), from the same .next.
      command: `npx next start --port ${FREE_PORT}`,
      url: `http://localhost:${FREE_PORT}/api/health`,
      env: {
        AGENT_URL: `http://127.0.0.1:${AGENT_PORT}`,
        INTERNAL_API_KEY,
        UPSTASH_REDIS_REST_URL: `http://127.0.0.1:${REDIS_PORT}`,
        UPSTASH_REDIS_REST_TOKEN,
        HACKATHON_MODE: "false",
      },
      reuseExistingServer: false,
    },
  ],
});
