// The news pill at the top of the landing's hero while HACKATHON_MODE is on (T46): every
// session is Pro, and this says so, linking to the plans. <HackathonPill> reads the
// environment per request, after `connection()`, so the e2e suite serves both modes from
// one build (on Vercel a change still needs a redeploy); the hero renders it inside
// <Suspense> so the static shell stays static. <HackathonBanner> is the markup, for tests.
// On the stage (dark) ground, so the amber chip is allowed.
import Link from "next/link";
import { connection } from "next/server";

import { HACKATHON_BANNER, hackathonMode } from "@/src/lib/entitlements";

export function HackathonBanner({ on }: { on: boolean }) {
  if (!on) return null;
  return (
    <p role="note" className="mb-8">
      <Link
        href="/pricing"
        className="group inline-flex max-w-full items-center gap-3 rounded-full border border-house/20 bg-stage-2 py-1 pr-4 pl-1 text-sm text-house/90 hover:border-house/50"
      >
        <span className="shrink-0 rounded-full bg-brand-amber px-2.5 py-1 text-xs font-semibold text-stage">
          Hackathon
        </span>
        <span className="min-w-0 font-medium">{HACKATHON_BANNER}</span>
        <span aria-hidden="true" className="shrink-0 text-house/60 group-hover:text-house">
          ↗
        </span>
      </Link>
    </p>
  );
}

export async function HackathonPill() {
  await connection();
  return <HackathonBanner on={hackathonMode(process.env.HACKATHON_MODE)} />;
}
