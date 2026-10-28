import * as Sentry from "@sentry/nextjs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST as createRun } from "@/app/api/runs/route";
import { GET as getRun } from "@/app/api/runs/[id]/route";
import { GET as getVenue } from "@/app/api/venues/[id]/route";
import { AGENT_URL, ctx, fakeAgent, jsonRequest } from "./test-agent";
import {
  RUN_ID_HEADER,
  UNAVAILABLE_REPORT_INTERVAL_MS,
  newRunId,
  redactText,
  resetUnavailableReports,
  runIdFor,
  scrubEvent,
  sentryOptions,
  shouldReportUnavailable,
} from "./observability";

// The run route's plan check (T48) would read the session from the agent first: a request
// of its own, with its own id, and not what this file is about.
vi.mock("@/src/server/session", () => ({ checkPlanRun: vi.fn(async () => null) }));

const scope = vi.hoisted(() => ({ setTag: vi.fn() }));
const isolationScope = vi.hoisted(() => ({ setTag: vi.fn() }));
vi.mock("@sentry/nextjs", async (original) => ({
  ...(await original<typeof import("@sentry/nextjs")>()),
  captureException: vi.fn(),
  withScope: vi.fn((cb: (s: typeof scope) => unknown) => cb(scope)),
  getIsolationScope: vi.fn(() => isolationScope),
}));

const VENUE = "The Synthetic Room";
const MESSAGE = "more synthetic jazz on thursdays";
const KEY = "test-internal-key";
const constraints = {
  include_tags: [],
  exclude_tags: [],
  size_tier: "mid",
  exclude_recent_months: 6,
  max_candidates: 10,
};

/** The X-Draw-Run-Id of each request the fake agent saw. */
function sentRunIds(): (string | null)[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([, init]) => new Headers((init as RequestInit).headers).get(RUN_ID_HEADER));
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(Sentry.captureException).mockClear();
  scope.setTag.mockClear();
  isolationScope.setTag.mockClear();
  resetUnavailableReports();
});

describe("run ids", () => {
  it("match the agent's id patterns", () => {
    // agent/draw/observability.py: NEW_RUN_ID_PATTERN and RUN_ID_PATTERN.
    expect(newRunId("run")).toMatch(/^run_[0-9a-f]{8,32}$/);
    expect(newRunId("req")).toMatch(/^req_[a-z0-9_-]{4,64}$/);
    expect(newRunId("run")).not.toBe(newRunId("run"));
  });

  it("come from the path for run routes", () => {
    expect(runIdFor("/runs/run_0123abcd/candidates/e1/brief")).toBe("run_0123abcd");
    expect(runIdFor("/runs/run_seed_dive-bar")).toBe("run_seed_dive-bar");
    expect(runIdFor("/venues/ven_1")).toMatch(/^req_/);
  });

  it("go to the agent on every request and into the log line", async () => {
    fakeAgent(
      Response.json({}, { headers: { [RUN_ID_HEADER]: "run_fromagent1" } }),
      Response.json({}),
      Response.json({}),
    );
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    await createRun(jsonRequest({ venue_id: "ven_1", constraints }));
    await getRun(new Request("http://web.test"), ctx({ id: "run_0123abcd" }));
    await getVenue(new Request("http://web.test"), ctx({ id: "ven_1" }));

    const [created, read, venue] = sentRunIds();
    expect(created).toMatch(/^run_[0-9a-f]{12}$/);
    expect(read).toBe("run_0123abcd");
    expect(venue).toMatch(/^req_[0-9a-f]{12}$/);
    // The request's Sentry scope, for errors thrown past proxy() (a page rendering).
    expect(isolationScope.setTag.mock.calls).toEqual([
      ["run_id", created],
      ["run_id", "run_fromagent1"],
      ["run_id", "run_0123abcd"],
      ["run_id", venue],
    ]);
    const lines = info.mock.calls.map(([line]) => String(line));
    // The id the agent answered with: it names a new run itself when ours was taken.
    expect(lines[0]).toMatch(new RegExp(`^agent POST /runs → 200 \\d+ms run_id=run_fromagent1$`));
    expect(lines[1]).toContain("run_id=run_0123abcd");
    expect(lines[2]).toContain(`run_id=${venue}`);
  });

  it("names the run in the error line and the Sentry tag when the agent fails", async () => {
    fakeAgent(new Response("boom", { status: 500 }));
    const error = vi.mocked(console.error);

    const res = await getRun(new Request("http://web.test"), ctx({ id: "run_0123abcd" }));

    expect(res.status).toBe(502);
    expect(String(error.mock.calls[0]?.[0])).toMatch(
      /^agent GET \/runs\/run_0123abcd → 500 \d+ms run_id=run_0123abcd$/,
    );
    expect(scope.setTag).toHaveBeenCalledExactlyOnceWith("run_id", "run_0123abcd");
    expect(Sentry.captureException).toHaveBeenCalledOnce();
  });

  it("reports an agent outage to Sentry once a minute, not per request", async () => {
    fakeAgent(new TypeError("fetch failed"), new TypeError("fetch failed"));

    const first = await getRun(new Request("http://web.test"), ctx({ id: "run_0123abcd" }));
    const second = await getRun(new Request("http://web.test"), ctx({ id: "run_0123abcd" }));

    expect([first.status, second.status]).toEqual([503, 503]);
    expect(Sentry.captureException).toHaveBeenCalledOnce();
  });

  it("does not report the user's own errors to Sentry", async () => {
    fakeAgent(Response.json({ detail: "nope" }, { status: 404 }));

    const res = await getRun(new Request("http://web.test"), ctx({ id: "run_0123abcd" }));

    expect(res.status).toBe(404);
    expect(Sentry.captureException).not.toHaveBeenCalled();
  });
});

