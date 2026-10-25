"use client";

// Soundcheck: everything behind one act's score, as a slide-over (a bottom sheet under
// 640px). "Why this act" first, then the trail as an expandable tree (inputs → Qloo calls →
// affinities → score breakdown with weights) and the cross-domain strip. Every item carries
// the agent's own source label (`source_labels`): Qloo data or Draw's interpretation. The UI
// labels nothing itself: the top reason and the profile lists have keys in the trail too.
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Spinner } from "@/src/components/progress";
import { ApiError, api, reasonText, type Reason } from "@/src/lib/api-client";
import { SUB_SCORES, SUB_SCORE_LABELS, type SubScore } from "@/src/lib/run-result";
import {
  REF,
  SUMMARISED,
  affinitySubScore,
  formatNeutral,
  labelOf,
  type CrossDomain,
  type CrossDomainItem,
  type SoundcheckData,
} from "@/src/lib/soundcheck";
import type { QlooStatus, SourceLabel } from "@/types/schemas";

const EMPTY: CrossDomain = { places: [], films: [], brands: [] };
/** Items per cross-domain list in the strip. */
const STRIP_ITEMS = 3;

export function Provenance({ source }: { source: SourceLabel | undefined }) {
  if (source === "qloo") {
    return (
      <span
        data-testid="source"
        data-source="qloo"
        title="Qloo data"
        className="inline-block shrink-0 rounded-sm bg-ink px-1 py-px text-[10px] font-semibold tracking-wide text-brand-amber uppercase"
      >
        Qloo
      </span>
    );
  }
  if (source === "draw") {
    return (
      <span
        data-testid="source"
        data-source="draw"
        title="Outdraw's interpretation"
        className="inline-block shrink-0 rounded-sm border border-ink/30 px-1 py-px text-[10px] font-semibold tracking-wide text-ink/70 uppercase"
      >
        Draw
      </span>
    );
  }
  // The agent validates every trail as fully labelled; say so plainly if one slips through.
  return (
    <span
      data-testid="source"
      data-source="none"
      className="inline-block shrink-0 rounded-sm border border-dashed border-ink/40 px-1 py-px text-[10px] text-ink/60"
    >
      Unlabelled
    </span>
  );
}

function StatusChip({ status }: { status: QlooStatus }) {
  const style =
    status === "ok"
      ? "bg-ink text-paper"
      : status === "partial" || status === "degraded"
        ? "bg-ink text-brand-amber"
        : "border border-dashed border-ink/40 text-ink/70";
  return (
    <span
      data-testid="status"
      className={`inline-block rounded-full px-2 py-px font-mono text-[11px] ${style}`}
    >
      {status}
    </span>
  );
}

function Item({ source, children }: { source: SourceLabel | undefined; children: React.ReactNode }) {
  return (
    <li data-testid="trail-item" className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0 flex-1 break-words">{children}</div>
      <Provenance source={source} />
    </li>
  );
}

function Branch({
  title,
  count,
  open = false,
  children,
}: {
  title: string;
  count?: number;
  open?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details open={open} className="group border-t border-ink/15 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-2 py-1 text-sm font-semibold text-ink">
        <span aria-hidden="true" className="inline-block w-3 transition-transform group-open:rotate-90">
          ›
        </span>
        <h3>{title}</h3>
        {count !== undefined && <span className="font-mono text-xs text-ink/50">{count}</span>}
      </summary>
      <div className="pl-5 text-sm">{children}</div>
    </details>
  );
}

const value2 = (v: number) => v.toFixed(2);

