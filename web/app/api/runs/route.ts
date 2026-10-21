import { runSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, proxy } from "@/src/server/proxy";

// One synchronous agent run (≤ 8 tool steps); keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

export async function POST(req: Request): Promise<Response> {
  return proxy(async () => agent.createRun(await body(req, runSchema)));
}
