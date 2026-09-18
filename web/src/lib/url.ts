/**
 * Feed metadata is untrusted input: it comes from third-party source JSON that
 * the pipeline mirrors. React escapes text, but an href is handed to the
 * browser as-is, so a `javascript:` (or `data:`) URL in a feed would execute in
 * the reader's session. Everything that becomes an href goes through here.
 */
export function safeExternalUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const raw = value.trim();
  if (!raw) return undefined;
  if (raw.startsWith("/")) return raw; // same-origin path from our own data
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}
