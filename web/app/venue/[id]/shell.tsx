// Page chrome shared by /venue/[id], its not-found page and its notices. API error states
// render src/components/error-state.tsx inside the Shell instead of Message.
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
    <div role="alert" className="rounded-card border border-line bg-surface p-6">
      <h1 className="display text-2xl">{title}</h1>
      <p className="mt-2 text-sm text-ink/70">{body}</p>
      <Link href="/venue/new" className="mt-4 inline-block text-sm font-medium underline">
        Set up a venue
      </Link>
    </div>
  );
}
