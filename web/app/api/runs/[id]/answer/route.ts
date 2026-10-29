import { answerSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, id, proxy } from "@/src/server/proxy";
import { checkPlanRun } from "@/src/server/session";

// A whole agent run (≤ 8 tool steps); keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** A run chained to the ask run `id` with the Qloo tag the booker chose for the word it
 * asked about; not_found when the choice was not offered, invalid_input when the run did
 * not ask about a tag (the agent's 422). A run on the plan's daily allowance like a fresh
 * one (T48). */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/runs/[id]/answer">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(async () => {
    const runId = id(params.id, "id");
    const input = await body(req, answerSchema);
    return (await checkPlanRun()) ?? agent.answerRun(runId, input);
  });
}
