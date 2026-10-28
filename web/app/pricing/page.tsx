import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ProBadge } from "@/src/components/pro";
import {
  FREE_SHORTLIST,
  PRO_FEATURES,
  PRO_PRICE_USD,
  RUNS_PER_DAY,
  hackathonMode,
} from "@/src/lib/entitlements";

// The two plans (T46/T47): Free for one room, Venue Pro for the booking workflow. No
// checkout yet: the judges have Pro through HACKATHON_MODE, and the upgrade itself (a
// Stripe Checkout Session in test mode, with a signature-verified webhook that flips the
// session's stored plan through the agent's PATCH /sessions/{id}) is on the roadmap below.
// The Pro card's action reads HACKATHON_MODE per request inside <Suspense>, as the banner
// does, so the page itself stays static.

export const metadata: Metadata = {
  title: "Pricing",
  description: `Free for one room. Venue Pro at $${PRO_PRICE_USD} a month for the whole booking workflow.`,
};

const FREE_INCLUDES = [
  "One venue and its crowd profile",
  `The top ${FREE_SHORTLIST} acts on every shortlist`,
  "Soundcheck evidence for every act, labelled Qloo or Outdraw",
  `${RUNS_PER_DAY.free} shortlist runs a day`,
  "The three demo venues, any time",
] as const;

const PRO_INCLUDES = [
  ...Object.values(PRO_FEATURES),
  `${RUNS_PER_DAY.pro} shortlist runs a day`,
] as const;

/** In the booker's words; the build notes (test mode, the webhook, the agent's PATCH) are
 * in docs/tasks.md under T47. */
const ROADMAP = [
  {
    title: "Checkout",
    body: "Upgrade to Venue Pro with a card, through Stripe.",
  },
  {
    title: "Instant upgrade",
    body: "Your plan switches to Pro the moment the payment goes through, on the same session you are using now.",
  },
  {
    title: "Billing",
    body: "Change your card, download invoices and cancel any time.",
  },
] as const;

const CTA =
  "inline-flex items-center justify-center rounded-md px-5 py-2.5 text-sm font-semibold";

