import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

// Building the profile spends up to 6 Qloo calls; keep above SLOW_TIMEOUT_MS.
export const maxDuration = 120;

/** Build the crowd profile; 409 {reason:"conflict", pending} while any match is unconfirmed. */
export async function POST(
  _req: Request,
  ctx: RouteContext<"/api/venues/[id]/profile">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.buildProfile(id(params.id, "id")));
}
