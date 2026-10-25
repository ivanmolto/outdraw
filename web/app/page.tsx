import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { seedCards, type SeedCard } from "@/src/server/seeds";

export const metadata: Metadata = {
  title: "Outdraw",
  description: "Book the acts your crowd will turn out for, from Qloo's taste graph.",
};

function Card({ seed }: { seed: SeedCard }) {
  return (
    <li>
      <Link
        href={`/v/${encodeURIComponent(seed.slug)}`}
        className="block h-full rounded-lg border border-ink/15 bg-white/60 p-4 hover:border-ink"
      >
        <h3 className="font-semibold text-ink">{seed.title}</h3>
        <p className="mt-1 text-xs text-ink/60">
          {seed.venueName} · {seed.city} · {seed.capacity.toLocaleString("en-US")} capacity
        </p>
        <p className="mt-2 text-sm text-ink/70">{seed.description}</p>
        <span className="mt-3 inline-block text-sm font-medium underline">Open demo</span>
      </Link>
    </li>
  );
}

/** The ready demo venues; request-bound, so the build never calls the agent. */
async function DemoCards() {
  await connection();
  let seeds: SeedCard[];
  try {
    seeds = await seedCards();
  } catch {
    seeds = [];
  }
  if (seeds.length === 0) {
    return (
      <p role="status" className="rounded-lg border border-ink/15 bg-white/60 p-4 text-sm text-ink/70">
        The demo venues aren&apos;t available right now. Set up your own venue instead.
      </p>
    );
  }
  return (
    <ul aria-label="Demo venues" className="grid gap-4 sm:grid-cols-3">
      {seeds.map((seed) => (
        <Card key={seed.slug} seed={seed} />
      ))}
    </ul>
  );
}

function CardsSkeleton() {
  return (
    <ul aria-busy="true" aria-label="Loading demo venues" className="grid gap-4 sm:grid-cols-3">
      {[0, 1, 2].map((i) => (
        <li key={i} className="h-40 animate-pulse rounded-lg border border-ink/15 bg-ink/5" />
      ))}
    </ul>
  );
}

/** The landing page: what Outdraw does, the way in for a venue, and the demo venues. */
export default function Home() {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className="mx-auto w-full max-w-5xl px-4 py-12">
        <p className="text-sm font-semibold tracking-wide text-ink/60 uppercase">Outdraw</p>
        <h1 className="mt-2 max-w-2xl text-3xl font-semibold tracking-tight">
          Book the acts your crowd will turn out for.
        </h1>
        <p className="mt-3 max-w-2xl text-ink/70">
          Tell Outdraw who has played your room. It reads your crowd&apos;s tastes from
          Qloo&apos;s taste graph and ranks acts with a fixed, explained score.
        </p>
        <Link
          href="/venue/new"
          className="mt-6 inline-block rounded-md bg-ink px-4 py-3 font-semibold text-paper"
        >
          Set up your venue
        </Link>

        <section aria-labelledby="demos" className="mt-12">
          <h2 id="demos" className="text-lg font-semibold">
            Try a demo venue
          </h2>
          <p className="mt-1 mb-4 text-sm text-ink/70">
            Precomputed: opens instantly and makes no Qloo or model calls.
          </p>
          <Suspense fallback={<CardsSkeleton />}>
            <DemoCards />
          </Suspense>
        </section>
      </div>
    </main>
  );
}
