// The crowd profile as cards: one per taste list (genres, related artists, places, films,
// brands) and demographics when the profile has them. Every card is Qloo data; the data
// quality banner and its notes are Outdraw's reading of the envelopes behind them. No hooks:
// it renders on the server from /venue/[id]/page.tsx and in tests from a fixture.
import Link from "next/link";

import type { VenueOut } from "@/types/agent";

/** The profile as the agent serves it (optional fields may be null). */
export type Profile = NonNullable<VenueOut["profile"]>;
type ProfileItem = Profile["genres"][number];

interface Props {
  venueId: string;
  venueName: string;
  profile: Profile;
}

const LISTS = [
  ["genres", "Genres", "Music genres your crowd shares."],
  ["related_artists", "Related artists", "Acts your crowd also follows."],
  ["places", "Places", "Where your crowd goes nearby."],
  ["films", "Films", "What your crowd watches."],
  ["brands", "Brands", "Brands your crowd likes."],
] as const satisfies readonly [keyof Profile, string, string][];

/** Affinity groups `crowd.py` averages; anything else in `demographics` is bookkeeping. */
const DEMOGRAPHIC_GROUPS = [
  ["age", "Age"],
  ["gender", "Gender"],
] as const;

/** `24_and_younger` → `24 and younger`. */
const bucketLabel = (key: string) => key.replaceAll("_", " ");

export function SourceBadge({ source }: { source: "qloo" | "outdraw" }) {
  return source === "qloo" ? (
    <span className="inline-block shrink-0 rounded-sm bg-ink px-1.5 whitespace-nowrap py-0.5 text-[11px] font-semibold tracking-wide text-brand-amber uppercase">
      Qloo data
    </span>
  ) : (
    <span className="inline-block shrink-0 rounded-sm border border-ink/30 px-1.5 whitespace-nowrap py-0.5 text-[11px] font-semibold tracking-wide text-ink/70 uppercase">
      Outdraw
    </span>
  );
}

function Card({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  const id = `card-${title.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section aria-labelledby={id} className="rounded-lg border border-ink/15 bg-white/60 p-4">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id={id} className="font-semibold text-ink">
            {title}
          </h2>
          <p className="text-sm text-ink/60">{hint}</p>
        </div>
        <SourceBadge source="qloo" />
      </header>
      {children}
    </section>
  );
}

/** A visual echo of the affinity number beside it; hidden from assistive tech. */
function Bar({ value }: { value: number }) {
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  return (
    <div aria-hidden="true" data-testid="bar" className="h-1.5 w-full rounded-full bg-ink/10">
      <div className="h-full rounded-full bg-ink" style={{ width: `${pct}%` }} />
    </div>
  );
}

function ListCard({ title, hint, items }: { title: string; hint: string; items: ProfileItem[] }) {
  return (
    <Card title={title} hint={hint}>
      {items.length === 0 ? (
        <p className="text-sm text-ink/60">Qloo returned nothing here for this venue.</p>
      ) : (
        <ol className="space-y-2">
          {items.map((item) => (
            <li key={item.entity_id} className="text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 break-words text-ink">{item.name}</span>
                {item.affinity != null && (
                  <span className="shrink-0 font-mono text-xs text-ink/60">
                    {item.affinity.toFixed(2)}
                  </span>
                )}
              </div>
              {item.affinity != null && (
                <Bar value={item.affinity} />
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/** Numeric `{bucket: affinity}` pairs of one demographics group, or none. */
function buckets(value: unknown): [string, number][] {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return [];
  return Object.entries(value).filter(
    (e): e is [string, number] => typeof e[1] === "number" && Number.isFinite(e[1]),
  );
}

function DemographicsCard({ demographics }: { demographics: Record<string, unknown> }) {
  const groups = DEMOGRAPHIC_GROUPS.map(([key, label]) => [label, buckets(demographics[key])] as const)
    .filter(([, rows]) => rows.length > 0);
  if (groups.length === 0) return null;
  return (
    <Card title="Demographics" hint="Aggregate audience affinity by group, never individuals.">
      <div className="space-y-4">
        {groups.map(([label, rows]) => (
          <div key={label}>
            <h3 className="mb-2 text-xs font-medium tracking-wide text-ink/60 uppercase">
              {label}
            </h3>
            <ul className="space-y-2">
              {rows.map(([bucket, value]) => (
                <li key={bucket} className="text-sm">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-ink">{bucketLabel(bucket)}</span>
                    <span className="font-mono text-xs text-ink/60">{value.toFixed(2)}</span>
                  </div>
                  <Bar value={value} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}

function QualityBanner({ profile }: { profile: Profile }) {
  if (profile.data_quality === "ok" && !profile.notes?.length) return null;
  const heading =
    profile.data_quality === "degraded"
      ? "Parts of this profile are missing."
      : profile.data_quality === "partial"
        ? "Some of this profile is partial."
        : "Notes on this profile.";
  return (
    <aside
      aria-label="Data quality"
      className="mb-6 rounded-lg border border-ink/30 bg-white p-4 text-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium text-ink">{heading}</p>
        <SourceBadge source="outdraw" />
      </div>
      {profile.notes && profile.notes.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-ink/70">
          {profile.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

export function ProfileView({ venueId, venueName, profile }: Props) {
  return (
    <>
      <header className="mb-6">
        <p className="text-sm font-medium text-ink/60">Crowd profile</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight break-words">{venueName}</h1>
        <p className="mt-2 text-sm text-ink/70">
          What the crowd that turns out for your past acts also likes, from Qloo&apos;s taste
          graph.
        </p>
      </header>
      <QualityBanner profile={profile} />
      <div className="grid gap-4 sm:grid-cols-2">
        {LISTS.map(([key, title, hint]) => (
          <ListCard key={key} title={title} hint={hint} items={profile[key]} />
        ))}
        {profile.demographics && <DemographicsCard demographics={profile.demographics} />}
      </div>
      <div className="sticky bottom-0 mt-8 -mx-4 border-t border-ink/10 bg-paper px-4 py-4">
        <Link
          href={`/venue/${encodeURIComponent(venueId)}/shortlist`}
          className="block w-full rounded-md bg-ink px-4 py-3 text-center font-semibold text-paper sm:inline-block sm:w-auto"
        >
          Build shortlist
        </Link>
      </div>
    </>
  );
}
