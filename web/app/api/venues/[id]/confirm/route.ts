import { confirmSchema } from "@/src/lib/schemas";
import { agent } from "@/src/server/agent";
import { body, id, proxy } from "@/src/server/proxy";

/** Record the user's pick for one resolution item, or `chosen_entity_id: null` to skip it. */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/venues/[id]/confirm">,
): Promise<Response> {
  const params = await ctx.params;
  return proxy(async () =>
    agent.confirmMatch(id(params.id, "id"), await body(req, confirmSchema)),
  );
}
