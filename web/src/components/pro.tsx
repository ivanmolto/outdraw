"use client";

// The plan in the browser (T46): <PlanLoader> reads `GET /api/me` once per page load into a
// small store, <ProBadge> marks a Pro feature for everyone, and <ProGate> hides one from a
// free plan. While the plan is unknown (loading, no loader, or the read failed) everything
// shows, so a slow or missing read never blocks a feature (`shows` in entitlements.ts).
// A store, not a context: a provider wrapping the page would shift when its streamed
// sections are revealed.
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { PRO_FEATURES, shows, type Me, type ProFeature } from "@/src/lib/entitlements";

let current: Me | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Read the plan once; later calls share the first read. */
export function loadPlan(): Promise<void> {
  loading ??= fetch("/api/me", { cache: "no-store" })
    .then((res) => (res.ok ? (res.json() as Promise<Me>) : null))
    .then((data) => {
      if (!data) return;
      current = data;
      for (const listener of listeners) listener();
    })
    .catch(() => {});
  return loading;
}

/** Forget the plan read, for tests. */
export function resetPlan(): void {
  current = null;
  loading = null;
}

/** Rendered once in the layout, beside the page rather than around it. */
export function PlanLoader() {
  useEffect(() => {
    void loadPlan();
  }, []);
  return null;
}

/** The session's plan, or null while it is unknown. */
export function usePlan(): Me | null {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => null,
  );
}

/** Whether to show `feature` for this session's plan. */
export function useShows(feature: ProFeature): boolean {
  return shows(usePlan(), feature);
}

/** Amber on the stage ground, the only place amber goes. `decorative` hides it from the
 * accessibility tree, for a badge inside a control whose name must stay the label. */
export function ProBadge({
  className = "",
  decorative = false,
}: {
  className?: string;
  decorative?: boolean;
}) {
  return (
    <span
      aria-hidden={decorative || undefined}
      className={`inline-flex shrink-0 items-center rounded-full bg-stage px-2 py-0.5 text-[0.65rem] leading-none font-semibold tracking-wide text-brand-amber uppercase ${className}`}
      title="A Pro feature"
    >
      Pro
    </span>
  );
}

/** What a free plan sees where `feature` would be: its name and the badge. */
export function ProNote({ feature }: { feature: ProFeature }) {
  return (
    <p role="note" className="flex items-center gap-2 text-sm text-ink/70">
      <ProBadge />
      {PRO_FEATURES[feature]}: a Pro feature.
    </p>
  );
}

/** `children` for a plan with `feature` (or an unknown plan); for one without, `fallback`
 * when given (null hides it outright), else the <ProNote>. */
export function ProGate({
  feature,
  children,
  fallback,
}: {
  feature: ProFeature;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  if (useShows(feature)) return children;
  return fallback === undefined ? <ProNote feature={feature} /> : fallback;
}
