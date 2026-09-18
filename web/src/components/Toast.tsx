"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface ToastAction {
  label: string;
  primary?: boolean;
  onSelect?: () => void;
}

export interface ToastState {
  message: string;
  actions?: ToastAction[];
  /** Stays until an action is chosen (the service-worker prompt). */
  persistent?: boolean;
}

/**
 * Fixed-position status toast.
 *
 * It is `position: fixed`, so showing or hiding it never touches layout — the
 * requirement for the "new version" notice is that the page must not move
 * under the reader. `role="status"` + `aria-live="polite"` announce it without
 * stealing focus, and the buttons are real buttons with a 44px touch target.
 */
export default function Toast({
  state,
  onDismiss,
}: {
  state: ToastState | null;
  onDismiss: () => void;
}) {
  const [visible, setVisible] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!state) return;
    /* Flip the enter transition on the next frame instead of synchronously:
       a synchronous setState in an effect cascades a second render pass, and
       the element has to be painted in its offscreen class first for the
       transition to run at all. */
    const frame = requestAnimationFrame(() => setVisible(true));
    if (!state.persistent) {
      timer.current = setTimeout(() => {
        setVisible(false);
        // Let the exit transition finish before the node is unmounted.
        timer.current = setTimeout(onDismiss, 250);
      }, 2600);
    }
    return () => {
      cancelAnimationFrame(frame);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [state, onDismiss]);

  const choose = useCallback(
    (action: ToastAction) => {
      setVisible(false);
      action.onSelect?.();
      setTimeout(onDismiss, 250);
    },
    [onDismiss],
  );

  if (!state) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed inset-x-3 bottom-[max(20px,env(safe-area-inset-bottom,0px))] z-[70] mx-auto flex w-fit max-w-[min(94vw,460px)] flex-wrap items-center justify-center gap-2 rounded-2xl bg-zinc-900 px-4 py-3 text-sm font-semibold text-zinc-50 shadow-xl transition duration-200 motion-reduce:transition-none dark:bg-zinc-100 dark:text-zinc-900 ${
        visible ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-3 opacity-0"
      }`}
    >
      <span className="min-w-0">{state.message}</span>
      {state.actions?.length ? (
        <span className="ml-auto flex items-center gap-2">
          {state.actions.map((action) => (
            <button
              key={action.label}
              type="button"
              onClick={() => choose(action)}
              className={`min-h-11 rounded-xl px-3.5 text-sm font-bold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current ${
                action.primary
                  ? "bg-zinc-50 text-zinc-900 hover:bg-white dark:bg-zinc-900 dark:text-zinc-50 dark:hover:bg-zinc-800"
                  : "border border-zinc-50/40 hover:bg-zinc-50/15 dark:border-zinc-900/40 dark:hover:bg-zinc-900/10"
              }`}
            >
              {action.label}
            </button>
          ))}
        </span>
      ) : null}
    </div>
  );
}
