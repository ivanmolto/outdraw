// Next.js Proxy (formerly Middleware): per-IP rate limits on the agent-backed /api routes.
// Seed pages (/v/*) and everything else are outside the matcher and never limited.
import { NextResponse, type NextRequest } from "next/server";

import { checkRateLimit } from "@/src/server/ratelimit";

export async function proxy(req: NextRequest): Promise<Response> {
  return (await checkRateLimit(req)) ?? NextResponse.next();
}

export const config = {
  matcher: ["/api/runs/:path*", "/api/venues/:path*"],
};
