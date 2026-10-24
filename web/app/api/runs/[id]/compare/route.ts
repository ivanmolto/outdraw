import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

// The first request per run waits on one model call; keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** The run's LLM-only shortlist (labelled `llm_only`), for the With Qloo / LLM only toggle. */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/runs/[id]/compare">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.getCompare(id(params.id, "id")));
}
