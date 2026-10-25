"use client";

// A seed run's precomputed reads (T30), handed down by /v/[slug] so the Soundcheck panel,
// its Brief tab and the LLM-only toggle render from them instead of fetching: browsing a
// demo venue sends nothing to /api/runs. Only the seed run is covered; a refinement of it is
// a live run, and its panels fetch as usual.
import { createContext, useContext, type ReactNode } from "react";

import type { BriefOut, CompareOut } from "@/types/agent";
import type { SoundcheckData } from "./soundcheck";

export interface Precomputed {
  runId: string;
  /** Soundcheck data by entity id, for every act on the shortlist. */
  soundcheck: Record<string, SoundcheckData>;
  /** Briefs by entity id: the top acts only, as precompute drafted them. */
  briefs: Record<string, BriefOut>;
  /** The LLM-only list, or null when precompute left it out. */
  compare: CompareOut | null;
}

const Context = createContext<Precomputed | null>(null);

export function PrecomputedProvider({
  value,
  children,
}: {
  value: Precomputed;
  children: ReactNode;
}) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** The precomputed reads when `runId` is the seed run on this page, else null. */
export function usePrecomputed(runId: string): Precomputed | null {
  const value = useContext(Context);
  return value?.runId === runId ? value : null;
}
