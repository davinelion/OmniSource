# Repository guide

OmniSource separates hand-maintained inputs, generated distribution files, application code, and the website. This keeps routine changes small while preserving every existing source URL.

## Directory map

```text
OmniSource/
├── catalog.json              # Hand-maintained app catalog (source of truth)
├── config/                   # Pipeline settings
├── assets/                   # Source, client, and app icons (PNG + WebP)
│   └── design-system/        # Shared design system (tokens, components, motion, utilities)
├── src/omnisource/           # Python package
│   ├── feeds/                # AltStore, RSS and updates-timeline renderers
│   ├── providers/            # GitHub and external-feed adapters
│   ├── utils/                # Shared helpers
│   ├── site.py               # Publisher: /apps.json + api/ mirror + _site/ (sitemap, robots, API mirror)
│   ├── discovery.py          # Discovery catalog + source index
│   ├── verification.py       # Trust indicators
│   ├── monitor.py            # Source health board + probe history
│   ├── duplicates.py         # Duplicate detection
│   ├── analytics.py          # Repository-derived metrics
│   ├── trending.py           # Phase 1 trending score (recency + availability + featured + verification)
│   ├── related.py            # Phase 2 relationship graph (bundle / category / developer / tags)
│   ├── screenshots.py        # Phase 3 screenshot validation, mirror, thumbnails
│   ├── reputation.py         # Phase 6 source reputation engine
│   ├── download_intel.py     # Phase 7 download intelligence
│   ├── community.py          # Phase 13 community features
│   ├── search_index.py       # Phase 4 Fuse.js-style search index
│   ├── install.py            # Phase 9 install card generator
│   ├── compare.py            # Phase 5 side-by-side comparison engine
│   └── app_pages.py          # Static App-Store-style app detail page generator
├── scripts/                  # Thin CLI entry points over src/omnisource
│   ├── omnisource.py         # Pipeline (sync → health → build)
│   ├── build_site.py         # Assemble _site/ (sitemap, robots, API mirror, gz, minify)
│   ├── publish_root.py       # Publish/verify /apps.json + api/ at the repository root
│   ├── validate.py           # Offline validator
│   ├── validate_jq.sh        # jq-based feed contract checks
│   ├── check_reproducible.py # Proves offline builds are deterministic
│   ├── merge_feeds.py        # Rebuild the unified apps.json from per-app feeds
│   ├── health_check.py       # Standalone download-link probe (HEAD/ranged GET)
│   └── notify.py             # Broadcast notifications (Discord/Telegram/ntfy/webhook)
├── schemas/                  # JSON schemas
├── tests/                    # Unit test suite (121 tests)
├── feeds/                    # Generated canonical feeds, RSS, intelligence docs, state
├── apps/<slug>/index.html    # Generated static app detail pages (design-system styled)
├── index.html                # Immersive home: hero, rails, stats, catalog, timeline
│                             #   (stat values are refreshed by the publisher on every build)
├── apps.json                 # Installable source URL (byte-identical to feeds/apps.json)
├── api/                      # Published machine API surface (+ .gz twins, index.json)
├── sitemap.xml / robots.txt  # Published SEO files (.nojekyll disables Jekyll)
├── compare.html              # Redirect shim → compare/ (preserves ?left=&right=)
├── compare/                  # Side-by-side app comparison (deep-linkable)
├── status/                   # Source Health Center (uptime, latency, sync)
├── analytics/                # Dashboard: trends, updates, verification, categories
├── install/                  # Install center (AltStore/SideStore/Feather/ESign/LiveContainer)
├── search/                   # Full-text search page
├── js/core.js                # OS namespace: theme, ⌘K palette, search engine, PWA
├── js/site.js                # Page renderers (dispatched on body[data-page])
├── manifest.webmanifest      # PWA install manifest (with shortcuts)
├── sw.js                     # Service worker (offline shell + SWR feeds)
├── sdk/                      # Phase 14: client SDKs
│   ├── javascript/           # ESM + CJS, no dependencies
│   └── python/               # Single-file, 3.8+, no dependencies
└── docs/                     # Project documentation
```

