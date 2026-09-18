"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

/**
 * Search input.
 *
 * The submit is a soft navigation (no document load), the control is a real
 * form so Enter works and a no-JS reader still gets `GET /search?q=…`, and the
 * label is a label — the previous button announced "⌘K", which is a keyboard
 * hint, not a name. The button keeps its width across states, so submitting
 * never nudges the input.
 */
export default function SearchBox({
  placeholder,
  initial = "",
  submitLabel = "Search",
}: {
  placeholder: string;
  initial?: string;
  submitLabel?: string;
}) {
  const [value, setValue] = useState(initial);
  const router = useRouter();
  const inputId = useId();

  useEffect(() => setValue(initial), [initial]);

  return (
    <form
      role="search"
      action="/search"
      method="get"
      className="flex w-full max-w-xl gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const query = value.trim();
        router.push(query ? `/search?q=${encodeURIComponent(query)}` : "/search");
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        {placeholder}
      </label>
      <input
        id={inputId}
        type="search"
        name="q"
        enterKeyHint="search"
        autoComplete="off"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        className="min-h-11 w-full min-w-0 rounded-xl border border-zinc-300 bg-white px-4 text-zinc-900 shadow-sm outline-none transition-colors focus:border-red-500 focus:ring-2 focus:ring-red-200 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
      <button
        type="submit"
        className="min-h-11 shrink-0 rounded-xl bg-red-600 px-5 font-semibold text-white shadow-sm transition-colors hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
      >
        {submitLabel}
      </button>
    </form>
  );
}
