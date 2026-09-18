import AppCard from "@/components/AppCard";
import Filters from "@/components/Filters";
import SearchBox from "@/components/SearchBox";
import { getAppsWithIds, getCategories } from "@/lib/data";
import { searchApps } from "@/lib/search";
import { getLangDict } from "@/lib/lang";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; category?: string; sort?: "relevance" | "name" | "updated" }>;
}) {
  const { dict } = await getLangDict();
  const params = await searchParams;
  const q = params.q ?? "";
  const category = params.category ?? "";
  const sort = params.sort ?? "relevance";
  const entries = getAppsWithIds();
  const byId = new Map(entries.map((entry) => [entry.app, entry.id]));
  const result = searchApps(
    entries.map((entry) => entry.app),
    q,
    { category, sort, limit: 48 },
  );
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold">{dict.sections.searchTitle}</h1>
        <p className="text-zinc-600 dark:text-zinc-400">{dict.sections.searchSubtitle}</p>
      </div>
      <SearchBox
        placeholder={dict.sections.searchPlaceholder}
        initial={q}
        submitLabel={dict.nav.search ?? "Search"}
      />
      <Filters
        action="/search"
        submitLabel={dict.common.apply ?? "Apply"}
        selects={[
          {
            name: "category",
            label: dict.common.category ?? "Category",
            value: category,
            options: [
              { value: "", label: `— ${dict.common.category ?? "Category"} —` },
              ...getCategories().map((c) => ({ value: c.name, label: c.name })),
            ],
          },
          {
            name: "sort",
            label: dict.common.sort ?? "Sort",
            value: sort,
            options: [
              { value: "relevance", label: `★ ${dict.common.relevance ?? "Relevance"}` },
              { value: "name", label: "A–Z" },
              { value: "updated", label: dict.common.updated ?? "Updated" },
            ],
          },
        ]}
      />
      <p className="text-sm text-zinc-500">
        {result.total} {dict.common.apps}
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {result.results.map(({ app, score }) => (
          <div key={byId.get(app) ?? app.bundleIdentifier} className="relative min-w-0">
            {q && (
              <span className="absolute -top-2 right-2 z-10 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900 dark:text-amber-200">
                {score.toFixed(1)}
              </span>
            )}
            <AppCard id={byId.get(app) ?? app.bundleIdentifier} app={app} />
          </div>
        ))}
      </div>
      {!result.results.length && <p className="text-zinc-500">{dict.common.noResults}</p>}
    </div>
  );
}
