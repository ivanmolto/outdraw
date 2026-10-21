// Test helper for the /venue/new components: a fake /api behind `fetch`, recording calls.
import { vi } from "vitest";

export interface Call {
  method: string;
  url: string;
  body: unknown;
}

type Reply = Response | ((call: Call) => Response | Promise<Response>);

/** Stub `fetch`; each call answers with the next reply whose path pattern matches. */
export function fakeApi(routes: [RegExp, Reply][]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call: Call = {
        method: init.method ?? "GET",
        url,
        body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      };
      calls.push(call);
      const i = routes.findIndex(([re]) => re.test(url));
      if (i === -1) throw new Error(`unexpected fetch ${url}`);
      const [, reply] = routes.splice(i, 1)[0];
      return typeof reply === "function" ? reply(call) : reply;
    }),
  );
  return calls;
}
