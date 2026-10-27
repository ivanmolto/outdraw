"use client";

// The run's pencil holds as the page knows them: read with the run (each shortlist entry
// carries its hold), then kept current by the Soundcheck panel's pencil switch, so the
// holds calendar shows a new hold without reading the run again.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/** Pencilled acts by entity ID: the night held (YYYY-MM-DD), or null while it has none. */
export type Holds = Record<string, string | null>;

interface HoldsValue {
  holds: Holds;
  setHold: (entityId: string, pencilled: boolean, date: string | null) => void;
}

const HoldsContext = createContext<HoldsValue | null>(null);

/** The page's holds, or null outside a shortlist (the switch then only saves). */
export function useHolds(): HoldsValue | null {
  return useContext(HoldsContext);
}

export function HoldsProvider({
  runId,
  initial,
  children,
}: {
  runId: string;
  initial: Holds;
  children: ReactNode;
}) {
  const [holds, setHolds] = useState(initial);
  // Another run on screen starts from its own holds.
  const [seen, setSeen] = useState(runId);
  if (seen !== runId) {
    setSeen(runId);
    setHolds(initial);
  }
  const setHold = useCallback((entityId: string, pencilled: boolean, date: string | null) => {
    setHolds((prev) => {
      const next = { ...prev };
      if (pencilled) next[entityId] = date;
      else delete next[entityId];
      return next;
    });
  }, []);
  const value = useMemo(() => ({ holds, setHold }), [holds, setHold]);
  return <HoldsContext.Provider value={value}>{children}</HoldsContext.Provider>;
}