describe("shouldReportUnavailable", () => {
  it("passes one report per interval", () => {
    expect(shouldReportUnavailable(1_000)).toBe(true);
    expect(shouldReportUnavailable(1_000 + UNAVAILABLE_REPORT_INTERVAL_MS - 1)).toBe(false);
    expect(shouldReportUnavailable(1_000 + UNAVAILABLE_REPORT_INTERVAL_MS)).toBe(true);
  });
});

describe("scrubEvent", () => {
  it("leaves no venue name, message or key in the event", () => {
    const event: Sentry.ErrorEvent = {
      type: undefined,
      message: `venue ${JSON.stringify(VENUE)}`,
      user: { ip_address: "203.0.113.1" },
      request: {
        method: "POST",
        url: `${AGENT_URL}/api/venues?name=${encodeURIComponent(VENUE)}`,
        data: { name: VENUE, message: MESSAGE },
        cookies: { session: "s" },
        headers: { "x-draw-internal-key": KEY, [RUN_ID_HEADER]: "run_0123abcd", "user-agent": "x" },
      },
      extra: { venue: { name: VENUE }, steps: 3 },
      contexts: { runtime: { name: "node" }, run: { message: MESSAGE } },
      breadcrumbs: [{ category: "console", message: `refine '${MESSAGE}'`, data: { name: VENUE } }],
      exception: {
        values: [
          { value: `no act fits '${VENUE}'`, stacktrace: { frames: [{ vars: { name: VENUE } }] } },
        ],
      },
    };

    const out = scrubEvent(event);

    const dumped = JSON.stringify(out);
    for (const secret of [VENUE, MESSAGE, KEY, "Synthetic"]) expect(dumped).not.toContain(secret);
    expect(out.request).toEqual({
      method: "POST",
      url: `${AGENT_URL}/api/venues`,
      headers: { [RUN_ID_HEADER]: "run_0123abcd" },
    });
    expect(out.contexts?.runtime?.name).toBe("node");
    expect(out.extra?.steps).toBe(3);
    expect(out.user).toBeUndefined();
  });

  it("redacts quoted text only", () => {
    expect(redactText(`Unknown venue '${VENUE}'.`)).toBe("Unknown venue '[redacted]'.");
    expect(redactText("no quotes, kept")).toBe("no quotes, kept");
  });
});

describe("sentryOptions", () => {
  it("is off without SENTRY_DSN", () => {
    expect(sentryOptions({ NODE_ENV: "test" })).toBeNull();
  });

  it("scrubs every event and sends no PII or traces", () => {
    const dsn = "https://k@o0.ingest.sentry.io/0";
    const options = sentryOptions({ NODE_ENV: "test", SENTRY_DSN: dsn });
    expect(options?.beforeSend).toBe(scrubEvent);
    expect(options?.tracesSampleRate).toBe(0);
    expect(options?.dataCollection).toMatchObject({
      userInfo: false,
      httpBodies: [],
      cookies: false,
    });
  });
});
