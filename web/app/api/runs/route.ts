import { runSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, proxy } from "@/src/server/proxy";
import { checkPlanRun } from "@/src/server/session";

// One synchronous agent run (≤ 8 tool steps); keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** A run, after the IP limits (proxy.ts) and the plan's daily allowance (T48): the body is
 * read first, so a bad request never spends a run. */
export async function POST(req: Request): Promise<Response> {
  return proxy(async () => {
    const input = await body(req, runSchema);
    return (await checkPlanRun()) ?? agent.createRun(input);
  });
}
