import { refineSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, id, proxy } from "@/src/server/proxy";

// One parse call plus a whole agent run (≤ 8 tool steps); keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** A refined run chained to `id` by `parent_run_id`; invalid_input when the agent can read
 * no change from the message (its 422). */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/runs/[id]/refine">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(async () => agent.refineRun(id(params.id, "id"), await body(req, refineSchema)));
}
