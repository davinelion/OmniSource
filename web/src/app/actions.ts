"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { DEFAULT_LOCALE, isLocale } from "@/i18n/dictionaries";
import { LANG_COOKIE } from "@/lib/lang-client";

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

/**
 * Persist the reader's language choice and re-render the current route with it.
 *
 * The locale lives in a cookie that the root layout reads on the server, so
 * changing it means asking the server for the same page in another language —
 * but that must not look (or feel) like a reload. A Server Action does exactly
 * that: the cookie is written server-side, `revalidatePath` invalidates the
 * router cache for the current route, and Next merges the freshly rendered
 * payload into the existing tree. There is no `router.refresh()`, no document
 * navigation, no history entry, no scroll reset and no white flash — the
 * pathname and query string are not touched at all.
 */
export async function setLocale(locale: string): Promise<{ locale: string }> {
  const next = isLocale(locale) ? locale : DEFAULT_LOCALE;
  const store = await cookies();
  store.set(LANG_COOKIE, next, {
    path: "/",
    maxAge: ONE_YEAR_SECONDS,
    sameSite: "lax",
  });
  // Re-render every route below the root layout, including the one on screen.
  revalidatePath("/", "layout");
  return { locale: next };
}
