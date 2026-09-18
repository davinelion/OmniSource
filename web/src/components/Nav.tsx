"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import LanguageSwitcher from "./LanguageSwitcher";
import type { Dictionary, Locale } from "@/i18n/dictionaries";

const LINKS: Array<{ href: string; key: string; fallback: string }> = [
  { href: "/", key: "home", fallback: "Home" },
  { href: "/apps", key: "apps", fallback: "Apps" },
  { href: "/sources", key: "sources", fallback: "Sources" },
  { href: "/collections", key: "collections", fallback: "Collections" },
  { href: "/categories", key: "categories", fallback: "Categories" },
  { href: "/developers", key: "developers", fallback: "Developers" },
  { href: "/search", key: "search", fallback: "Search" },
  { href: "/status", key: "status", fallback: "Status" },
  { href: "/security", key: "security", fallback: "Security" },
  { href: "/statistics", key: "statistics", fallback: "Statistics" },
  { href: "/about", key: "about", fallback: "About" },
];

/* The same scroll lock the static site uses, for the same reasons: pinning the
   body out of flow at the current offset is the only lock that both stops iOS
   touch scrolling *and* leaves the header sticky. It is applied only while the
   drawer is open and released — with the offset restored — the moment it
   closes. No wheel/touchmove interception, no preventDefault. */
function lockBody(): number {
  const y = window.scrollY || 0;
  document.body.style.top = `-${y}px`;
  document.body.classList.add("nav-lock");
  return y;
}

function unlockBody(y: number) {
  document.body.classList.remove("nav-lock");
  document.body.style.top = "";
  if (y) window.scrollTo(0, y);
}

export default function Nav({ dict, lang }: { dict: Dictionary; lang: Locale }) {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef(0);

  const label = useCallback(
    (key: string, fallback: string) => dict.nav[key] ?? fallback,
    [dict],
  );

  const close = useCallback(() => {
    setOpen((wasOpen) => {
      if (wasOpen) unlockBody(scrollRef.current);
      return false;
    });
  }, []);

  const openDrawer = useCallback(() => {
    scrollRef.current = lockBody();
    setOpen(true);
  }, []);

  // Navigating (including a back/forward gesture) always closes the drawer, and
  // the lock can never outlive the route it was opened on.
  useEffect(() => {
    setOpen(false);
    unlockBody(0);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        toggleRef.current?.focus({ preventScroll: true });
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = Array.from(
        panel.querySelectorAll<HTMLElement>('a[href], button:not([disabled])'),
      ).filter((node) => node.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (!panel.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    // Focus the first drawer item once the panel is on screen; the transition
    // is transform-only, so this cannot scroll the page.
    const frame = requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLElement>("a[href], button")?.focus({ preventScroll: true });
    });
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      cancelAnimationFrame(frame);
    };
  }, [open, close]);

  // The lock must never survive the page being hidden (iOS bfcache) or a
  // resize back into the desktop layout.
  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth >= 1024 && open) close();
    };
    const onHide = () => {
      document.body.classList.remove("nav-lock");
      document.body.style.top = "";
    };
    window.addEventListener("resize", onResize, { passive: true });
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pagehide", onHide);
      onHide();
    };
  }, [open, close]);

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-zinc-900 dark:focus:bg-zinc-900 dark:focus:text-zinc-50"
      >
        {dict.common.skipToContent ?? "Skip to content"}
      </a>

      <header className="sticky top-0 z-40 border-b border-zinc-200 bg-white/85 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/85">
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 px-4 py-2">
          <Link
            href="/"
            className="flex min-w-0 items-center gap-2 font-extrabold tracking-tight text-zinc-900 dark:text-white"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-red-500 to-orange-500 text-lg text-white">
              O
            </span>
            <span className="truncate">OmniSource</span>
          </Link>

          <nav aria-label="Primary" className="ml-auto hidden min-w-0 flex-1 items-center justify-end gap-x-3 text-sm lg:flex">
            {LINKS.map((link) => {
              const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded px-1.5 py-1 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-white ${
                    active ? "font-semibold text-zinc-900 dark:text-white" : "text-zinc-600 dark:text-zinc-300"
                  }`}
                >
                  {label(link.key, link.fallback)}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex shrink-0 items-center gap-2 lg:ml-3">
            <LanguageSwitcher lang={lang} label={label("language", "Language")} />
            <button
              ref={toggleRef}
              type="button"
              onClick={() => (open ? close() : openDrawer())}
              aria-expanded={open}
              aria-controls={panelId}
              aria-label={open ? label("closeMenu", "Close menu") : label("openMenu", "Open menu")}
              className="flex h-11 w-11 items-center justify-center rounded-xl border border-zinc-200 text-zinc-700 transition-colors hover:bg-zinc-100 active:bg-zinc-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500 lg:hidden dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                {open ? <path d="m6 6 12 12M18 6 6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
              </svg>
            </button>
          </div>
        </div>
      </header>

      {/* Backdrop and drawer are siblings of the header, not children: a
          blurred sticky header becomes the containing block for fixed
          descendants, which is exactly how the old drawer ended up riding off
          the top of the screen with it. */}
      <div
        aria-hidden="true"
        onClick={close}
        className={`fixed inset-0 z-40 bg-zinc-900/30 backdrop-blur-sm transition-opacity duration-200 motion-reduce:transition-none lg:hidden ${
          open ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <div
        id={panelId}
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label("menu", "Menu")}
        className={`fixed inset-x-3 top-[calc(env(safe-area-inset-top,0px)+64px)] z-50 max-h-[min(80vh,640px)] origin-top overflow-y-auto overscroll-contain rounded-2xl border border-zinc-200 bg-white p-2 shadow-2xl transition duration-200 ease-out motion-reduce:transition-none lg:hidden dark:border-zinc-800 dark:bg-zinc-900 ${
          open ? "pointer-events-auto visible translate-y-0 opacity-100" : "pointer-events-none invisible -translate-y-2 opacity-0"
        }`}
      >
        <nav aria-label={label("menu", "Menu")} className="flex flex-col">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                onClick={close}
                className={`flex min-h-12 items-center rounded-xl px-4 text-[15px] transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                  active ? "bg-zinc-100 font-semibold dark:bg-zinc-800" : "text-zinc-700 dark:text-zinc-200"
                }`}
              >
                {label(link.key, link.fallback)}
              </Link>
            );
          })}
        </nav>
      </div>
    </>
  );
}
