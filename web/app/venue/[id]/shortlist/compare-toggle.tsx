"use client";

// The "With Qloo / LLM only" toggle: Outdraw's shortlist beside what the model suggests on
// its own (one tool-less call, cached per run by the agent). Side by side on desktop, tabs on
// mobile. The LLM list is fetched only when the toggle is first switched on, so a run that
// is never compared spends no model tokens on it.
import { useEffect, useId, useState, useSyncExternalStore } from "react";

import { Spinner } from "@/src/components/progress";
import { LivePaused } from "@/src/components/live-paused";
import {
  ApiError,
  api,
  reasonText,
  sampleSeed,
  type CompareOut,
  type Reason,
} from "@/src/lib/api-client";
import type { SeedSlug } from "@/src/lib/seeds";
import { usePrecomputed } from "@/src/lib/precomputed";

/** Below Tailwind's `md`: two lists don't fit side by side. */
const MOBILE = "(max-width: 767px)";

/** The media query, or null where there is none (the server, jsdom). */
const query = () => (typeof window.matchMedia === "function" ? window.matchMedia(MOBILE) : null);

function subscribe(onChange: () => void) {
  const mq = query();
  mq?.addEventListener("change", onChange);
  return () => mq?.removeEventListener("change", onChange);
}

/** Whether the viewport is mobile-sized; desktop on the server (the toggle starts off). */
export function useMobile(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => query()?.matches ?? false,
    () => false,
  );
}

type Load =
  | { name: "loading" }
  | { name: "done"; out: CompareOut }
  | { name: "failed"; reason: Reason }
  /** Refused for want of live analysis (busy, budget, LIVE_MODE=off). */
  | { name: "paused"; slug: SeedSlug }
  /** A seed run precompute made no comparison for: the agent would refuse it, so no fetch. */
  | { name: "absent" };

/** Compare lists by run: the agent caches them, so one fetch per page is enough. */
const loaded = new Map<string, CompareOut>();

/** Clears the module cache between tests. */
export function resetCompareCache() {
  loaded.clear();
}

/** A seed run's list comes precomputed with the page (src/lib/precomputed.tsx): no fetch. */
function useCompare(runId: string, on: boolean): Load {
  const seed = usePrecomputed(runId);
  const [state, setState] = useState<Load>(() => {
    const hit = loaded.get(runId);
    return hit ? { name: "done", out: hit } : { name: "loading" };
  });
  useEffect(() => {
    if (!on || seed || loaded.has(runId)) return;
    let active = true;
    api.getCompare(runId).then(
      (out) => {
        if (!out.error) loaded.set(runId, out);
        if (active) setState({ name: "done", out });
      },
      (err: unknown) => {
        if (!active) return;
        const slug = sampleSeed(err);
        setState(
          slug
            ? { name: "paused", slug }
            : { name: "failed", reason: err instanceof ApiError ? err.reason : "unavailable" },
        );
      },
    );
    return () => {
      active = false;
    };
  }, [runId, on, seed]);
  if (seed) return seed.compare ? { name: "done", out: seed.compare } : { name: "absent" };
  return state;
}

const normalise = (name: string) => name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

