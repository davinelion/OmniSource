import Link from "next/link";
import AppCard from "@/components/AppCard";
import SearchBox from "@/components/SearchBox";
import { getAppsWithIds, getSources, getStatus } from "@/lib/data";
import { getLangDict } from "@/lib/lang";

const SOURCE_URL = "https://iamsmmh.github.io/OmniSource/apps.json";

const CLIENTS = [
  { name: "AltStore", href: `altstore://source?url=${SOURCE_URL}` },
  { name: "SideStore", href: `sidestore://source?url=${SOURCE_URL}` },
  { name: "FlareStore", href: `flarestore://source?url=${SOURCE_URL}` },
  { name: "Feather", href: "feather://source/iamsmmh.github.io/OmniSource/apps.json" },
  { name: "ESign", href: `esign://addsource?url=${SOURCE_URL}` },
  { name: "Ksign", href: `ksign://addsource?url=${SOURCE_URL}` },
  { name: "LiveContainer", href: `livecontainer://sources?url=${SOURCE_URL}` },
];

export default async function Home() {
  const { dict } = await getLangDict();
  const apps = getAppsWithIds().slice(0, 8);
  const total = getAppsWithIds().length;
  const sources = getSources().length;
  const status = getStatus() as { pipeline?: { status?: string } };
  const pipeline = String(status.pipeline?.status ?? "unknown");

  return (
    <div className="space-y-10">
      <section className="rounded-3xl bg-gradient-to-br from-red-600 via-orange-600 to-amber-600 p-8 text-white shadow-lg md:p-12">
        <p className="text-sm font-semibold uppercase tracking-widest text-white/80">
          {total} {dict.common.apps} · {sources} {dict.common.sources}
        </p>
        <h1 className="mt-2 max-w-2xl text-3xl font-extrabold tracking-tight md:text-5xl">{dict.home.tagline}</h1>
        <div className="mt-6">
          <SearchBox placeholder={dict.sections.searchPlaceholder} submitLabel={dict.nav.search ?? "Search"} />
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          {CLIENTS.map((c) => (
            <a
              key={c.name}
              href={c.href}
              className="rounded-full bg-white/15 px-4 py-1.5 text-sm font-semibold backdrop-blur hover:bg-white/25"
            >
              ➕ {c.name}
            </a>
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {[
          { t: dict.home.why1t, d: dict.home.why1d },
          { t: dict.home.why2t, d: dict.home.why2d },
          { t: dict.home.why3t, d: dict.home.why3d },
        ].map((f) => (
          <div key={f.t} className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="font-bold">{f.t}</h2>
            <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{f.d}</p>
          </div>
        ))}
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-bold">{dict.sections.appsTitle}</h2>
          <Link href="/apps" className="text-sm font-semibold text-red-600 hover:underline">
            {dict.common.viewAll} →
          </Link>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {apps.map(({ id, app }) => (
            <AppCard key={id} id={id} app={app} />
          ))}
        </div>
      </section>

      <section className="flex flex-wrap items-center gap-3 rounded-2xl border border-zinc-200 bg-white p-5 text-sm dark:border-zinc-800 dark:bg-zinc-900">
        <span className="font-semibold">{dict.home.liveStatus}:</span>
        <span
          className={`rounded-full px-3 py-1 font-semibold ${
            pipeline === "healthy" || pipeline === "online"
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200"
              : "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200"
          }`}
        >
          {pipeline}
        </span>
        <Link href="/status" className="font-semibold text-red-600 hover:underline">
          {dict.nav.status} →
        </Link>
      </section>
    </div>
  );
}
