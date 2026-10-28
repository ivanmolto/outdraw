"use client";

// "Export to my booking tool" (T49): the shortlist as a CSV and its pencil holds as a
// plain-text summary, downloaded from what the page holds (the run's acts, the holds as
// the pencil switch left them, the support acts of briefs drafted so far). An export, not
// a live integration, and labelled so. Pro (`exports`). A <details> menu, open without
// JavaScript; while open, a click outside it, Escape or a download closes it.
import { useEffect, useRef } from "react";

import { ProBadge, ProGate } from "@/src/components/pro";
import {
  EXPORT_NOTE,
  exportFileName,
  exportRows,
  holdSummary,
  toCsv,
  type ExportAct,
} from "@/src/lib/export";
import { usePrecomputed } from "@/src/lib/precomputed";
import { draftedSupport } from "./brief";
import { useHolds } from "./holds";

function download(name: string, type: string, text: string) {
  // A BOM so Excel reads the UTF-8 (accents in act names) rather than guessing.
  const url = URL.createObjectURL(new Blob(["﻿", text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** The booker's own date (not UTC: an export just after midnight is dated today here). */
export function today(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const BUTTON = "rounded-md border border-ink/20 px-3 py-1.5 text-sm font-medium text-ink";

function Menu({
  venueName,
  runId,
  acts,
}: {
  venueName: string;
  runId: string;
  acts: ExportAct[];
}) {
  const holds = useHolds()?.holds ?? {};
  const seed = usePrecomputed(runId);
  const rows = () => exportRows(acts, holds, draftedSupport(runId, seed?.briefs ?? null));
  const menu = useRef<HTMLDetailsElement>(null);
  const close = () => menu.current?.removeAttribute("open");
  useEffect(() => {
    const el = menu.current;
    if (!el) return;
    const onPointer = (e: PointerEvent) => {
      if (el.open && e.target instanceof Node && !el.contains(e.target)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && el.open) {
        close();
        el.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  return (
    <details ref={menu} className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-full border border-line px-3 py-1 text-sm font-medium text-ink">
        Export to my booking tool <ProBadge decorative />
      </summary>
      <div
        role="group"
        aria-label="Export"
        className="absolute left-0 z-10 sm:right-0 sm:left-auto mt-2 w-72 max-w-[calc(100vw-2rem)] space-y-3 rounded-card border border-line bg-surface p-4 shadow-sm"
      >
        <p className="text-xs text-ink/70">{EXPORT_NOTE}</p>
        <div className="flex flex-col gap-2">
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              const date = today();
              download(
                exportFileName(venueName, "shortlist", date),
                "text/csv;charset=utf-8",
                toCsv(rows()),
              );
              close();
            }}
          >
            Download shortlist (CSV)
          </button>
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              const date = today();
              download(
                exportFileName(venueName, "holds", date),
                "text/plain;charset=utf-8",
                holdSummary(venueName, rows(), date),
              );
              close();
            }}
          >
            Download pencil-hold summary (text)
          </button>
        </div>
        <p className="text-xs text-ink/60">
          Support acts come from briefs drafted so far; the rest stay blank.
        </p>
      </div>
    </details>
  );
}

export function ExportMenu(props: { venueName: string; runId: string; acts: ExportAct[] }) {
  return (
    <ProGate feature="exports">
      <Menu {...props} />
    </ProGate>
  );
}
