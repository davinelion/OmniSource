# OmniSource Web

Next.js 16, TypeScript, Tailwind, and an installable PWA for the validated
OmniSource catalog. The root static site remains the GitHub Pages compatibility
path. This application is source-first: it provides catalog browsing, source
health, release/security evidence, operational statistics, and install links.
It does not provide accounts, ratings, reviews, comments, social activity,
marketplace transactions, or personalized recommendations.

## Routes

| Route | Page |
|---|---|
| `/` | Home, client deep-links, catalog summary, pipeline status |
| `/apps` + `/apps/[id]` | validated app catalog and release details |
| `/sources` + `/sources/[id]` | source registry, health, sync, history and reputation |
| `/collections` | committed source/category collections |
| `/categories` | category browser |
| `/developers` | publisher/developer index |
| `/search` | deterministic metadata search |
| `/status` | source/feed/download availability |
| `/security` | hashes, provenance, integrity and gate findings |
| `/statistics` | daily/weekly/monthly operational analytics |
| `/about` | scope, architecture and limitations |
| `/api/v3/*` | cacheable dynamic API (see `docs/API-V3.md`) |

## Data flow

`prebuild` and `predev` run `scripts/sync-data.mjs`. It copies validated
pipeline outputs into `web/`:

- `src/data/*.json` — server-bundled snapshots from `feeds/` and `data/`;
- `public/data/v3/*.json` — client-fetchable API snapshots for search/PWA.

The app builds without reaching localhost or an external database. Production
browser requests remain relative to the deployed origin.

## Develop

```bash
cd web
npm ci
npm run typecheck
npm run lint
npm run dev                 # binds Next's normal development port
npm run build && npm start
```

The live preview environment should start Next with `--hostname 0.0.0.0` and
an allowed preview origin when used in a sandbox.

## API v3

The route handler supports:

- `GET /api/v3/apps?page=2&per_page=20&sort=-versionDate&category=Games&q=youtube`;
- `GET /api/v3/apps/{id}`, `/sources`, `/sources/{id}`;
- `GET /api/v3/search?q=...`, `/status`, `/security`, `/analytics`, `/releases`;
- pagination envelopes, deterministic sorting/filtering, ETags and `304`
  responses, and `Cache-Control: s-maxage=300, stale-while-revalidate=3600`.

## i18n and accessibility

English, Bangla, Arabic, Spanish, French, German, Japanese, and Chinese
locale modules are loaded lazily. Missing strings fall back to English and
Arabic sets RTL document direction. Pages use semantic headings, labels,
keyboard-focusable controls, contrast-aware colors, responsive grids, and
text alternatives for icons/images.

## PWA and deployment

`manifest.webmanifest` plus the service worker provide a cache-first shell and
network-first API snapshots. It is registered only in production. Deploy on
Vercel, Cloudflare, or a self-hosted Node 22 host with `npm run build && npm
start`. Static hosting can use generated `/api/v3/*.json` snapshots; dynamic
query semantics require Node.
