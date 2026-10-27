import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import {
  SUB_SCORES,
  SUB_SCORE_LABELS,
  parseRun,
  type ShortlistEntry,
  type SubScore,
} from "@/src/lib/run-result";
import type { SeedSlug } from "@/src/lib/seeds";
import { seedCards, seedPage, type SeedCard, type SeedPage } from "@/src/server/seeds";

export const metadata: Metadata = {
  title: { absolute: "Outdraw" },
  description: "Book the acts your crowd will turn out for, from Qloo's taste graph.",
};

const CTA =
  "inline-flex items-center justify-center rounded-md px-6 py-3 text-base font-semibold";

const pct = (value: number) => `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%`;

/** A ready seed page, or null while it is being precomputed or the read fails. Every
 * landing section reads the same cached entry as /v/[slug], so the page spends nothing. */
async function readySeed(slug: string): Promise<Extract<SeedPage, { state: "ready" }> | null> {
  try {
    const page = await seedPage(slug);
    return page.state === "ready" ? page : null;
  } catch {
    return null;
  }
}

/** The seed the hero and the crowd-signals panel show. Brooklyn reads best on the real
 * data: two genres, venues a booker knows, and the strongest top act; nola-jazz (the
 * agent's own fallback) has a single genre in its profile. */
const SHOWCASE: SeedSlug = "brooklyn-indie";

// --- hero ---------------------------------------------------------------------------------

/** On the stage: a Qloo-sourced number in amber, Outdraw's own outlined. */
function HeroChip({ name, entry }: { name: SubScore; entry: ShortlistEntry }) {
  const value = entry.sub_scores?.[name];
  if (value == null) return null;
  const neutral = entry.neutral?.includes(name);
  const qloo = entry.source_labels?.[name] === "qloo";
  return (
    <li
      className={`rounded-full px-2 py-0.5 ${
        neutral
          ? "border border-dashed border-house/40 text-house/70"
          : qloo
            ? "bg-stage text-house ring-1 ring-house/20"
            : "border border-house/40 text-house"
      }`}
    >
      {SUB_SCORE_LABELS[name]}{" "}
      <span className={`font-mono ${qloo && !neutral ? "text-brand-amber" : ""}`}>
        {value.toFixed(2)}
      </span>
      <span className="sr-only">
        {neutral ? " (no Qloo signal, run median)" : qloo ? " (Qloo data)" : " (Outdraw)"}
      </span>
    </li>
  );
}

function HeroMark() {
  return (
    <Image
      src="/brand/flat-mark/mark-amber.svg"
      alt=""
      aria-hidden="true"
      data-testid="hero-mark"
      width={512}
      height={512}
      priority
      className="pointer-events-none mx-auto hidden h-72 w-72 opacity-30 lg:block"
    />
  );
}

/** The product, before the first scroll: the top act of the showcase seed's precomputed
 * shortlist, as the shortlist draws it (the fixed score, its bar, the reason, the labelled
 * sub-scores). Real stored numbers, never a mock-up; the mark when the seed isn't ready. */
/** The act at the top of a ready seed's shortlist (the first with sub-scores). */
function topAct(page: Extract<SeedPage, { state: "ready" }>): ShortlistEntry | undefined {
  const run = parseRun(page.run);
  if (run.kind !== "shortlist") return undefined;
  return run.shortlist.find((e) => e.sub_scores) ?? run.shortlist[0];
}

