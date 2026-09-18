import Link from "next/link";
import { notFound } from "next/navigation";
import AppCard from "@/components/AppCard";
import CopyButton from "@/components/CopyButton";
import { getAppsWithIds, getSourceById, getSources } from "@/lib/data";
import { getLangDict } from "@/lib/lang";
import { safeExternalUrl } from "@/lib/url";

export function generateStaticParams() {
  return getSources().map((s) => ({ id: s.id }));
}

export default async function SourceDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const source = getSourceById(decodeURIComponent(id));
  if (!source) notFound();
  const { dict } = await getLangDict();
  const homepage = safeExternalUrl(source.homepage);
  const sourceUrl = safeExternalUrl(source.sourceURL);
  const slugs = new Set(source.appSlugs ?? []);
  const entries = getAppsWithIds().filter(
    ({ id: appId, app }) => slugs.has(appId) || (source.apps ?? []).includes(app.name),
  );
  return (
    <div className="space-y-6">
      <Link href="/sources" className="text-sm font-semibold text-red-600 hover:underline">
        ← {dict.sections.sourcesTitle}
      </Link>
      <div className="rounded-3xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <h1 className="text-2xl font-extrabold">{source.source}</h1>
        <div className="mt-2 flex flex-wrap gap-2 text-sm">
          {source.status && (
            <span className="rounded-full bg-zinc-100 px-3 py-1 font-semibold dark:bg-zinc-800">{source.status}</span>
          )}
          {typeof source.score === "number" && (
            <span className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">
              {dict.common.score} {source.score}
            </span>
          )}
          <span className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">
            {source.appCount ?? entries.length} {dict.common.apps}
          </span>
        </div>
        <dl className="mt-4 grid gap-2 text-sm md:grid-cols-2">
          {source.publisher && (
            <div><dt className="text-zinc-500">Publisher</dt><dd className="font-semibold">{source.publisher}</dd></div>
          )}
          {source.homepage && (
            <div>
              <dt className="text-zinc-500">Homepage</dt>
              <dd className="min-w-0 break-words">
                {homepage ? (
                  <a href={homepage} rel="noopener noreferrer" className="font-semibold text-red-600 hover:underline">
                    {source.homepage}
                  </a>
                ) : (
                  <span>{source.homepage}</span>
                )}
              </dd>
            </div>
          )}
          {sourceUrl && (
            <div className="md:col-span-2">
              <dt className="text-zinc-500">Feed URL</dt>
              <dd className="break-all font-mono text-xs">{source.sourceURL}</dd>
              <dd className="mt-2">
                <CopyButton
                  value={sourceUrl}
                  label={dict.common.copyUrl ?? "Copy source URL"}
                  copiedLabel={dict.common.copied ?? "Copied"}
                  failedLabel={dict.common.copyFailed ?? "Copy blocked — press and hold to copy"}
                />
              </dd>
            </div>
          )}
        </dl>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {entries.map(({ id: appId, app }) => (
          <AppCard key={appId} id={appId} app={app} />
        ))}
      </div>
      {!entries.length && <p className="text-zinc-500">{dict.common.noResults}</p>}
    </div>
  );
}
