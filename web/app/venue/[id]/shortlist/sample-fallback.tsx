"use client";

// What a shortlist page shows when live analysis is refused (the web's rate limit, the
// agent's daily budget or LIVE_MODE=off, all alike): the closest seed venue's precomputed
// shortlist under a banner, instead of an error. The sample is read from the seed cache
// (GET /api/seeds/{slug}), so it spends nothing and answers while the agent refuses runs.
// It is read-only here: refining it would be another live run.
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { api, type SeedSample } from "@/src/lib/api-client";
import { PrecomputedProvider } from "@/src/lib/precomputed";
import { parseRun } from "@/src/lib/run-result";
import type { SeedSlug } from "@/src/lib/seeds";
import { RefinableRun } from "./refinable-run";
import { ShortlistSkeleton } from "./shortlist-view";

export const SAMPLE_BANNER = "Live analysis is busy, here's a sample";

type State = { name: "loading" } | { name: "ready"; sample: SeedSample } | { name: "failed" };

/** In-flight reads by seed: Strict Mode mounts effects twice. */
const inFlight = new Map<SeedSlug, Promise<SeedSample>>();

function readSample(slug: SeedSlug): Promise<SeedSample> {
  let read = inFlight.get(slug);
  if (!read) {
    read = api.getSeedSample(slug).finally(() => inFlight.delete(slug));
    inFlight.set(slug, read);
  }
  return read;
}

interface Props {
  slug: SeedSlug;
  /** Under the banner text: how to get back to live analysis. */
  action?: ReactNode;
  /** Shown instead when the sample can't be read either. */
  fallback: ReactNode;
}

export function SampleFallback({ slug, action, fallback }: Props) {
  const [state, setState] = useState<State>({ name: "loading" });

  useEffect(() => {
    let active = true;
    readSample(slug).then(
      (sample) => active && setState({ name: "ready", sample }),
      () => active && setState({ name: "failed" }),
    );
    return () => {
      active = false;
    };
  }, [slug]);

  if (state.name === "failed") return fallback;
  if (state.name === "loading") return <ShortlistSkeleton label="Loading a sample shortlist" />;

  const { sample } = state;
  const demo = `/v/${encodeURIComponent(sample.slug)}`;
  return (
    <>
      <aside
        role="status"
        aria-label="Sample shortlist"
        className="mb-8 rounded-card border border-dashed border-ink/40 p-4 text-sm"
      >
        <p className="flex flex-wrap items-center gap-2 font-medium text-ink">
          <span className="rounded-sm bg-stage px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-brand-amber uppercase">
            Sample
          </span>
          {SAMPLE_BANNER}
        </p>
        <p className="mt-2 text-ink/70">
          Outdraw can&apos;t run a live analysis right now, so this is the precomputed shortlist
          of the demo venue {sample.venueName} ({sample.title}). It made no Qloo or model calls.
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          {action}
          <Link href={demo} className="font-medium underline">
            Open the full demo
          </Link>
        </div>
      </aside>
      <PrecomputedProvider value={sample.precomputed}>
        <RefinableRun
          venueId={sample.venueId}
          venueName={sample.venueName}
          chain={[parseRun(sample.run)]}
          crossDomain={sample.crossDomain}
          played={sample.played}
          profileHref={`${demo}#profile`}
          refinable={false}
        />
      </PrecomputedProvider>
    </>
  );
}
