"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";

export interface FilterSelect {
  name: string;
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
}

/**
 * Catalog filters as a client island.
 *
 * The server-rendered `<form method="get">` it replaces worked with JavaScript
 * off, but every filter change was a full document navigation: white flash,
 * scroll reset, and a new history entry even when only the sort order changed.
 * Here the same query string is built client-side and pushed through the App
 * Router, so the shell, the scroll position and any in-flight input survive.
 * The form element itself stays, so a no-JS reader still gets a native submit.
 */
export default function Filters({
  selects,
  action,
  submitLabel,
}: {
  selects: FilterSelect[];
  action: string;
  submitLabel: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const [pending, startTransition] = useTransition();

  const navigate = useCallback(
    (name: string, value: string) => {
      const params = new URLSearchParams(search?.toString() ?? "");
      if (value) params.set(name, value);
      else params.delete(name);
      const query = params.toString();
      startTransition(() => {
        router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
      });
    },
    [pathname, router, search],
  );

  const current = (select: FilterSelect) => search?.get(select.name) ?? select.value;

  return (
    <form
      action={action}
      method="get"
      aria-busy={pending}
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        // With JavaScript on, keep the soft navigation; the browser submit is
        // the fallback path for readers without it.
        if (typeof window === "undefined") return;
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const params = new URLSearchParams(search?.toString() ?? "");
        data.forEach((value, key) => {
          if (typeof value === "string" && value) params.set(key, value);
          else params.delete(key);
        });
        const query = params.toString();
        startTransition(() => {
          router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
        });
      }}
    >
      {selects.map((select) => (
        <label key={select.name} className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-300">
          <span className="sr-only">{select.label}</span>
          <select
            name={select.name}
            value={current(select)}
            onChange={(event) => navigate(select.name, event.target.value)}
            className="min-h-11 rounded-lg border border-zinc-300 bg-white px-3 text-sm disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900"
          >
            {select.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      ))}
      <button
        type="submit"
        className="min-h-11 rounded-lg bg-zinc-900 px-4 text-sm font-semibold text-white transition-colors hover:bg-zinc-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 dark:bg-zinc-100 dark:text-zinc-900"
      >
        {submitLabel}
      </button>
    </form>
  );
}
