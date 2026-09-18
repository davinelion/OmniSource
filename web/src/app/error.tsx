"use client";

import { useEffect } from "react";

/**
 * Route-level error boundary.
 *
 * A failure while rendering one page must not take the shell down with it:
 * the navigation, the footer and every other route keep working, and the
 * reader gets a plain-language message and a retry instead of a stack trace
 * (the digest is the only detail shown, and only because it helps support).
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep the detail in the console for developers; never render it.
    console.error("OmniSource route error:", error);
  }, [error]);

  return (
    <div className="mx-auto max-w-2xl space-y-4 rounded-3xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      <h1 className="text-xl font-extrabold">Something went wrong while loading this section</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        The rest of the site is still available. You can retry this section, or go back to the catalog.
      </p>
      {error.digest && <p className="font-mono text-xs text-zinc-400">ref: {error.digest}</p>}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={reset}
          className="min-h-11 rounded-xl bg-red-600 px-5 font-semibold text-white transition-colors hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
        >
          Try again
        </button>
        {/* Deliberately a full document load: the client router state is the
            thing most likely to be broken when this boundary renders, so it is
            replaced rather than reused. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a
          href="/"
          className="inline-flex min-h-11 items-center rounded-xl border border-zinc-300 px-5 font-semibold transition-colors hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          Back to home
        </a>
      </div>
    </div>
  );
}
