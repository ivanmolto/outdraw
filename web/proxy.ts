// Next.js Proxy (formerly Middleware): per-IP rate limits on the agent-backed /api routes,
// and a real 404 for a demo page that is not a seed. Everything else is outside the matcher.
import { NextResponse, type NextRequest } from "next/server";

import { isSeedSlug } from "@/src/lib/seeds";
import { checkRateLimit } from "@/src/server/ratelimit";

/** `/v/{slug}` for a slug that is no seed: the not-found page with a 404 status. The page
 * itself streams behind a prerendered shell, so a `notFound()` there would be a 200. Seed
 * pages are never rate-limited. */
function demoPage(req: NextRequest): Response {
  const slug = decodeURIComponent(req.nextUrl.pathname.slice("/v/".length));
  if (isSeedSlug(slug)) return NextResponse.next();
  return NextResponse.rewrite(new URL("/_not-found", req.url), { status: 404 });
}

export async function proxy(req: NextRequest): Promise<Response> {
  if (req.nextUrl.pathname.startsWith("/v/")) return demoPage(req);
  return (await checkRateLimit(req)) ?? NextResponse.next();
}

export const config = {
  matcher: ["/api/runs/:path*", "/api/venues/:path*", "/v/:slug"],
};