/** The panel's content for a loaded trail. No fetching: the tests render it from a fixture. */
export function Soundcheck({
  data,
  crossDomain = EMPTY,
}: {
  data: SoundcheckData;
  crossDomain?: CrossDomain;
}) {
  const { trail, summaries } = data;
  const neutral = new Set(trail.neutral);
  const subs = SUB_SCORES.filter((name) => trail.sub_scores[name] != null);
  const region = trail.inputs.find((i) => i.ref === "region");
  const strip: { list: keyof CrossDomain; kind: string; item: CrossDomainItem }[] = (
    [
      ["places", "Place"],
      ["films", "Film"],
      ["brands", "Brand"],
    ] as const
  ).flatMap(([list, kind]) =>
    crossDomain[list].slice(0, STRIP_ITEMS).map((item) => ({ list, kind, item })),
  );

  return (
    <div className="space-y-5">
      <section aria-labelledby="why-this-act">
        <h3 id="why-this-act" className="text-sm font-semibold text-ink">
          Why this act
        </h3>
        <ul>
          <Item source={labelOf(trail, REF.topReason)}>
            <p className="text-ink/80">{trail.top_reason}</p>
          </Item>
        </ul>
      </section>

      <div role="group" aria-label="Evidence trail">
        <Branch title="Inputs" count={trail.inputs.length} open>
          <ul aria-label="Inputs">
            {trail.inputs.map((input) => (
              <Item key={input.ref} source={labelOf(trail, REF.input(input.ref))}>
                {input.label}
              </Item>
            ))}
          </ul>
        </Branch>

        <Branch title="Qloo calls" count={trail.qloo_calls.length} open>
          {trail.qloo_calls.length === 0 ? (
            <p className="py-1.5 text-ink/60">No Qloo calls behind this score.</p>
          ) : (
            <ul aria-label="Qloo calls">
              {trail.qloo_calls.map((call) => {
                const summary = summaries[call.call_id];
                return (
                  <Item key={call.call_id} source={labelOf(trail, REF.call(call.call_id))}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-medium text-ink">{call.workflow}</span>
                      <StatusChip status={call.status} />
                      <span data-testid="cache" className="text-xs text-ink/60">
                        {call.cache_hit ? "cached" : "live"}
                      </span>
                    </div>
                    {call.params_summary && (
                      <p className="mt-0.5 font-mono text-xs break-all text-ink/50">
                        {call.params_summary}
                      </p>
                    )}
                    {SUMMARISED.has(call.status) && (
                      <p data-testid="envelope-summary" className="mt-1 text-xs text-ink/80">
                        {summary ?? `Qloo marked this call ${call.status} without a summary.`}
                      </p>
                    )}
                  </Item>
                );
              })}
            </ul>
          )}
        </Branch>

        <Branch title="Affinities" count={trail.affinities.length} open>
          {trail.affinities.length === 0 ? (
            <p className="py-1.5 text-ink/60">No Qloo affinity behind this score.</p>
          ) : (
            <ul aria-label="Affinities">
              {trail.affinities.map((affinity) => {
                const sub = affinitySubScore(trail.qloo_calls, affinity);
                const workflow = trail.qloo_calls.find((c) => c.call_id === affinity.call_id)?.workflow;
                return (
                  <Item
                    key={`${affinity.call_id}:${sub ?? affinity.entity}`}
                    source={sub ? labelOf(trail, REF.affinity(sub)) : undefined}
                  >
                    <span className="text-ink">{sub ? SUB_SCORE_LABELS[sub] : "Affinity"}</span>{" "}
                    <span className="font-mono">{value2(affinity.value)}</span>
                    {workflow && <span className="text-ink/50"> from {workflow}</span>}
                  </Item>
                );
              })}
            </ul>
          )}
        </Branch>

        <Branch title="Score breakdown" open>
          <ul aria-label="Score breakdown">
            {subs.map((name) => (
              <Breakdown
                key={name}
                name={name}
                value={trail.sub_scores[name] as number}
                weight={trail.weights[name] ?? 0}
                neutral={neutral.has(name)}
                source={labelOf(trail, name)}
                weightSource={labelOf(trail, REF.weight(name))}
              />
            ))}
            <Item source={labelOf(trail, REF.total)}>
              <span className="font-semibold text-ink">Total</span>{" "}
              <span data-testid="trail-total" className="font-mono">
                {trail.total.toFixed(3)}
              </span>
              <span className="text-ink/50"> = Σ weight × sub-score</span>
            </Item>
          </ul>
        </Branch>
      </div>

      <section aria-labelledby="streaming-misses" className="rounded-lg bg-ink p-3 text-paper">
        <h3 id="streaming-misses" className="text-sm font-semibold">
          What streaming data misses
        </h3>
        <p className="mt-0.5 text-xs text-paper/70">
          The crowd&apos;s taste outside music, and where it is, behind this shortlist.
        </p>
        <ul aria-label="Cross-domain contributors" className="mt-2 flex flex-wrap gap-2 text-xs">
          {strip.map(({ list, kind, item }) => (
            <Contributor
              key={`${kind}:${item.entity_id}`}
              kind={kind}
              source={labelOf(trail, REF.profile(list))}
            >
              {item.name}
              {item.affinity != null && <span className="font-mono"> {value2(item.affinity)}</span>}
            </Contributor>
          ))}
          {region && (
            <Contributor kind="Region" source={labelOf(trail, REF.input("region"))}>
              {region.label.replace(/^Venue region:\s*/, "")}
            </Contributor>
          )}
          {trail.sub_scores.local_heat != null && (
            <Contributor kind="Local heat" source={labelOf(trail, "local_heat")}>
              <span className="font-mono">{value2(trail.sub_scores.local_heat)}</span>
            </Contributor>
          )}
        </ul>
        {strip.length === 0 && (
          <p className="mt-2 text-xs text-paper/70">
            Qloo returned no places, films or brands for this crowd.
          </p>
        )}
      </section>
    </div>
  );
}