async function HeroPreview() {
  await connection();
  const page = await readySeed(SHOWCASE);
  const entry = page && topAct(page);
  if (!page || !entry) return <HeroMark />;
  const reason = entry.top_reason ?? entry.reason;
  const demo = `/v/${encodeURIComponent(page.seed.slug)}`;
  return (
    <div
      data-testid="hero-preview"
      aria-label="Shortlist preview"
      className="rounded-card bg-stage-2 p-6 text-house ring-1 ring-house/15 sm:p-8"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-sm border border-dashed border-house/40 px-1.5 py-0.5 font-semibold tracking-wide text-house/70 uppercase">
          Precomputed demo
        </span>
        <span className="text-house/70">
          {page.seed.title} · {page.seed.capacity.toLocaleString("en-US")} cap
        </span>
      </div>
      <p className="mt-8 text-xs font-semibold tracking-wide text-house/60 uppercase">
        Top of the shortlist
      </p>
      <div className="mt-2 flex items-end justify-between gap-6">
        <p className="display min-w-0 text-3xl break-words sm:text-4xl">{entry.name}</p>
        <p className="display shrink-0 text-6xl leading-none sm:text-7xl">
          <span className="sr-only">Score </span>
          {entry.total.toFixed(2)}
        </p>
      </div>
      <div aria-hidden="true" className="mt-4 h-2 w-full rounded-full bg-stage ring-1 ring-house/15">
        <div className="h-full rounded-full bg-brand-amber" style={{ width: pct(entry.total) }} />
      </div>
      {reason && <p className="mt-6 text-sm text-house/80">{reason}</p>}
      {entry.sub_scores && (
        <ul aria-label={`Sub-scores for ${entry.name}`} className="mt-4 flex flex-wrap gap-2 text-xs">
          {SUB_SCORES.map((name) => (
            <HeroChip key={name} name={name} entry={entry} />
          ))}
        </ul>
      )}
      <div className="mt-8 flex flex-wrap items-center justify-between gap-4 text-sm">
        <Link href={demo} className="font-semibold underline">
          Open the demo
        </Link>
        <span className="text-xs text-house/60">
          <span className="text-brand-amber">Amber</span> is Qloo data. The rest is Outdraw.
        </span>
      </div>
    </div>
  );
}

function HeroSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading shortlist preview"
      className="h-96 animate-pulse rounded-card bg-stage-2 ring-1 ring-house/15"
    />
  );
}

/** The stage: a dark band in both themes, where amber may shine. */
function Hero() {
  return (
    <section aria-labelledby="hero" className="bg-stage text-house">
      <div className="mx-auto grid w-full max-w-6xl gap-12 px-4 py-16 sm:py-24 lg:grid-cols-[minmax(0,1fr)_32rem] lg:items-center lg:gap-16">
        <div>
          <p className="text-sm font-semibold tracking-[0.18em] text-brand-amber uppercase">
            Booking intelligence for live-music venues
          </p>
          <h1 id="hero" className="display mt-6 max-w-3xl text-5xl sm:text-6xl lg:text-7xl">
            Book the acts your crowd will turn out for.
          </h1>
          <p className="mt-6 max-w-2xl text-lg text-house/80">
            Outdraw reads your crowd&apos;s taste from Qloo&apos;s taste graph, across music,
            food, film and brands, then ranks acts with a fixed, explained score. Every pick
            carries its evidence.
          </p>
          <div className="mt-8 flex flex-col gap-4 sm:flex-row">
            <Link href="/venue/new" className={`${CTA} bg-brand-amber text-stage hover:bg-[#ffc421]`}>
              Set up your venue
            </Link>
            <Link
              href="#demos"
              className={`${CTA} border border-house/40 text-house hover:border-house`}
            >
              Try a demo venue
            </Link>
          </div>
          <p className="mt-12 max-w-2xl border-l-2 border-brand-amber pl-4 text-house/80">
            Streaming numbers say who is popular. Outdraw says who this room&apos;s crowd will
            love next.
          </p>
        </div>
        <Suspense fallback={<HeroSkeleton />}>
          <HeroPreview />
        </Suspense>
      </div>
    </section>
  );
}

// --- demo cards -------------------------------------------------------------------------

interface CardPreview {
  acts: { name: string; total: number }[];
  /** The crowd's top genre and top place, when the profile has them. */
  genre?: string;
  place?: string;
}

async function cardPreview(slug: string): Promise<CardPreview> {
  const page = await readySeed(slug);
  if (!page) return { acts: [] };
  return {
    acts: (page.run.result?.shortlist ?? [])
      .slice(0, 3)
      .map((entry) => ({ name: entry.name, total: entry.total })),
    genre: page.profile.genres[0]?.name,
    place: page.profile.places[0]?.name,
  };
}

