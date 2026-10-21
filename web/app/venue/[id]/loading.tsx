// Skeleton for /venue/[id] while the agent answers: the same header, card grid and button
// footprint as ProfileView, so nothing jumps when the profile streams in.
const CARDS = ["genres", "related-artists", "places", "films", "brands", "demographics"];

function Line({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-ink/10 ${className}`} />;
}

export default function Loading() {
  return (
    <main className="flex-1 bg-paper text-ink">
      <div
        className="mx-auto w-full max-w-3xl px-4 py-8"
        role="status"
        aria-busy="true"
        aria-label="Loading crowd profile"
      >
        <div className="mb-6 space-y-2">
          <Line className="h-4 w-24" />
          <Line className="h-7 w-2/3" />
          <Line className="h-4 w-full max-w-md" />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {CARDS.map((card) => (
            <div
              key={card}
              data-testid="skeleton-card"
              className="space-y-3 rounded-lg border border-ink/15 bg-white/60 p-4"
            >
              <Line className="h-5 w-1/3" />
              <Line className="h-3 w-2/3" />
              {[0, 1, 2, 3].map((i) => (
                <Line key={i} className="h-3 w-full" />
              ))}
            </div>
          ))}
        </div>
        <Line className="mt-8 h-12 w-full sm:w-40" />
      </div>
    </main>
  );
}
