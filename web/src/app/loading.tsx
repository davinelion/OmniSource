/**
 * Route-level skeleton. Data is bundled at build time, so this mostly covers
 * the moment a server payload is swapped in — it exists to guarantee that the
 * reader never sees a blank screen, not because it is expected to linger.
 */
export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite" className="space-y-6">
      <span className="sr-only">Loading…</span>
      <div className="h-8 w-56 animate-pulse rounded-lg bg-zinc-200 motion-reduce:animate-none dark:bg-zinc-800" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <div
            key={index}
            className="flex gap-3 rounded-2xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="h-14 w-14 shrink-0 animate-pulse rounded-xl bg-zinc-200 motion-reduce:animate-none dark:bg-zinc-800" />
            <div className="flex-1 space-y-2 py-1">
              <div className="h-4 w-3/4 animate-pulse rounded bg-zinc-200 motion-reduce:animate-none dark:bg-zinc-800" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-zinc-200 motion-reduce:animate-none dark:bg-zinc-800" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