## What should be edited?

| Change | Edit | Then run |
| --- | --- | --- |
| Add or update an app | `catalog.json` and, when needed, `assets/` | `make build` |
| Change sync behavior | `src/omnisource/` | `make check` |
| Change the landing page | `index.html` / the root-level pages | `make site` |
| Change pipeline defaults | `config/settings.json` | `make check` |
| Change validation rules | `schemas/` or `src/omnisource/validation.py` | `make check` |

Do not hand-edit `feeds/` or anything the pipeline generates. The sync pipeline owns the whole generated surface; `README.md` is hand-written and the pipeline leaves it alone.

## Generated outputs

Running the pipeline produces, under `feeds/`:

- a per-app AltStore feed (`<slug>.json`) and the master `apps.json`;
- a per-app RSS release feed (`<slug>.xml`) plus the combined `feed.xml`/`rss.xml`;
- `updates.json` — a sanitized "What's new" timeline for the website,
  derived from `state.json` update history and newest versions;
- `health.json` with per-app reachability plus `updatedDaysAgo`/`stale`
  annotations (staleness threshold: `config/settings.json` → `staleAfterDays`);
- the intelligence documents: `discovery.json` (searchable index),
  `sources.json` (upstreams + clients), `verification.json` (trust levels),
  `status.json` (health board + latency history), `duplicates.json`
  (duplicate groups + recommendations) and `analytics.json` (metrics +
  rolling snapshot history);
- Shields.io-compatible badges (`badge-*.json`) and pipeline state
  (`state.json`, never published).

It also renders one static App-Store-style page per app at
`apps/<slug>/index.html` (see `src/omnisource/app_pages.py`).
`src/omnisource/site.py` publishes every public URL from those canonical
files: `/apps.json` and the machine API surface (`api/`, with `.json.gz`
twins and an `api/index.json` manifest) at the repository root, plus
`sitemap.xml` (home + section pages + every app page), `robots.txt`,
`.nojekyll` and the home page's live statistics. `scripts/build_site.py`
assembles the historical flat URL family into `_site/` for the GitHub
Actions deployment. See [`API.md`](API.md) for the endpoint contracts.

## How are generated artifacts published?

Generated files have canonical homes under `feeds/` (JSON/XML) and `apps/`
(pages). The repository root stays clean — only `/apps.json` (the installable
source URL) and the `api/` mirror are committed there:

- **Repository root** — `publish_repo_artifacts()` writes `/apps.json`,
  `/api/*` (with `.gz` twins), `sitemap.xml`, `robots.txt`, `.nojekyll` and
  the home page's stat values, and prunes any root JSON/XML the publisher no
  longer owns. The pipeline refreshes it at the end of every run and
  `scripts/publish_root.py` does the same for manual/`merge.yml` runs
  (`--check` reports drift).
- **GitHub Actions deployment** — `scripts/build_site.py` assembles the full
  URL family (every `<slug>.json`/`<slug>.xml`, `feed.xml`, `rss.xml`, the
  `feeds/` tree, the `api/` mirror and minified CSS) into `_site/`, and
  `sync.yml` deploys it with `actions/deploy-pages`.

Copies are byte-identical to their `feeds/` originals — git stores the shared
blob once, and `scripts/check_reproducible.py` fails a rebuild that would
change one. Existing subscriptions such as the following therefore continue
to work from either mode:

```text
https://iamsmmh.github.io/OmniSource/apps.json
```

## Common commands

```bash
make build       # Sync and regenerate feeds
make check       # Lint, validate, and test
make site        # Assemble the deployable site in _site/
make serve       # Build and preview the site locally
make clean       # Remove local site output
```

The Pages workflow calls `scripts/build_site.py`, so local and production site assembly use one implementation.

## Data flow

