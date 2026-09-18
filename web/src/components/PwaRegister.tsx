"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Toast, { type ToastState } from "./Toast";
import { getDictionary } from "@/i18n/dictionaries";

const KEYS = {
  prompted: "omnisource-web-sw-prompted:",
  later: "omnisource-web-sw-later:",
  reloaded: "omnisource-web-sw-reloaded:",
};

function readFlag(key: string): boolean {
  try {
    return sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeFlag(key: string) {
  try {
    sessionStorage.setItem(key, "1");
  } catch {
    /* private mode */
  }
}

/**
 * Registers the service worker and offers updates without ever reloading by
 * itself. `service-worker.js` waits for `omnisource-skip-waiting` (it no longer
 * calls `skipWaiting()` on install), so a deploy cannot yank the page the
 * reader is on: the toast appears, and only "Update" activates the new worker —
 * then the page reloads exactly once, on that click.
 */
export default function PwaRegister({ lang }: { lang: string }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const labels = useRef({ updateReady: "A new version is ready.", update: "Update", later: "Later" });
  const reloading = useRef(false);

  const dismiss = useCallback(() => setToast(null), []);

  const applyUpdate = useCallback((worker: ServiceWorker | null, version: string) => {
    if (reloading.current) return;
    reloading.current = true;
    writeFlag(KEYS.reloaded + version);
    let done = false;
    const reload = () => {
      if (done) return;
      done = true;
      window.location.reload();
    };
    navigator.serviceWorker.addEventListener("controllerchange", reload);
    try {
      worker?.postMessage({ type: "omnisource-skip-waiting" });
    } catch {
      /* the worker may already be active */
    }
    setTimeout(reload, 1400);
  }, []);

  const offer = useCallback(
    (worker: ServiceWorker | null, version: string) => {
      if (readFlag(KEYS.reloaded + version) || readFlag(KEYS.later + version)) return;
      if (readFlag(KEYS.prompted + version)) return;
      writeFlag(KEYS.prompted + version);
      setToast({
        message: labels.current.updateReady,
        persistent: true,
        actions: [
          { label: labels.current.update, primary: true, onSelect: () => applyUpdate(worker, version) },
          { label: labels.current.later, onSelect: () => writeFlag(KEYS.later + version) },
        ],
      });
    },
    [applyUpdate],
  );

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;

    let cancelled = false;
    getDictionary(lang)
      .then((dict) => {
        labels.current = {
          updateReady: dict.common.updateReady ?? labels.current.updateReady,
          update: dict.common.update ?? labels.current.update,
          later: dict.common.later ?? labels.current.later,
        };
      })
      .catch(() => undefined);

    const register = async () => {
      try {
        const registration = await navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" });
        if (cancelled) return;
        const pending = () => registration.waiting ?? registration.installing;
        const hadController = Boolean(navigator.serviceWorker.controller);

        if (registration.waiting && hadController) {
          offer(registration.waiting, registration.waiting.scriptURL);
        }
        registration.addEventListener("updatefound", () => {
          const next = registration.installing;
          if (!next) return;
          next.addEventListener("statechange", () => {
            if (next.state !== "installed") return;
            if (!navigator.serviceWorker.controller) return; // first install, not an update
            offer(registration.waiting ?? next, next.scriptURL);
          });
        });
        navigator.serviceWorker.addEventListener("message", (event: MessageEvent) => {
          const data = event.data as { type?: string; version?: string } | null;
          if (!data || data.type !== "omnisource-sw-updated") return;
          if (!navigator.serviceWorker.controller) return;
          offer(pending(), data.version ?? "unknown");
        });
      } catch {
        /* offline support is progressive: a failed registration must not break the page */
      }
    };

    if (document.readyState === "complete") void register();
    else window.addEventListener("load", register, { once: true });

    return () => {
      cancelled = true;
      window.removeEventListener("load", register);
    };
  }, [lang, offer]);

  return <Toast state={toast} onDismiss={dismiss} />;
}
