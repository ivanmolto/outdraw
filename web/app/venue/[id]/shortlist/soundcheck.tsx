"use client";

// Soundcheck: everything behind one act's score, as a slide-over (a bottom sheet under
// 640px). "Why this act" first, then the trail as an expandable tree (inputs → Qloo calls →
// affinities → score breakdown with weights) and the cross-domain strip. Every item carries
// the agent's own source label (`source_labels`): Qloo data or Draw's interpretation. The UI
// labels nothing itself: the top reason and the profile lists have keys in the trail too.
// A second tab holds the act's booking brief (brief.tsx), and the header its pencil hold.
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Spinner } from "@/src/components/progress";
import { ApiError, api, reasonText, type Reason } from "@/src/lib/api-client";
import { usePrecomputed } from "@/src/lib/precomputed";
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
import { BriefTab, PencilToggle } from "./brief";
import { Provenance } from "./provenance";

const EMPTY: CrossDomain = { places: [], films: [], brands: [] };
/** Items per cross-domain list in the strip. */
const STRIP_ITEMS = 3;

function StatusChip({ status }: { status: QlooStatus }) {
  const style =
    status === "ok"
      ? "bg-stage text-house ring-1 ring-line"
      : status === "partial" || status === "degraded"
        ? "bg-stage text-brand-amber"
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

/** Trail elements carry their label key as `data-ref`, so a brief's evidence link can find
 * them (`tabIndex -1`: focusable from the link, never a Tab stop). */
const refProps = (ref: string | undefined) =>
  ref === undefined ? {} : { "data-ref": ref, tabIndex: -1 };

function Item({
  source,
  evRef,
  children,
}: {
  source: SourceLabel | undefined;
  evRef?: string;
  children: React.ReactNode;
}) {
  return (
    <li
      data-testid="trail-item"
      {...refProps(evRef)}
      className="flex items-start justify-between gap-3 rounded-sm py-1.5 data-[highlight]:bg-brand-amber/25"
    >
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
    <details open={open} className="group border-t border-line py-2">
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
          <Item source={labelOf(trail, REF.topReason)} evRef={REF.topReason}>
            <p className="text-ink/80">{trail.top_reason}</p>
          </Item>
        </ul>
      </section>

      <div role="group" aria-label="Evidence trail">
        <Branch title="Inputs" count={trail.inputs.length} open>
          <ul aria-label="Inputs">
            {trail.inputs.map((input) => (
              <Item
                key={input.ref}
                source={labelOf(trail, REF.input(input.ref))}
                evRef={REF.input(input.ref)}
              >
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
                  <Item
                    key={call.call_id}
                    source={labelOf(trail, REF.call(call.call_id))}
                    evRef={REF.call(call.call_id)}
                  >
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
                    evRef={sub ? REF.affinity(sub) : undefined}
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
            <Item source={labelOf(trail, REF.total)} evRef={REF.total}>
              <span className="font-semibold text-ink">Total</span>{" "}
              <span data-testid="trail-total" className="font-mono">
                {trail.total.toFixed(3)}
              </span>
              <span className="text-ink/50"> = Σ weight × sub-score</span>
            </Item>
          </ul>
        </Branch>
      </div>

      <section aria-labelledby="streaming-misses" className="rounded-card bg-stage p-4 text-house ring-1 ring-line">
        <h3 id="streaming-misses" className="text-sm font-semibold">
          What streaming data misses
        </h3>
        <p className="mt-0.5 text-xs text-house/70">
          The crowd&apos;s taste outside music, and where it is, behind this shortlist.
        </p>
        <ul aria-label="Cross-domain contributors" className="mt-2 flex flex-wrap gap-2 text-xs">
          {strip.map(({ list, kind, item }) => (
            <Contributor
              key={`${kind}:${item.entity_id}`}
              kind={kind}
              source={labelOf(trail, REF.profile(list))}
              evRef={REF.profile(list)}
            >
              {item.name}
              {item.affinity != null && <span className="font-mono"> {value2(item.affinity)}</span>}
            </Contributor>
          ))}
          {region && (
            <Contributor
              kind="Region"
              source={labelOf(trail, REF.input("region"))}
              evRef={REF.input("region")}
            >
              {region.label.replace(/^Venue region:\s*/, "")}
            </Contributor>
          )}
          {trail.sub_scores.local_heat != null && (
            <Contributor kind="Local heat" source={labelOf(trail, "local_heat")} evRef="local_heat">
              <span className="font-mono">{value2(trail.sub_scores.local_heat)}</span>
            </Contributor>
          )}
        </ul>
        {strip.length === 0 && (
          <p className="mt-2 text-xs text-house/70">
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
    <li
      data-testid="trail-item"
      data-sub-score={name}
      {...refProps(name)}
      className="border-b border-line py-1.5 data-[highlight]:bg-brand-amber/25"
    >
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
      <div
        {...refProps(REF.weight(name))}
        className="mt-0.5 flex items-center justify-between gap-3 text-xs text-ink/60 data-[highlight]:bg-brand-amber/25"
      >
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
  evRef,
  children,
}: {
  kind: string;
  source: SourceLabel | undefined;
  evRef: string;
  children: React.ReactNode;
}) {
  return (
    <li
      data-testid="trail-item"
      {...refProps(evRef)}
      className="flex items-center gap-1.5 rounded-full border border-house/30 py-0.5 pr-1 pl-2 data-[highlight]:border-brand-amber"
    >
      <span className="text-house/60">{kind}</span>
      <span>{children}</span>
      {/* Paper ground for the badge so its own ink/amber pair stays legible on ink. */}
      <span className="rounded-sm bg-house">
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

/** Loaded trails by run and act: a trail never changes once its run is stored, and the
 * pencil switch keeps `pencilled` current. */
const loaded = new Map<string, SoundcheckData>();

/** Clears the module cache between tests. */
export function resetSoundcheckCache() {
  loaded.clear();
}

/** A seed run's trail comes precomputed with the page (src/lib/precomputed.tsx): no fetch. */
function useTrail(runId: string, entityId: string, attempt: number): Load {
  const key = `${runId} ${entityId}`;
  const pre = usePrecomputed(runId)?.soundcheck[entityId];
  const [state, setState] = useState<{ key: string; load: Load }>(() => {
    const hit = loaded.get(key);
    return { key, load: hit ? { name: "done", data: hit } : { name: "loading" } };
  });
  useEffect(() => {
    if (pre || loaded.has(key)) return;
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
  }, [key, runId, entityId, attempt, pre]);
  if (pre) return { name: "done", data: pre };
  return state.key === key ? state.load : { name: "loading" };
}

/** How long a trail item stays highlighted after an evidence link lands on it. */
export const HIGHLIGHT_MS = 2000;

type Tab = "evidence" | "brief";
const TABS: [Tab, string][] = [
  ["evidence", "Evidence"],
  ["brief", "Brief"],
];

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
  const tabsId = useId();
  const [attempt, setAttempt] = useState(0);
  const load = useTrail(runId, entityId, attempt);
  /** Seed runs are read-only: no pencil hold to set. */
  const seed = usePrecomputed(runId) !== null;
  const [tab, setTab] = useState<Tab>("evidence");
  /** The trail item an evidence link asked for, focused once the Evidence tab is drawn. */
  const [target, setTarget] = useState<string | null>(null);
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ evidence: null, brief: null });
  /** The highlight's timer, so a second link clears the first highlight at once. Not an
   * effect cleanup: `setTarget(null)` re-runs the effect, and a cleanup there would cancel
   * the timer before it fired. */
  const highlight = useRef<{ el: HTMLElement; timer: ReturnType<typeof setTimeout> } | null>(
    null,
  );

  useEffect(() => {
    if (tab !== "evidence" || target === null || !dialog.current) return;
    const el = Array.from(dialog.current.querySelectorAll<HTMLElement>("[data-ref]")).find(
      (node) => node.dataset.ref === target,
    );
    setTarget(null);
    if (!el) return;
    for (let d = el.closest("details"); d; d = d.parentElement?.closest("details") ?? null) {
      d.open = true;
    }
    el.scrollIntoView?.({ block: "center" });
    el.focus();
    if (highlight.current) {
      clearTimeout(highlight.current.timer);
      delete highlight.current.el.dataset.highlight;
    }
    el.dataset.highlight = "";
    const timer = setTimeout(() => {
      delete el.dataset.highlight;
      highlight.current = null;
    }, HIGHLIGHT_MS);
    highlight.current = { el, timer };
  }, [tab, target]);
  useEffect(
    () => () => {
      if (highlight.current) clearTimeout(highlight.current.timer);
    },
    [],
  );

  const onEvidence = useCallback((ref: string) => {
    setTab("evidence");
    setTarget(ref);
  }, []);

  const onTabKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const next = tab === "evidence" ? "brief" : "evidence";
    setTab(next);
    tabRefs.current[next]?.focus();
  };

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
        <header className="flex items-start justify-between gap-3 border-b border-line p-4">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-ink/60 uppercase">Soundcheck</p>
            <h2 id={titleId} className="mt-0.5 font-semibold break-words text-ink">
              {name}
            </h2>
            {load.name === "done" && seed && (
              <p className="mt-2 text-xs text-ink/60">
                Pencil holds are off on the demo. Refine the shortlist for a live copy.
              </p>
            )}
            {load.name === "done" && !seed && (
              <div className="mt-2">
                <PencilToggle
                  runId={runId}
                  entityId={entityId}
                  initial={load.data.pencilled}
                  initialDate={load.data.pencilDate ?? null}
                  onChange={(pencilled, pencilDate) => {
                    const key = `${runId} ${entityId}`;
                    const hit = loaded.get(key);
                    if (hit) loaded.set(key, { ...hit, pencilled, pencilDate });
                  }}
                />
              </div>
            )}
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
        {load.name === "done" && (
          <div role="tablist" aria-label="Soundcheck views" className="flex gap-1 border-b border-line px-4">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                ref={(el) => {
                  tabRefs.current[key] = el;
                }}
                type="button"
                role="tab"
                id={`${tabsId}-${key}`}
                aria-selected={tab === key}
                aria-controls={`${tabsId}-${key}-panel`}
                tabIndex={tab === key ? 0 : -1}
                onClick={() => setTab(key)}
                onKeyDown={onTabKey}
                className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                  tab === key ? "border-ink text-ink" : "border-transparent text-ink/60"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
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
          {load.name === "done" && (
            <div
              role="tabpanel"
              id={`${tabsId}-${tab}-panel`}
              aria-labelledby={`${tabsId}-${tab}`}
            >
              {tab === "evidence" ? (
                <Soundcheck data={load.data} crossDomain={crossDomain} />
              ) : (
                <BriefTab
                  runId={runId}
                  entityId={entityId}
                  onEvidence={onEvidence}
                  linkable={(ref) => ref in load.data.trail.source_labels}
                />
              )}
            </div>
          )}
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
