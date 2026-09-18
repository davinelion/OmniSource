"use client";

/**
 * Last-resort boundary: only reached when the root layout itself throws. It
 * must render its own <html>/<body>, and it must not leak internals.
 */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", lineHeight: 1.6 }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 800 }}>OmniSource could not start</h1>
        <p>Please reload the page. If it keeps happening, the deployed data may be mid-update.</p>
        <button
          type="button"
          onClick={reset}
          style={{ marginTop: "1rem", padding: "0.7rem 1.2rem", borderRadius: 12, border: "1px solid #d4d4d8", background: "transparent", fontWeight: 600 }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
