import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ErrorState } from "@/src/components/error-state";
import { PrecomputedProvider } from "@/src/lib/precomputed";
import { parseRun } from "@/src/lib/run-result";
import { crossDomain } from "@/src/lib/soundcheck";
import { agentError } from "@/src/server/agent";
import { seedPage, type SeedPage } from "@/src/server/seeds";
import { ProfileView } from "../../venue/[id]/profile-view";
import { RefinableRun } from "../../venue/[id]/shortlist/refinable-run";
import { Message, Shell } from "../../venue/[id]/shell";

export const metadata: Metadata = {
  title: "Demo venue",
  description: "A demo venue's crowd profile and shortlist, precomputed from Qloo's taste graph.",
};

/** No static shell to validate: the page is one cached read behind `params`, and no
 * loading.tsx (a skeleton would flash on every cold cache for a page that is otherwise
 * instant). Unknown slugs never get here: proxy.ts answers them with a 404. */
export const instant = false;

/** The slug pattern of agent/draw/seeds.py. */
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** /v/[slug]: a demo venue's precomputed profile, shortlist, Soundcheck, toggle and briefs,
 * read once and cached (src/server/seeds.ts). Nothing on the page calls Qloo or the model;
 * a refinement forks the seed run into a live run on the venue's own shortlist page. */
export default async function DemoVenuePage({ params }: PageProps<"/v/[slug]">) {
  const { slug } = await params;
  if (slug.length > 40 || !SLUG.test(slug)) notFound();

  let page: SeedPage;
  try {
    page = await seedPage(slug);
  } catch (err) {
    // The agent's own refusals are states of this page, read off the digest (the read ran
    // in a `use cache` scope, which hands back a stripped error); anything else is ours (a
    // bug) and goes to ./error.tsx, reported by instrumentation.ts.
    const agentErr = agentError(err);
    if (!agentErr) throw err;
    return (
      <Shell>
        <ErrorState reason={agentErr.reason} retryHref={`/v/${encodeURIComponent(slug)}`} />
      </Shell>
    );
  }
  // Unreachable behind proxy.ts (unknown slugs are 404 there); kept so the page stands alone.
  if (page.state === "missing") notFound();
  if (page.state === "not_ready") {
    return (
      <Shell>
        <Message
          title="This demo isn't ready yet"
          body={`${page.title} hasn't been precomputed yet. Set up your own venue in the meantime.`}
        />
      </Shell>
    );
  }

  const { seed, venueId, profile, run, precomputed } = page;
  return (
    <PrecomputedProvider value={precomputed}>
      <Shell wide>
        <aside
          aria-label="About this demo"
          className="mb-8 rounded-card border border-dashed border-ink/40 p-4 text-sm"
        >
          <p className="flex flex-wrap items-center gap-2 font-medium text-ink">
            <span className="rounded-sm bg-stage px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-brand-amber uppercase">
              Precomputed demo
            </span>
            {seed.title}
          </p>
          <p className="mt-2 text-ink/70">
            {seed.description} Computed ahead of time: browsing it makes no Qloo or model calls.
            Refining the shortlist starts a live run.
          </p>
          <Link href="/venue/new" className="mt-2 inline-block font-medium underline">
            Set up your own venue
          </Link>
        </aside>
        <section id="profile" aria-label="Crowd profile" className="scroll-mt-4">
          <ProfileView
            venueId={venueId}
            venueName={seed.venueName}
            profile={profile}
            action={false}
          />
        </section>
        <section aria-label="Shortlist" className="mt-12 border-t border-line pt-8">
          <RefinableRun
            venueId={venueId}
            venueName={seed.venueName}
            chain={[parseRun(run)]}
            crossDomain={crossDomain(profile)}
            shortlistPath={`/venue/${encodeURIComponent(venueId)}/shortlist`}
            profileHref="#profile"
          />
        </section>
      </Shell>
    </PrecomputedProvider>
  );
}