function Signature({ genre, place }: Pick<CardPreview, "genre" | "place">) {
  if (!genre && !place) return null;
  return (
    <p className="mt-4 text-sm text-ink/80">
      <span className="font-semibold text-ink">Crowd:</span>{" "}
      {[genre, place].filter(Boolean).join(" · ")}
    </p>
  );
}

function TopThree({ acts }: { acts: CardPreview["acts"] }) {
  if (acts.length === 0) {
    return <p className="mt-4 text-sm text-ink/60">Shortlist on its way.</p>;
  }
  return (
    <div className="mt-4">
      <p className="text-xs font-semibold tracking-wide text-ink/60 uppercase">
        Top 3 by Outdraw&apos;s score
      </p>
      <ol aria-label="Top three acts" className="mt-2 space-y-2">
        {acts.map((act, i) => (
          <li key={act.name} className="text-sm">
            <div className="flex items-baseline gap-2">
              <span className="display w-5 shrink-0 text-base text-ink/60" aria-hidden="true">
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 truncate font-medium text-ink">{act.name}</span>
              <span className="shrink-0 font-mono text-xs text-ink/60">
                <span className="sr-only">Score </span>
                {act.total.toFixed(2)}
              </span>
            </div>
            <div aria-hidden="true" className="mt-1 ml-7 h-1.5 rounded-full bg-stage">
              <div className="h-full rounded-full bg-brand-amber" style={{ width: pct(act.total) }} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

async function Card({ seed }: { seed: SeedCard }) {
  const preview = await cardPreview(seed.slug);
  const showcase = seed.slug === SHOWCASE;
  return (
    <li className="flex">
      <Link
        href={`/v/${encodeURIComponent(seed.slug)}`}
        data-showcase={showcase || undefined}
        className={`group relative flex w-full flex-col rounded-card border bg-surface p-6 hover:border-ink ${
          showcase ? "border-ink" : "border-line"
        }`}
      >
        {/* Pinned beside "Open demo", clear of any title: the three cards keep the same flow. */}
        {showcase && (
          <span className="absolute right-6 bottom-6 rounded-sm bg-stage px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-brand-amber uppercase">
            Shown above
          </span>
        )}
        {/* Two lines reserved: a longer title doesn't push its card's rows below its neighbours'. */}
        <h3 className="display min-h-[3.1rem] text-2xl text-ink">{seed.title}</h3>
        <p className="mt-2 text-sm text-ink/60">
          {seed.venueName} · {seed.city} · {seed.capacity.toLocaleString("en-US")} cap
        </p>
        <p className="mt-2 text-sm text-ink/80">{seed.description}</p>
        <Signature genre={preview.genre} place={preview.place} />
        <TopThree acts={preview.acts} />
        <span className="mt-auto pt-6 text-sm font-semibold text-ink group-hover:underline">
          Open demo
        </span>
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
      <p role="status" className="rounded-card border border-line bg-surface p-6 text-sm text-ink/70">
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
        <li key={i} className="h-72 animate-pulse rounded-card border border-line bg-surface-2" />
      ))}
    </ul>
  );
}

function Demos() {
  return (
    <section aria-labelledby="demos-heading" id="demos" className="scroll-mt-8 border-t border-line py-16">
      <div className="mx-auto w-full max-w-6xl px-4">
        <p className="text-sm font-semibold tracking-wide text-ink/60 uppercase">Demo venues</p>
        <h2 id="demos-heading" className="display mt-2 text-3xl sm:text-4xl">
          Three rooms, already read.
        </h2>
        <p className="mt-2 mb-8 max-w-2xl text-ink/70">
          Precomputed from Qloo ahead of time: each opens instantly and browsing it makes no
          Qloo or model calls.
        </p>
        <Suspense fallback={<CardsSkeleton />}>
          <DemoCards />
        </Suspense>
      </div>
    </section>
  );
}

// --- how it works -----------------------------------------------------------------------

const STEPS = [
  {
    title: "Tell us who drew",
    body: "Your room, its capacity and a few past shows with tickets sold. Outdraw matches each act in Qloo and you confirm the matches.",
  },
  {
    title: "Read the crowd",
    body: "From the acts that sold, Qloo describes the crowd: genres and related artists, and the bars, films and brands it skews toward near your venue.",
  },
  {
    title: "Rank with evidence",
    body: "Candidates are scored by a fixed formula over Qloo signals. The model explains and drafts the brief. It never sets the order.",
  },
] as const;

function HowItWorks() {
  return (
    <section aria-labelledby="how" id="how-it-works" className="scroll-mt-8 border-t border-line py-16">
      <div className="mx-auto w-full max-w-6xl px-4">
        <p className="text-sm font-semibold tracking-wide text-ink/60 uppercase">How it works</p>
        <h2 id="how" className="display mt-2 text-3xl sm:text-4xl">
          Three steps from past shows to a shortlist.
        </h2>
        {/* One rule runs across all three steps: a sequence, not three boxes. */}
        <ol className="mt-12 grid gap-8 border-t-2 border-ink sm:grid-cols-3 sm:gap-0">
          {STEPS.map((step, i) => (
            <li key={step.title} className="pt-6 sm:pr-8 sm:last:pr-0">
              <span className="display block text-5xl text-ink" aria-hidden="true">
                0{i + 1}
              </span>
              <h3 className="display mt-4 text-xl">
                <span className="sr-only">Step {i + 1}: </span>
                {step.title}
              </h3>
              <p className="mt-2 max-w-xs text-sm text-ink/70">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

// --- why Qloo ---------------------------------------------------------------------------

const STREAMING = [
  "Global plays and followers",
  "Who sold in this room before",
  "Chart momentum, worldwide",
];

const QLOO = [
  "The bars, restaurants, films and brands your crowd skews toward, tied to your neighbourhood",
  "Audience overlap between acts, for support pairings and acts like your past hits",
  "Local heat and momentum for an act in your region",
  "Age skew, to match the night's format",
];

/** Items per list in the crowd-signals panel. */
const SIGNAL_ITEMS = 3;

const SIGNAL_LISTS = [
  ["genres", "Music"],
  ["places", "Places"],
  ["films", "Films"],
  ["brands", "Brands"],
] as const;

/** The showcase seed's crowd, as Qloo describes it across domains: the lists behind the
 * profile page and Soundcheck's strip, read from the same precomputed entry. Nothing is
 * drawn when the seed isn't ready. */
async function CrowdSignals() {
  await connection();
  const page = await readySeed(SHOWCASE);
  if (!page) return null;
  const act = topAct(page);
  return (
    <div
      data-testid="crowd-signals"
      aria-label="Crowd signals"
      className="mt-8 rounded-card bg-stage p-6 text-house ring-1 ring-house/15"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 className="display text-xl text-brand-amber">
            What Qloo knows about {page.seed.title}&apos;s crowd
          </h3>
          <p className="mt-1 text-sm text-house/70">
            Aggregate affinities for the room&apos;s crowd, from its past acts. Never about
            individuals.
          </p>
        </div>
        <span className="rounded-sm border border-dashed border-house/40 px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-house/70 uppercase">
          Precomputed demo
        </span>
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
        {SIGNAL_LISTS.map(([key, label], i) => {
          const [top, ...rest] = page.profile[key].slice(0, SIGNAL_ITEMS);
          return (
            <section
              key={key}
              aria-label={label}
              className="rounded-card bg-stage-2 p-4 ring-1 ring-house/15"
            >
              <div className="flex items-baseline justify-between gap-2">
                <h4 className="text-xs font-semibold tracking-wide text-house/60 uppercase">
                  {label}
                </h4>
                <span className="font-mono text-xs text-house/50" aria-hidden="true">
                  0{i + 1}
                </span>
              </div>
              {!top ? (
                <p className="mt-4 text-sm text-house/60">Nothing here for this crowd.</p>
              ) : (
                <>
                  {/* Two lines reserved, so the numbers share a baseline whatever the name. */}
                  <p className="display mt-4 min-h-[2.6rem] text-xl break-words">{top.name}</p>
                  {top.affinity != null && (
                    <p className="display mt-1 text-3xl text-brand-amber">
                      <span className="sr-only">Affinity </span>
                      {top.affinity.toFixed(2)}
                    </p>
                  )}
                  {rest.length > 0 && (
                    <ol className="mt-4 space-y-1 border-t border-house/15 pt-3 text-sm text-house/70">
                      {rest.map((item) => (
                        <li key={item.entity_id} className="flex items-baseline justify-between gap-2">
                          <span className="min-w-0 truncate">{item.name}</span>
                          {item.affinity != null && (
                            <span className="shrink-0 font-mono text-xs text-brand-amber">
                              {item.affinity.toFixed(2)}
                            </span>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}
            </section>
          );
        })}
        {act && (
          <section
            aria-label="Top act"
            className="rounded-card border-2 border-brand-amber bg-stage-2 p-4 sm:col-span-2 lg:p-6"
          >
            <div className="flex items-baseline justify-between gap-2">
              <h4 className="text-xs font-semibold tracking-wide text-brand-amber uppercase">
                So Outdraw ranks
              </h4>
              <span className="font-mono text-xs text-house/50" aria-hidden="true">
                05
              </span>
            </div>
            <p className="display mt-4 text-xl break-words lg:text-3xl">{act.name}</p>
            {act.sub_scores?.crowd_affinity != null && (
              <p className="display mt-1 text-3xl text-brand-amber lg:text-5xl">
                <span className="sr-only">Crowd affinity </span>
                {act.sub_scores.crowd_affinity.toFixed(2)}
              </p>
            )}
            <p className="mt-4 border-t border-house/15 pt-3 text-sm text-house/70">
              Crowd affinity from Qloo, then Outdraw&apos;s score{" "}
              <span className="font-mono text-house">{act.total.toFixed(2)}</span> puts it first.
            </p>
          </section>
        )}
      </div>
      <p className="mt-6 text-xs text-house/60">
        Every amber number is Qloo data. Which acts the crowd will turn out for is
        Outdraw&apos;s score on top of it.{" "}
        <Link
          href={`/v/${encodeURIComponent(page.seed.slug)}#profile`}
          className="font-semibold text-house underline"
        >
          See the full crowd profile
        </Link>
      </p>
    </div>
  );
}

function WhyQloo() {
  return (
    <section aria-labelledby="why-qloo" id="why" className="scroll-mt-8 border-t border-line py-16">
      <div className="mx-auto w-full max-w-6xl px-4">
        <p className="text-sm font-semibold tracking-wide text-ink/60 uppercase">Why Qloo</p>
        <h2 id="why-qloo" className="display mt-2 max-w-3xl text-3xl sm:text-4xl">
          Streaming numbers measure the world. Your crowd lives on one street.
        </h2>
        <p className="mt-4 max-w-2xl text-ink/70">
          Booking tools answer &ldquo;who is popular?&rdquo; and &ldquo;who sold here
          before?&rdquo;. Neither answers &ldquo;who will this neighbourhood&apos;s crowd love
          that we have never booked?&rdquo;. That takes taste across domains, tied to a
          location: Qloo&apos;s data.
        </p>
        <Suspense fallback={null}>
          <CrowdSignals />
        </Suspense>
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <div className="rounded-card border border-line bg-surface p-6">
            <h3 className="display text-xl">What streaming data measures</h3>
            <ul className="mt-4 space-y-2 text-sm text-ink/70">
              {STREAMING.map((item) => (
                <li key={item} className="flex gap-3">
                  <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-ink/40" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-card bg-stage p-6 text-house ring-1 ring-house/15">
            <h3 className="display text-xl text-brand-amber">What Qloo adds for your room</h3>
            <ul className="mt-4 space-y-2 text-sm text-house/85">
              {QLOO.map((item) => (
                <li key={item} className="flex gap-3">
                  <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-amber" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
        </div>
        <p className="mt-6 max-w-2xl text-sm text-ink/70">
          Take Qloo away and Outdraw could only guess. The Soundcheck panel shows the difference
          on every pick, with each number labelled as Qloo data or Outdraw&apos;s interpretation.
        </p>
      </div>
    </section>
  );
}

/** The landing page: the stage with a real shortlist row, the demo venues, how it works and
 * why Qloo, with the showcase crowd's signals. */
export default function Home() {
  return (
    <main className="flex-1 bg-paper text-ink">
      <Hero />
      <Demos />
      <HowItWorks />
      <WhyQloo />
    </main>
  );
}
