import type { Metadata } from "next";

import { ProBadge } from "@/src/components/pro";
import { NewVenue } from "./new-venue";

export const metadata: Metadata = {
  title: "New venue",
  description: "Tell Outdraw about your room and the acts you've booked.",
};

export default function NewVenuePage() {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className="mx-auto w-full max-w-2xl px-4 py-8">
        <header className="mb-8">
          <p className="text-sm font-medium text-ink/60">New venue</p>
          <h1 className="mt-1 display text-3xl sm:text-4xl">Set up your venue</h1>
          <p className="mt-2 text-sm text-ink/70">
            Your room and the acts that drew. We match them in Qloo, you confirm, and Outdraw
            builds your crowd profile.
          </p>
          <p className="mt-3 flex items-center gap-2 text-xs text-ink/60">
            <ProBadge /> More than one venue is a Pro feature.
          </p>
        </header>
        <NewVenue />
      </div>
    </main>
  );
}
