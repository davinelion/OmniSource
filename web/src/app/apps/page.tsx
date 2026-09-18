import AppCard from "@/components/AppCard";
import Filters from "@/components/Filters";
import { getAppsWithIds, getCategories } from "@/lib/data";
import { getLangDict } from "@/lib/lang";

export default async function AppsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; sort?: string }>;
}) {
  const { dict } = await getLangDict();
  const params = await searchParams;
  const category = params.category ?? "";
  const sort = params.sort ?? "name";
  const categories = getCategories();
  let entries = getAppsWithIds();
  if (category) entries = entries.filter((e) => (e.app.category ?? "") === category);
  entries = [...entries].sort((a, b) =>
    sort === "updated"
      ? String(b.app.versionDate ?? "").localeCompare(String(a.app.versionDate ?? ""))
      : a.app.name.localeCompare(b.app.name),
  );
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold">{dict.sections.appsTitle}</h1>
        <p className="text-zinc-600 dark:text-zinc-400">{dict.sections.appsSubtitle}</p>
      </div>
      <Filters
        action="/apps"
        submitLabel={dict.common.apply ?? "Apply"}
        selects={[
          {
            name: "category",
            label: dict.common.category ?? "Category",
            value: category,
            options: [
              { value: "", label: `— ${dict.common.category ?? "Category"} —` },
              ...categories.map((c) => ({ value: c.name, label: `${c.name} (${c.count})` })),
            ],
          },
          {
            name: "sort",
            label: dict.common.sort ?? "Sort",
            value: sort,
            options: [
              { value: "name", label: "A–Z" },
              { value: "updated", label: dict.common.updated ?? "Updated" },
            ],
          },
        ]}
      />
      <p className="text-sm text-zinc-500">
        {entries.length} {dict.common.apps}
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {entries.map(({ id, app }) => (
          <AppCard key={id} id={id} app={app} />
        ))}
      </div>
      {!entries.length && <p className="text-zinc-500">{dict.common.noResults}</p>}
    </div>
  );
}
