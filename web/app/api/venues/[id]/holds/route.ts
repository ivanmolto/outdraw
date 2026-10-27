import { agent } from "@/src/server/agent";
import { id, proxy } from "@/src/server/proxy";

/** The venue's pencil holds across every run, for the shortlist's calendar. A plain read of
 * our own rows: no Qloo or model call, and a GET, so outside the rate limits. */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/venues/[id]/holds">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(() => agent.getHolds(id(params.id, "id")));
}
