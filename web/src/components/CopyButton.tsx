"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Copy-to-clipboard with an honest fallback.
 *
 * `navigator.clipboard` needs a secure context and a permission; iOS Safari has
 * shipped it for years but standalone PWAs and in-app WebViews can still refuse.
 * When the modern API is unavailable or rejected, the value is put into a
 * temporary textarea and copied with `execCommand`; if that fails too, the text
 * is left selected and the reader is told to press and hold instead of being
 * shown a false "Copied" confirmation. Nothing here navigates, reloads or
 * changes layout — the confirmation is a state swap inside the button.
 */
export default function CopyButton({
  value,
  label,
  copiedLabel = "Copied",
  failedLabel = "Press and hold to copy",
  className,
}: {
  value: string;
  label: string;
  copiedLabel?: string;
  failedLabel?: string;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const settle = useCallback((next: "copied" | "failed") => {
    setState(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), next === "copied" ? 1800 : 4200);
  }, []);

  const copy = useCallback(() => {
    const text = String(value ?? "");
    const legacy = () => {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "readonly");
      area.setAttribute("aria-hidden", "true");
      area.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
      document.body.appendChild(area);
      const y = window.scrollY;
      let ok = false;
      try {
        area.select();
        if (area.setSelectionRange) area.setSelectionRange(0, text.length);
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      if (window.scrollY !== y) window.scrollTo(0, y);
      if (ok) area.remove();
      settle(ok ? "copied" : "failed");
    };

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => settle("copied"), legacy);
    } else {
      legacy();
    }
  }, [value, settle]);

  return (
    <button
      type="button"
      onClick={copy}
      aria-live="polite"
      className={
        className ??
        "inline-flex min-h-11 items-center gap-2 rounded-xl border border-zinc-300 px-4 font-semibold transition-colors hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 dark:border-zinc-700 dark:hover:bg-zinc-800"
      }
    >
      <span aria-hidden>{state === "copied" ? "✓" : state === "failed" ? "!" : "⧉"}</span>
      {state === "copied" ? copiedLabel : state === "failed" ? failedLabel : label}
    </button>
  );
}
