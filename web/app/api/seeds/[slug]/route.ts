import type { SeedSample } from "@/src/lib/api-client";
import { isSeedSlug } from "@/src/lib/seeds";
import { crossDomain } from "@/src/lib/soundcheck";
import { AgentError } from "@/src/server/agent";
import { fail, proxy } from "@/src/server/proxy";
import { seedPage } from "@/src/server/seeds";

/** A seed's precomputed shortlist, for the sample the shortlist pages show when live
 * analysis is refused (busy, budget, live_mode_off). Read from the same shared cache as
 * /v/[slug], so it answers even while the agent is refusing runs, and spends nothing:
 * outside proxy.ts's matcher, never rate-limited. 404 for a slug that is no seed, 409 for a
 * seed not precomputed yet. */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/seeds/[slug]">,
): Promise<Response> {
  const { slug } = await ctx.params;
  if (!isSeedSlug(slug)) return fail(404, { reason: "not_found" });
  return proxy(async (): Promise<SeedSample> => {
    const page = await seedPage(slug);
    if (page.state === "missing") throw new AgentError(404, "not_found");
    if (page.state === "not_ready") throw new AgentError(409, "conflict");
    return {
      slug,
      title: page.seed.title,
      venueId: page.venueId,
      venueName: page.seed.venueName,
      run: page.run,
      precomputed: page.precomputed,
      crossDomain: crossDomain(page.profile),
    };
  });
}