```
                    ┌──────────────┐
                    │  catalog.json │  (hand-edited, source of truth)
                    └──────┬───────┘
                           │
              ┌────────────┼─────────────┐
              │            │             │
              ▼            ▼             ▼
        ┌─────────┐  ┌──────────┐  ┌────────────┐
        │  sync   │  │  health  │  │  validate  │
        │ (GH/    │  │  probes  │  │  (offline) │
        │  feeds) │  │          │  │            │
        └────┬────┘  └────┬─────┘  └────────────┘
             │            │
             ▼            ▼
        ┌─────────────────────┐
        │    state.json       │ (versions, health, update history)
        └────────┬────────────┘
                 │
                 ▼
        ┌─────────────────────┐
        │      build stage    │
        │  • apps.json        │  AltStore Source v2 (master)
        │  • <slug>.json      │  Per-app AltStore feeds
        │  • feed.xml, *.xml  │  RSS
        │  • discovery.json   │  Phase 0: search index
        │  • trending.json    │  Phase 1: trending score
        │  • related.json     │  Phase 2: relationship graph
        │  • screenshots.json │  Phase 3: screenshot catalog
        │  • search-index.json│  Phase 4: Fuse.js-compatible index
        │  • compare.json     │  Phase 5: side-by-side pairs
        │  • reputation.json  │  Phase 6: source reputation
        │  • download-intel…  │  Phase 7: availability / latency
        │  • install.json     │  Phase 9: install cards
        │  • community.json   │  Phase 13: community lists
        │  • apps/<slug>/     │  Static app detail pages
        └─────────┬───────────┘
                  │
        ┌─────────┴───────────┐
        ▼                     ▼
   feeds/                 apps/
        │                     │
        └─────────┬───────────┘
                  ▼
       src/omnisource/site.py
        ├── publish_repo_artifacts()  ──▶  repository root
        │      /apps.json · /api/* (+.gz) · sitemap.xml
        │      robots.txt · .nojekyll · homepage stats
        └── build_site()              ──▶  _site/            (Actions deploy)
               full flat URL family + feeds/ + api/ + minified CSS

   The hand-maintained pages at the repository root (index.html, install/,
   js/, sw.js, manifest) render the live experience on top of the published
   feeds/ and api/; assets/design-system/ styles both the website and the
   generated pages. The repository root serves /apps.json + /feeds/ + /api/,
   and the _site/ artifact additionally carries the historical flat URLs —
   both are assembled from the same canonical files.
```

## Generation pipeline (Make targets)

| Target | What it does |
| --- | --- |
| `make build` | Runs the sync + health + build stages, writing everything under `feeds/`, `apps/`, `/apps.json` and the `api/` mirror. |
| `make publish` | Calls `scripts/publish_root.py` to refresh/repair the repository-root surface (`/apps.json`, `api/`, `sitemap.xml`, `robots.txt`). |
| `make site` | Calls `scripts/build_site.py` to assemble the deployable site in `_site/`: the root-level site files, every feed at the flat root and `api/` (with `.gz` twins), `sitemap.xml` + `robots.txt` and the home page's live statistics, fresh on every build. |
| `make check` | Runs the offline validator (`scripts/validate.py`), the jq contract checks (`scripts/validate_jq.sh`) and the unit test suite (`python3 -m unittest discover -s tests`). |
| `make serve` | Builds the site and serves `_site/` on a local port for development. |

The Pages workflow (`.github/workflows/sync.yml`) calls the same scripts so
local development and production are byte-identical.

## Performance budget

* Homepage render target: < 1 second on cold cache.
* Search target: < 50 ms per keystroke (in-memory fuzzy match).
* JSON size: each generated document is < 50 KB except `compare.json`
  (≈ 250 KB for the full pairwise matrix of 22 apps).
* Service worker version is bumped whenever a new feed is added, so
  existing clients pick up the new content on their next page load and
  the SW prompts the user to reload.
