import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

// Resolving spends one Qloo call per unresolved act; keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

export async function POST(
  _req: Request,
  ctx: RouteContext<"/api/venues/[id]/resolve">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.resolveVenue(id(params.id, "id")));
}
