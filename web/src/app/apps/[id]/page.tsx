import Link from "next/link";
import { notFound } from "next/navigation";
import AppIcon from "@/components/AppIcon";
import CopyButton from "@/components/CopyButton";
import { getAppById, getAppsWithIds } from "@/lib/data";
import { getLangDict } from "@/lib/lang";
import { safeExternalUrl } from "@/lib/url";

/** Every app is published as a single-app feed next to the master feed. */
function singleAppFeed(bundleIdentifier: string): string {
  return `https://iamsmmh.github.io/OmniSource/feeds/${encodeURIComponent(bundleIdentifier)}.json`;
}

export function generateStaticParams() {
  return getAppsWithIds().map(({ id }) => ({ id }));
}

export default async function AppDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = getAppById(decodeURIComponent(id));
  if (!app) notFound();
  const { dict } = await getLangDict();
  const versions = Array.isArray(app.versions) ? app.versions : [];
  const downloadUrl = safeExternalUrl(app.downloadURL);
  const sourceFeed = app.bundleIdentifier ? singleAppFeed(app.bundleIdentifier) : "";
  return (
    <div className="space-y-6">
      <Link href="/apps" className="text-sm font-semibold text-red-600 hover:underline">
        ← {dict.sections.appsTitle}
      </Link>
      <div className="flex flex-col gap-5 rounded-3xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900 md:flex-row">
        <AppIcon app={app} size={112} className="rounded-3xl text-4xl" />
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-extrabold">{app.name}</h1>
          <p className="text-zinc-600 dark:text-zinc-400">
            {app.developerName} · {app.bundleIdentifier}
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-sm">
            <span className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">
              {dict.common.version} {app.version} · {app.versionDate}
            </span>
            <span className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">{app.category}</span>
            {typeof app.size === "number" && app.size > 0 && (
              <span className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">
                {(app.size / 1048576).toFixed(1)} MB
              </span>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {downloadUrl && (
              <a
                href={downloadUrl}
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center rounded-xl bg-red-600 px-5 font-semibold text-white transition-colors hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
              >
                ⬇ {dict.common.download} (.ipa)
              </a>
            )}
            <a
              href={sourceFeed}
              className="inline-flex min-h-11 items-center rounded-xl border border-zinc-300 px-5 font-semibold transition-colors hover:bg-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {dict.home.addSource}
            </a>
            {sourceFeed && (
              <CopyButton
                value={sourceFeed}
                label={dict.common.copyUrl ?? "Copy source URL"}
                copiedLabel={dict.common.copied ?? "Copied"}
                failedLabel={dict.common.copyFailed ?? "Copy blocked — press and hold to copy"}
              />
            )}
          </div>
        </div>
      </div>
      {app.localizedDescription && (
        <div className="prose-omni rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <p className="whitespace-pre-wrap text-[15px]">{app.localizedDescription}</p>
        </div>
      )}
      {!!versions.length && (
        <div className="rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-3 font-bold">
            {dict.common.version}s ({versions.length})
          </h2>
          <ul className="space-y-2 text-sm">
            {versions.slice(0, 10).map((v, i) => {
              const ver = v as Record<string, unknown>;
              return (
                <li key={i} className="flex flex-wrap items-center gap-2 border-b border-zinc-100 pb-2 dark:border-zinc-800">
                  <span className="font-mono font-semibold">{String(ver.version ?? "?")}</span>
                  <span className="text-zinc-500">{String(ver.date ?? "")}</span>
                  {safeExternalUrl(ver.downloadURL) && (
                    <a
                      href={safeExternalUrl(ver.downloadURL)}
                      rel="noopener noreferrer"
                      className="ml-auto font-semibold text-red-600 hover:underline"
                    >
                      {dict.common.download}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