function LlmList({ load, qlooNames }: { load: Load; qlooNames: string[] }) {
  if (load.name === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 rounded-card border border-line bg-surface p-4 text-sm text-ink/70">
        <Spinner /> Asking the model for its own list, with no Qloo data…
      </p>
    );
  }
  if (load.name === "paused") {
    return (
      <LivePaused slug={load.slug} className="bg-surface">
        The LLM-only list can&apos;t be fetched right now. Outdraw&apos;s shortlist is unaffected;
        try the toggle again later.
      </LivePaused>
    );
  }
  if (load.name === "failed") {
    return (
      <p role="alert" className="rounded-card border border-line bg-surface p-4 text-sm text-ink/70">
        {reasonText(load.reason)}
      </p>
    );
  }
  if (load.name === "absent") {
    return (
      <p role="status" className="rounded-card border border-line bg-surface p-4 text-sm text-ink/70">
        This demo has no LLM-only list. Refine the shortlist for a live run to compare one.
      </p>
    );
  }
  const { out } = load;
  const acts = out.acts ?? [];
  const ours = new Set(qlooNames.map(normalise));
  const shared = acts.filter((a) => ours.has(normalise(a.name))).length;
  return (
    <div>
      <p data-testid="disclaimer" className="mb-3 rounded-card border border-dashed border-ink/40 p-3 text-xs text-ink/70">
        {out.disclaimer}
      </p>
      {out.error || acts.length === 0 ? (
        <p role="alert" className="rounded-card border border-line bg-surface p-4 text-sm text-ink/70">
          The model gave no usable list for this run.
        </p>
      ) : (
        <>
          <p className="mb-3 text-xs text-ink/60" data-testid="overlap">
            {shared} of {acts.length} also on Outdraw&apos;s shortlist.
          </p>
          <ol aria-label="LLM-only acts" className="space-y-3">
            {acts.map((act, i) => {
              const both = ours.has(normalise(act.name));
              return (
                <li
                  key={`${i}:${act.name}`}
                  data-overlap={both}
                  className="rounded-card border border-dashed border-ink/30 p-4"
                >
                  <div className="flex items-baseline gap-3">
                    <span className="shrink-0 font-mono text-sm text-ink/50" aria-hidden="true">
                      {i + 1}
                    </span>
                    <h3 className="min-w-0 flex-1 font-semibold break-words text-ink">{act.name}</h3>
                    <span className="shrink-0 rounded-sm border border-dashed border-ink/40 px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-ink/70 uppercase">
                      LLM only
                    </span>
                  </div>
                  {act.reason && <p className="mt-2 text-sm text-ink/70">{act.reason}</p>}
                  <p className="mt-2 text-xs text-ink/50">
                    {both ? "Also on Outdraw's shortlist" : "Not on Outdraw's shortlist"} · no
                    Qloo evidence · not scored
                  </p>
                </li>
              );
            })}
          </ol>
        </>
      )}
    </div>
  );
}

const VIEWS = [
  { key: "qloo", label: "With Qloo" },
  { key: "llm", label: "LLM only" },
] as const;
type View = (typeof VIEWS)[number]["key"];

export function CompareToggle({
  runId,
  qlooNames,
  children,
}: {
  runId: string;
  /** Act names on Outdraw's shortlist, to mark the LLM's picks that overlap. */
  qlooNames: string[];
  /** Outdraw's ranked list, rendered by the server. */
  children: React.ReactNode;
}) {
  const [on, setOn] = useState(false);
  const [view, setView] = useState<View>("qloo");
  const mobile = useMobile();
  const load = useCompare(runId, on);
  const id = useId();

  const llm = <LlmList load={load} qlooNames={qlooNames} />;

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <span id={`${id}-label`} className="text-sm font-medium text-ink">
          With Qloo / LLM only
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={`${id}-label`}
          onClick={() => setOn((v) => !v)}
          className={`relative h-6 w-11 shrink-0 rounded-full border ${on ? "border-stage bg-stage ring-1 ring-line" : "border-ink bg-transparent"}`}
        >
          <span
            aria-hidden="true"
            className={`absolute top-0.5 h-4.5 w-4.5 rounded-full ${on ? "left-5.5 bg-brand-amber" : "left-0.5 bg-ink"}`}
          />
        </button>
      </div>

      {!on ? (
        children
      ) : mobile ? (
        <div data-testid="compare-tabs">
          <div role="tablist" aria-label="With Qloo / LLM only" className="mb-4 grid grid-cols-2 rounded-card border border-ink/20 p-1 text-sm">
            {VIEWS.map((v) => (
              <button
                key={v.key}
                type="button"
                role="tab"
                id={`${id}-tab-${v.key}`}
                aria-selected={view === v.key}
                aria-controls={`${id}-panel-${v.key}`}
                onClick={() => setView(v.key)}
                className={`rounded-md px-3 py-1.5 font-medium ${view === v.key ? "bg-ink text-paper" : "text-ink"}`}
              >
                {v.label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`${id}-panel-qloo`}
            aria-labelledby={`${id}-tab-qloo`}
            hidden={view !== "qloo"}
          >
            {children}
          </div>
          <div
            role="tabpanel"
            id={`${id}-panel-llm`}
            aria-labelledby={`${id}-tab-llm`}
            hidden={view !== "llm"}
          >
            {llm}
          </div>
        </div>
      ) : (
        <div data-testid="compare-columns" className="grid grid-cols-2 gap-6">
          <section aria-labelledby={`${id}-col-qloo`} className="min-w-0">
            <h2 id={`${id}-col-qloo`} className="mb-3 text-sm font-semibold text-ink">
              With Qloo
            </h2>
            {children}
          </section>
          <section aria-labelledby={`${id}-col-llm`} className="min-w-0">
            <h2 id={`${id}-col-llm`} className="mb-3 text-sm font-semibold text-ink">
              LLM only
            </h2>
            {llm}
          </section>
        </div>
      )}
    </>
  );
}
