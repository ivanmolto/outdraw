// The strip above the header while HACKATHON_MODE is on (T46): every session is Pro, and
// this says so. <HackathonStrip> reads the environment per request, after `connection()`,
// so the e2e suite serves both modes from one build (on Vercel a change still needs a
// redeploy); the layout renders it inside <Suspense> so the static shell of every page
// stays static. <HackathonBanner> is the markup, for tests.
import { connection } from "next/server";

import { HACKATHON_BANNER, hackathonMode } from "@/src/lib/entitlements";
import { ProBadge } from "./pro";

export function HackathonBanner({ on }: { on: boolean }) {
  if (!on) return null;
  return (
    <div role="note" className="bg-stage text-house">
      <p className="mx-auto flex w-full max-w-6xl items-center gap-2 px-4 py-2 text-sm">
        <ProBadge className="ring-1 ring-house/30" />
        {HACKATHON_BANNER}
      </p>
    </div>
  );
}

export async function HackathonStrip() {
  await connection();
  return <HackathonBanner on={hackathonMode(process.env.HACKATHON_MODE)} />;
}
