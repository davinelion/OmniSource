import Link from "next/link";
import { getLangDict } from "@/lib/lang";

export default async function NotFound() {
  const { dict } = await getLangDict();
  return (
    <div className="mx-auto max-w-2xl space-y-4 rounded-3xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
      <h1 className="text-xl font-extrabold">404</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        {dict.common.noResults ?? "No results found."}
      </p>
      <Link href="/apps" className="font-semibold text-red-600 hover:underline">
        {dict.sections.appsTitle ?? "Apps"} →
      </Link>
    </div>
  );
}
