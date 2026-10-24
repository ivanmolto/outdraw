// Page chrome shared by /venue/[id], its not-found page and its error states.
import Link from "next/link";

/** `wide` makes room for two lists side by side (the shortlist's LLM-only comparison). */
export function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className={`mx-auto w-full px-4 py-8 ${wide ? "max-w-5xl" : "max-w-3xl"}`}>
        {children}
      </div>
    </main>
  );
}

export function Message({ title, body }: { title: string; body: string }) {
  return (
    <div role="alert" className="rounded-lg border border-ink/15 bg-white/60 p-6">
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 text-sm text-ink/70">{body}</p>
      <Link href="/venue/new" className="mt-4 inline-block text-sm font-medium underline">
        Set up a venue
      </Link>
    </div>
  );
}
