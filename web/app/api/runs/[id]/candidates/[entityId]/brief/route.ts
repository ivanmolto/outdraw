import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";
import { peekRateLimit, spendRateLimit } from "@/src/server/ratelimit";

// The first request per act waits on one Qloo call and one model call (one retry); keep
// above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** The act's booking brief for the Soundcheck panel's Brief tab, cached by the agent.
 *
 * Rate-limited here, not in the proxy: a first brief spends agent calls and counts as a
 * run, a cached copy is a free read, and only the agent's answer says which. So: refuse
 * when the visitor has no token left, call the agent, take a token when it drafted. */
export async function GET(
  req: Request,
  ctx: RouteContext<"/api/runs/[id]/candidates/[entityId]/brief">,
): Promise<Response> {
  const params = await ctx.params;
  const limited = await peekRateLimit(req);
  if (limited) return limited;
  return proxy(async () => {
    const out = await agent.getBrief(id(params.id, "id"), id(params.entityId, "entityId"));
    if (!out.cached) await spendRateLimit(req);
    return out;
  });
}
