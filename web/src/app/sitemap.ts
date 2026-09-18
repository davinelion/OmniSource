import type { MetadataRoute } from "next";
import { getAppsWithIds, getSources } from "@/lib/data";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://iamsmmh.github.io/OmniSource";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  const pages = ["", "/apps", "/sources", "/collections", "/categories", "/developers", "/search", "/status", "/security", "/statistics", "/about"];
  return [
    ...pages.map((path) => ({ url: `${SITE_URL}${path || "/"}`, lastModified: now, priority: path === "" ? 1 : 0.7 })),
    ...getAppsWithIds().map(({ id }) => ({
      url: `${SITE_URL}/apps/${encodeURIComponent(id)}`,
      lastModified: now,
      priority: 0.5,
    })),
    ...getSources().map((source) => ({
      url: `${SITE_URL}/sources/${encodeURIComponent(source.id)}`,
      lastModified: now,
      priority: 0.4,
    })),
  ];
}
