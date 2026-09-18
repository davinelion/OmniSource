import Link from "next/link";
import AppIcon from "./AppIcon";
import type { AltApp } from "@/lib/data";


/**
 * One catalog card. Every card has the same structure — fixed icon box, three
 * text rows (name, developer · version, category) that truncate instead of
 * wrapping the grid out of shape — so a row of cards lines up regardless of how
 * long the names are. The whole card is a single link with a visible focus ring,
 * and the icon keeps its aspect ratio so nothing jumps while it loads.
 */
export default function AppCard({ id, app }: { id: string; app: AltApp }) {
  return (
    <Link
      href={`/apps/${encodeURIComponent(id)}`}
      className="group flex min-w-0 gap-3 rounded-2xl border border-zinc-200 bg-white p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 motion-reduce:transition-none dark:border-zinc-800 dark:bg-zinc-900"
    >
      <AppIcon app={app} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold text-zinc-900 group-hover:underline dark:text-zinc-100">
          {app.name}
        </span>
        <span className="block truncate text-sm text-zinc-500 dark:text-zinc-400">
          {app.developerName} · v{app.version ?? "?"}
        </span>
        <span className="mt-1 inline-block max-w-full truncate rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {app.category ?? "Utilities"}
        </span>
      </span>
    </Link>
  );
}