function Breakdown({
  name,
  value,
  weight,
  neutral,
  source,
  weightSource,
}: {
  name: SubScore;
  value: number;
  weight: number;
  neutral: boolean;
  source: SourceLabel | undefined;
  weightSource: SourceLabel | undefined;
}) {
  return (
    <li data-testid="trail-item" data-sub-score={name} className="border-b border-ink/10 py-1.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className="text-ink">{SUB_SCORE_LABELS[name]}</span>{" "}
          {neutral ? (
            <span data-testid="neutral" className="text-ink/60">
              no Qloo signal, neutral {formatNeutral(value)}
            </span>
          ) : (
            <span className="font-mono">{value2(value)}</span>
          )}
        </div>
        <Provenance source={source} />
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-3 text-xs text-ink/60">
        <span>
          weight <span className="font-mono">{value2(weight)}</span> → adds{" "}
          <span className="font-mono">{(weight * value).toFixed(3)}</span>
        </span>
        <Provenance source={weightSource} />
      </div>
    </li>
  );
}

function Contributor({
  kind,
  source,
  children,
}: {
  kind: string;
  source: SourceLabel | undefined;
  children: React.ReactNode;
}) {
  return (
    <li
      data-testid="trail-item"
      className="flex items-center gap-1.5 rounded-full border border-paper/30 py-0.5 pr-1 pl-2"
    >
      <span className="text-paper/60">{kind}</span>
      <span>{children}</span>
      {/* Paper ground for the badge so its own ink/amber pair stays legible on ink. */}
      <span className="rounded-sm bg-paper">
        <Provenance source={source} />
      </span>
    </li>
  );
}

// --- the slide-over ----------------------------------------------------------------------

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** Keep Tab and Shift+Tab inside `root` (the panel is aria-modal; the page behind must
 * not take focus). Focus that has left the panel comes back to its first control. */