function Includes({ items, dark = false }: { items: readonly string[]; dark?: boolean }) {
  return (
    <ul className={`mt-6 space-y-2 text-sm ${dark ? "text-house/85" : "text-ink/80"}`}>
      {items.map((item) => (
        <li key={item} className="flex gap-2">
          {/* Drawn, not the ✓ glyph: Geist has none, and the fallback font differs by
              machine, which moved the visual snapshots between CI and Docker. */}
          <svg
            aria-hidden
            viewBox="0 0 16 16"
            className={`mt-1 h-3 w-3 shrink-0 ${dark ? "text-brand-amber" : "text-ink"}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 8.5l3 3 7-7" />
          </svg>
          {item}
        </li>
      ))}
    </ul>
  );
}

/** Under the title while HACKATHON_MODE is on: the one thing to know during judging. */
export function HackathonNote({ hackathon }: { hackathon: boolean }) {
  if (!hackathon) return null;
  return (
    <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-card bg-stage px-4 py-3 text-sm text-house">
      <ProBadge className="ring-1 ring-house/30" />
      <span>
        <strong className="font-semibold">Pro is free for everyone during the Qloo hackathon.</strong>{" "}
        You are on it now: nothing to pay, nothing to set up.
      </span>
    </p>
  );
}

/** The Pro card's action, for the mode in force: Pro is on for everyone during the
 * hackathon; otherwise checkout is not open yet. */
export function ProAction({ hackathon }: { hackathon: boolean }) {
  if (hackathon) {
    return (
      <div className="mt-6">
        <p className="text-sm text-house/85">
          You are on Pro now, free, for the hackathon. Every feature above is on.
        </p>
        <Link href="/#demos" className={`${CTA} mt-4 bg-brand-amber text-stage hover:bg-[#ffc421]`}>
          Open a demo venue
        </Link>
      </div>
    );
  }
  return (
    <div className="mt-6">
      <p className="text-sm text-house/85">
        Checkout isn&apos;t open yet. Start free today; Pro upgrades are next on the roadmap.
      </p>
      <Link href="/venue/new" className={`${CTA} mt-4 border border-house/40 text-house hover:bg-house/10`}>
        Start free
      </Link>
    </div>
  );
}

async function HackathonNoteStrip() {
  await connection();
  return <HackathonNote hackathon={hackathonMode(process.env.HACKATHON_MODE)} />;
}

async function ProActionStrip() {
  await connection();
  return <ProAction hackathon={hackathonMode(process.env.HACKATHON_MODE)} />;
}

/** The page's content, for the mode given; the page reads the mode per request. */
export function Pricing({ note, action }: { note: React.ReactNode; action: React.ReactNode }) {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className="mx-auto w-full max-w-6xl px-4 py-12 sm:py-16">
        <header className="max-w-2xl">
          <p className="text-sm font-semibold tracking-wide text-ink/60 uppercase">Pricing</p>
          <h1 className="display mt-2 text-3xl sm:text-4xl">Free for one room. Pro for the calendar.</h1>
          <p className="mt-3 text-ink/70">
            Every plan reads the same Qloo signals and ranks with the same fixed formula. Pro
            adds the rest of the booking workflow and more runs a day.
          </p>
          {note}
        </header>

        <ul aria-label="Plans" className="mt-10 grid gap-6 md:grid-cols-2">
          <li className="rounded-card border border-line bg-surface p-6 sm:p-8">
            <h2 className="display text-2xl">Free</h2>
            <p className="mt-2 flex items-baseline gap-1">
              <span className="display text-4xl">$0</span>
              <span className="text-sm text-ink/60">a month</span>
            </p>
            <p className="mt-3 text-sm text-ink/70">
              Read your crowd and get a shortlist for one venue.
            </p>
            <Includes items={FREE_INCLUDES} />
            <Link href="/venue/new" className={`${CTA} mt-6 bg-ink text-paper hover:opacity-90`}>
              Set up a venue
            </Link>
          </li>

          {/* The edge keeps it a card on the dark page, where stage and paper are one. */}
          <li className="rounded-card border border-house/15 bg-stage p-6 text-house sm:p-8">
            <h2 className="display flex items-center gap-3 text-2xl">
              Venue Pro <ProBadge className="ring-1 ring-house/30" />
            </h2>
            <p className="mt-2 flex items-baseline gap-1">
              <span className="display text-4xl text-brand-amber">${PRO_PRICE_USD}</span>
              <span className="text-sm text-house/70">a month, per venue group</span>
            </p>
            <p className="mt-3 text-sm text-house/80">
              The whole booking workflow: every act on the list, the brief and the outreach
              email, exports into the tool you already pay for.
            </p>
            <Includes items={PRO_INCLUDES} dark />
            {action}
          </li>
        </ul>

        <section aria-labelledby="roadmap" className="mt-16 max-w-3xl">
          <h2 id="roadmap" className="display text-2xl">
            What&apos;s next
          </h2>
          <p className="mt-2 text-sm text-ink/70">
            Paying for Pro is on the roadmap. Nothing is charged today.
          </p>
          <ol className="mt-6 list-decimal space-y-4 pl-6 marker:font-semibold marker:text-ink/60">
            {ROADMAP.map((step) => (
              <li key={step.title} className="pl-1">
                <h3 className="font-semibold">{step.title}</h3>
                <p className="mt-1 text-sm text-ink/70">{step.body}</p>
              </li>
            ))}
          </ol>
          <p className="mt-6 text-sm text-ink/60">
            Outdraw sits beside the booking tool you already pay for. It ranks and explains;
            it does not replace your calendar, offers or contracts.
          </p>
        </section>
      </div>
    </main>
  );
}

export default function PricingPage() {
  return (
    <Pricing
      note={
        <Suspense fallback={null}>
          <HackathonNoteStrip />
        </Suspense>
      }
      action={
        <Suspense fallback={<div className="mt-6 h-20" aria-hidden />}>
          <ProActionStrip />
        </Suspense>
      }
    />
  );
}
