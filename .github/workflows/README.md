# Workflows

The automation for OmniSource lives here. Every workflow starts with read-only
permissions and opts in to exactly what it needs, never more.

| Workflow | Trigger | Writes | Purpose |
| --- | --- | --- | --- |
| [`sync.yml`](sync.yml) | schedule (6 h, incremental) · push · manual | feeds, `apps/` pages, README, published root URLs, Pages | Resolve official upstream releases, probe links, rebuild every feed + intelligence documents + app pages, republish the flat/API URLs, deploy |
| [`validate.yml`](validate.yml) | pull request · push · manual | nothing | Parallel offline gate: structural validation + reproducibility, unit test matrix (3.11/3.12), `ruff` + `actionlint` (cached) |
| [`merge.yml`](merge.yml) | `feeds/*.json` changed (PR check · push commit) · manual | `feeds/apps.json` + its published copies | Rebuild the unified master source from modular feeds; on a pull request the same gate runs read-only and fails if `apps.json` is not the merge output |
| [`health-check.yml`](health-check.yml) | schedule (daily) · manual | GitHub Issue | HEAD-probe every download URL + mirror and report broken links via an issue |
| [`verify.yml`](verify.yml) | schedule (weekly, Mon 07:00 UTC) · manual | `feeds/state.json`, `feeds/integrity_report.json` + its `/api/` copies | Stream and hash every published asset (full integrity), record the verdict as `lastFullVerification` and fail on a digest/size mismatch |
| [`build-uyouenhanced.yml`](build-uyouenhanced.yml) | manual | Release asset | Build and publish the uYouEnhanced IPA, then trigger a feed sync |
| [`discovery.yml`](discovery.yml) | schedule (12 h) · manual | `data/discovered_sources.json` | Autonomous discovery (GitHub code search, feed probes, release scans, web catalogs) + validation gate |
| [`monitoring.yml`](monitoring.yml) | schedule (30 min) · manual | `data/status.json`, `data/selfheal_report.json`, `data/mirror_status.json` | Probe sources/downloads, plan self-healing repairs, evaluate mirrors |
| [`security.yml`](security.yml) | push · PR · schedule (daily) · manual | `data/security.json` | SHA/hash audit, duplicate binaries, integrity rollup; fails on critical findings |
| [`analytics.yml`](analytics.yml) | schedule (daily) · manual | `data/analytics_rollup.json` | Daily / weekly / monthly analytics windows |
| [`website.yml`](website.yml) | push · PR · manual | build artifact | Typecheck, lint and production-build the Next.js app in `web/` |
| [`publish.yml`](publish.yml) | push · schedule (daily) · manual | `data/*`, `feeds/clients/*`, `api/v3/*` | Rebuild + validate derived artifacts (canonical DB, ledger, enrichment, reputation, client feeds, API v3) |

## How they fit together

```
catalog.json ──▶ sync.yml (scripts/omnisource.py) ──▶ feeds/*.json
                                     │
                                     ├─▶ repository root mirror  ─▶ GitHub Pages (branch deploy)
                                     └─▶ _site/                  ─▶ GitHub Pages (Actions deploy)
▲ │
│ ▼
build-uyouenhanced.yml   merge.yml (scripts/merge_feeds.py)
(publishes uyouenhanced-v release) ──▶ feeds/apps.json ──▶ scripts/publish_root.py
```

- **`sync.yml`** is the only scheduled writer. `concurrency` prevents two runs from
  writing `feeds/` at once.
- **`merge.yml`** is the safety net for direct edits to `feeds/`: it re-derives
  `feeds/apps.json` from the modular feeds and republishes the root mirror with
  `scripts/publish_root.py`, keeping `feeds/` as the single source of truth.
- **`health-check.yml`** is independent of releases: a broken upstream link is
  reported even when nothing new has shipped.
- **`verify.yml`** is the only job that reads the bytes. `sync.yml` verifies
  metadata on every build (recorded digest format, non-zero size, an installable
  IPA); proving a digest *matches* needs the download, which is too slow for a
  6-hour cadence. It rebuilds from committed state (`--no-sync`) so the verdict
  always describes the feeds that are published today, not a newer upstream view,
  and commits only the two documents it owns.
- **`validate.yml`** guards pull requests. It is read-only and network-free, so it
  is safe on forks. The reproducibility step fails a PR that hand-edits a
  generated feed instead of `catalog.json`.
- **Autonomous pipeline order:** `sync.yml` (upstreams → `feeds/`) →
  `publish.yml` (derived artifacts) → `security.yml` (gate) →
  `website.yml` (modern app build). `discovery.yml`, `monitoring.yml` and
  `analytics.yml` run on their own cadence and only ever write `data/`.

## Hardening rules (all workflows)

- Every multi-line `run:` block starts with `set -euo pipefail`.
- All shell variables are quoted; word-splitting uses arrays (`"${ARGS[@]}"`).
- Workflow inputs / step outputs reach the shell through `env:` only — never
  interpolate `${{ }}` into `run:` scripts (script injection).
- Dispatch inputs are validated (https-only URLs, character allowlists) and
  sanitized before use in artifact names.
- Minimal `permissions`, `concurrency` groups and `timeout-minutes` everywhere.

## After a fresh fork

1. **Settings → Pages** — either source works:
   * *Deploy from a branch* (`main` / root) serves the committed tree. The
     generated URLs are published there by the pipeline, so `/apps.json`,
     `/<slug>.json`, `/api/*`, `sitemap.xml` and `robots.txt` resolve.
   * *GitHub Actions* serves the `_site/` artifact that `sync.yml` uploads.
     `sync.yml` deploys it either way; GitHub simply ignores the artifact
     while the branch mode is selected.
2. **Settings → Actions → Workflow permissions** — leave at the default
   *read repository contents*; each workflow requests more explicitly.
3. No repository secrets are required — `github.token` covers every case.

## Why `/apps.json` lives at the root

GitHub Pages serves *one* of two things: the branch tree, or the uploaded
`_site/` artifact. `/apps.json` — the URL installers register as a source —
must be committed, because a file that is not committed does not exist on the
web. The repository root is otherwise kept clean: `feeds/` is the single
source of truth, and `src/omnisource/site.py` publishes only `/apps.json`,
the `/api/` mirror (+ `.gz` twins), `sitemap.xml`, `robots.txt`, `.nojekyll`
and the homepage statistics there:

* `publish_repo_artifacts()` writes the root surface. The pipeline calls it at
  the end of every run; `scripts/publish_root.py` does the same for manual or
  `merge.yml` runs, and its `--check` mode reports drift.
* `build_site()` assembles the full URL family — including the historical flat
  feeds — into `_site/` (plus minified CSS and optional `.br` twins) for the
  Actions deployment.

Copies are byte-identical to `feeds/` — git stores the shared blob once — and
`scripts/check_reproducible.py` (run by `validate.yml`, both `merge.yml` jobs and
`sync.yml`) fails a rebuild that would change one, so a hand-edited feed can
never be published.