export function trapTab(e: KeyboardEvent, root: HTMLElement | null) {
  if (!root) return;
  const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.closest("details:not([open])") === null || el.tagName === "SUMMARY",
  );
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (!active || !root.contains(active)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

type Load =
  | { name: "loading" }
  | { name: "done"; data: SoundcheckData }
  | { name: "failed"; reason: Reason };

/** Loaded trails by run and act: a trail never changes once its run is stored. */
const loaded = new Map<string, SoundcheckData>();

/** Clears the module cache between tests. */
export function resetSoundcheckCache() {
  loaded.clear();
}

function useTrail(runId: string, entityId: string, attempt: number): Load {
  const key = `${runId} ${entityId}`;
  const [state, setState] = useState<{ key: string; load: Load }>(() => {
    const hit = loaded.get(key);
    return { key, load: hit ? { name: "done", data: hit } : { name: "loading" } };
  });
  useEffect(() => {
    if (loaded.has(key)) return;
    let active = true;
    api.getSoundcheck(runId, entityId).then(
      (data) => {
        loaded.set(key, data);
        if (active) setState({ key, load: { name: "done", data } });
      },
      (err: unknown) => {
        if (active) {
          const reason = err instanceof ApiError ? err.reason : "unavailable";
          setState({ key, load: { name: "failed", reason } });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [key, runId, entityId, attempt]);
  return state.key === key ? state.load : { name: "loading" };
}

function Panel({
  runId,
  entityId,
  name,
  crossDomain,
  onClose,
}: {
  runId: string;
  entityId: string;
  name: string;
  crossDomain?: CrossDomain;
  onClose: () => void;
}) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const [attempt, setAttempt] = useState(0);
  const load = useTrail(runId, entityId, attempt);

  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") trapTab(e, dialog.current);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40">
      <div aria-hidden="true" data-testid="backdrop" onClick={onClose} className="absolute inset-0 bg-ink/40" />
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="soundcheck-panel"
        className="absolute inset-x-0 bottom-0 flex max-h-[85dvh] flex-col rounded-t-2xl border-t border-ink/20 bg-paper sm:inset-y-0 sm:right-0 sm:left-auto sm:max-h-none sm:w-[28rem] sm:rounded-none sm:border-t-0 sm:border-l"
      >
        <header className="flex items-start justify-between gap-3 border-b border-ink/15 p-4">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-ink/60 uppercase">Soundcheck</p>
            <h2 id={titleId} className="mt-0.5 font-semibold break-words text-ink">
              {name}
            </h2>
          </div>
          <button
            ref={close}
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md border border-ink/20 px-2 py-1 text-sm text-ink"
          >
            Close
          </button>
        </header>
        <div className="overflow-y-auto p-4">
          {load.name === "loading" && (
            <p role="status" className="flex items-center gap-2 text-sm text-ink/70">
              <Spinner /> Loading the evidence trail…
            </p>
          )}
          {load.name === "failed" && (
            <div role="alert" className="text-sm">
              <p className="text-ink/80">{reasonText(load.reason)}</p>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="mt-3 rounded-md bg-ink px-3 py-1.5 font-semibold text-paper"
              >
                Try again
              </button>
            </div>
          )}
          {load.name === "done" && <Soundcheck data={load.data} crossDomain={crossDomain} />}
        </div>
      </div>
    </div>
  );
}

/** The per-act trigger: opens the panel and hands focus back when it closes. */
export function SoundcheckButton({
  runId,
  entityId,
  name,
  crossDomain,
}: {
  runId: string;
  entityId: string;
  name: string;
  crossDomain?: CrossDomain;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const onClose = useCallback(() => {
    setOpen(false);
    trigger.current?.focus();
  }, []);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        aria-label={`Soundcheck for ${name}`}
        onClick={() => setOpen(true)}
        className="text-sm font-medium text-ink underline"
      >
        Soundcheck
      </button>
      {open && (
        <Panel
          runId={runId}
          entityId={entityId}
          name={name}
          crossDomain={crossDomain}
          onClose={onClose}
        />
      )}
    </>
  );
}
