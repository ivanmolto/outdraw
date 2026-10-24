"use client";

// Proof of life for the slow steps (Qloo matching, the crowd profile, the shortlist run): a
// spinner and a seconds counter, so a wait of up to two minutes never looks like a stuck
// button. The counter keeps moving under reduced motion, where the spinner stands still.
import { useEffect, useState } from "react";

/** Whole seconds since mount, ticking once a second. */
export function useElapsed(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);
  return seconds;
}

export const formatElapsed = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-testid="spinner"
      className={`inline-block size-4 shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent motion-reduce:animate-none ${className}`}
    />
  );
}

/** "0:12 elapsed". A `timer` role is not announced on every tick, unlike a live region. */
export function Elapsed({ className = "" }: { className?: string }) {
  const seconds = useElapsed();
  return (
    <span role="timer" aria-label="Time elapsed" className={`font-mono tabular-nums ${className}`}>
      {formatElapsed(seconds)} elapsed
    </span>
  );
}
