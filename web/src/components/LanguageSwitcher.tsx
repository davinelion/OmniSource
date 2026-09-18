"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { setLocale } from "@/app/actions";
import { LOCALES, type Locale } from "@/i18n/dictionaries";
import { LANG_COOKIE } from "@/lib/lang-client";

const NAMES: Record<Locale, string> = {
  en: "English",
  bn: "বাংলা",
  ar: "العربية",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  ja: "日本語",
  zh: "中文",
};

const RTL: ReadonlySet<string> = new Set(["ar"]);

function applyDocumentLanguage(locale: string) {
  const root = document.documentElement;
  root.lang = locale;
  root.dir = RTL.has(locale) ? "rtl" : "ltr";
}

/**
 * Language switch.
 *
 * What it used to do: write the cookie and call `router.refresh()` — a full
 * re-request of the current route, fired blindly, with no pending state and no
 * fallback. It is replaced by a Server Action (see `app/actions.ts`) which the
 * framework routes as an in-place re-render:
 *
 *   - the pathname and query string are never touched, so no history entry is
 *     added and the reader stays exactly where they were;
 *   - `scroll` is never reset and no document navigation happens;
 *   - the choice is visible immediately (the select, `<html lang>` and `dir`
 *     update before the network round trip finishes);
 *   - `useTransition` gives an honest pending state instead of a frozen
 *     control, and the select is disabled while the swap is in flight so a
 *     double-tap cannot queue two switches;
 *   - if the action cannot run (offline, or a static-export deployment with no
 *     server), the cookie is still written client-side and the reader is told
 *     the choice applies from the next page — never a silent lie, never a
 *     reload.
 */
export default function LanguageSwitcher({ lang, label }: { lang: Locale; label: string }) {
  const [value, setValue] = useState<Locale>(lang);
  const [failed, setFailed] = useState(false);
  const [pending, startTransition] = useTransition();
  const selectRef = useRef<HTMLSelectElement>(null);

  // The server is the source of truth: if a render arrives with another locale
  // (a link, a back/forward, another tab), the control follows it.
  useEffect(() => {
    setValue(lang);
    setFailed(false);
  }, [lang]);

  function change(next: Locale) {
    if (next === value) return;
    setValue(next);
    setFailed(false);
    applyDocumentLanguage(next);
    startTransition(async () => {
      try {
        await setLocale(next);
      } catch {
        // Offline / no server: keep the cookie so the next request is correct.
        try {
          document.cookie = `${LANG_COOKIE}=${next};path=/;max-age=31536000;SameSite=Lax`;
        } catch {
          /* cookies blocked: the selection stays for this view only */
        }
        setFailed(true);
      }
    });
  }

  return (
    <div className="flex items-center gap-2">
      <label className="flex items-center gap-1.5 text-sm text-zinc-600 dark:text-zinc-300">
        <span className="sr-only">{label}</span>
        <span aria-hidden>🌐</span>
        <select
          ref={selectRef}
          value={value}
          disabled={pending}
          aria-busy={pending}
          onChange={(event) => change(event.target.value as Locale)}
          className="min-h-11 rounded-lg border border-zinc-300 bg-transparent px-2 py-1 text-sm disabled:opacity-60 dark:border-zinc-700"
        >
          {LOCALES.map((locale) => (
            <option key={locale} value={locale}>
              {NAMES[locale]}
            </option>
          ))}
        </select>
      </label>
      {/* One polite line, no layout shift: it sits in the flex row and only
          exists while it has something to say. */}
      <span aria-live="polite" className="sr-only">
        {pending ? "…" : failed ? "Language saved for your next visit" : ""}
      </span>
      {pending && (
        <span
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-zinc-400 border-t-transparent motion-reduce:animate-none"
        />
      )}
    </div>
  );
}
