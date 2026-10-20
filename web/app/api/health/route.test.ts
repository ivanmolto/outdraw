import { describe, expect, it } from "vitest";

import pkg from "../../../package.json";
import { GET } from "./route";

describe("GET /api/health", () => {
  it("returns ok with the package version", async () => {
    const res = GET();

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({ ok: true, version: pkg.version });
  });
});
