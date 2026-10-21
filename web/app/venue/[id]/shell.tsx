// Page chrome shared by /venue/[id], its not-found page and its error states.
import Link from "next/link";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div className="mx-auto w-full max-w-3xl px-4 py-8">{children}</div>
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
