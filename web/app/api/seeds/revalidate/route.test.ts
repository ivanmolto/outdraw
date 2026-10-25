// POST /api/seeds/revalidate: the internal key gates it, and it drops the seed tags.
import { afterEach, describe, expect, it, vi } from "vitest";

const revalidateTag = vi.fn();
vi.mock("next/cache", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/cache")>()),
  revalidateTag: (...args: unknown[]) => revalidateTag(...args),
}));

import { POST } from "./route";

const KEY = "test-internal-key";

function post(body: unknown, key: string | null = KEY): Request {
  return new Request("http://web.test/api/seeds/revalidate", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(key === null ? {} : { "x-draw-internal-key": key }),
    },
    body: JSON.stringify(body),
  });
}

afterEach(() => {
  vi.unstubAllEnvs();
  revalidateTag.mockClear();
});

describe("POST /api/seeds/revalidate", () => {
  it("drops the list and each named seed", async () => {
    vi.stubEnv("INTERNAL_API_KEY", KEY);
    const res = await POST(post({ slugs: ["nola-jazz", "brooklyn-indie"] }));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      revalidated: ["seeds", "seed:nola-jazz", "seed:brooklyn-indie"],
    });
    expect(revalidateTag.mock.calls).toEqual([
      ["seeds", "max"],
      ["seed:nola-jazz", "max"],
      ["seed:brooklyn-indie", "max"],
    ]);
  });

  it("drops only the list tag with no slugs (every page carries it)", async () => {
    vi.stubEnv("INTERNAL_API_KEY", KEY);
    const res = await POST(post({}));
    expect(await res.json()).toEqual({ revalidated: ["seeds"] });
  });

  it("is a 401 without the key, or with the key unset on the server", async () => {
    vi.stubEnv("INTERNAL_API_KEY", KEY);
    expect((await POST(post({}, "nope"))).status).toBe(401);
    expect((await POST(post({}, null))).status).toBe(401);
    vi.stubEnv("INTERNAL_API_KEY", "");
    expect((await POST(post({}))).status).toBe(401);
    expect(revalidateTag).not.toHaveBeenCalled();
  });

  it("rejects a malformed slug", async () => {
    vi.stubEnv("INTERNAL_API_KEY", KEY);
    expect((await POST(post({ slugs: ["Not A Slug"] }))).status).toBe(400);
    expect(revalidateTag).not.toHaveBeenCalled();
  });
});
