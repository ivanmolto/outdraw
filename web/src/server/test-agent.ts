// Test helper: a fake agent behind `fetch`, recording what the proxy routes send it.
import { vi } from "vitest";

export const AGENT_URL = "http://agent.test";
export const KEY = "test-internal-key";

export interface Sent {
  method: string;
  url: string;
  key: string | null;
  body: unknown;
}

/** Stub env and `fetch`; each call answers with the next `replies` entry (or throws it). */
export function fakeAgent(...replies: (Response | Error)[]): Sent[] {
  vi.stubEnv("AGENT_URL", AGENT_URL + "/");
  vi.stubEnv("INTERNAL_API_KEY", KEY);
  vi.spyOn(console, "error").mockImplementation(() => {});
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      sent.push({
        method: init.method ?? "GET",
        url,
        key: new Headers(init.headers).get("x-draw-internal-key"),
        body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      });
      const reply = replies.shift() ?? Response.json({});
      if (reply instanceof Error) throw reply;
      return reply;
    }),
  );
  return sent;
}

export function jsonRequest(body: unknown): Request {
  return new Request("http://web.test/api", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

export const ctx = <P extends Record<string, string>>(params: P) => ({
  params: Promise.resolve(params),
});
