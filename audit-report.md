# OmniSource complete repository audit

**Audit date:** 2026-09-12
**Repository:** `iamsmmh/OmniSource`
**Method:** static audit (`python3 scripts/audit.py`), source review, schema/feed
validation, full Python tests, generated-output checks, and web configuration
review. The audit is offline and does not treat a missing external provider as
healthy.

## Scope and inventory

The review covered every tracked and generated boundary:

- **Python:** `src/omnisource/` domain services, repository adapters, providers,
  validators, feed renderers, observability, security, backups, plugins and
  event bus;
- **automation:** all `.github/workflows/*.yml`, schedules, permissions,
  concurrency, shell strictness, secrets, artifact retention, commit/push
  allow-lists and generated-output drift;
- **discovery and data:** GitHub code/repository/release/Pages discovery,
  `data/discovered_sources.json`, quarantine, source registry, intelligence,
  release history, analytics, status, security, backups and schemas;
- **feeds and API:** root compatibility feed, `feeds/`, all five client
  projections, single-app/collection feeds, API v2/v3, static API mirrors,
  pagination/search/cache behavior, and JSON/GZip contracts;
- **web and assets:** static GitHub Pages PWA, HTML routes, CSS/design system,
  icons/screenshots, service worker, and the Next.js 16 TypeScript/Tailwind PWA;
- **documentation and collections:** README, docs, contribution/deployment/
  migration/security/performance/architecture material, committed collections,
  source pages, developer/source views, and analytics surfaces.

## Verification results

| Check | Result | Evidence |
|---|---|---|
| Python unit/integration suite | **340 passed** | `PYTHONPATH=src python3 -m unittest discover -s tests` |
| Python bytecode compilation | **pass** | `python3 -m compileall -q src scripts plugins` |
| Static audit | **pass with non-blocking findings** | `reports/*.md` |
| Internal links and SEO | **0 broken / 0 missing SEO** in 192 scanned HTML pages | `reports/links.md` |
| Structural accessibility | **0 page findings** in 24 scanned pages | `reports/accessibility.md` |
| Static locale parity | **8 locales, 151 keys, 100% coverage** | `reports/localization.md` |
| Catalog/feed validation | **pass** | `scripts/validate.py`, feed validators |
| Security snapshot | **pass**; 64/94 SHA-256, 0 duplicates, 0 failing findings | `security-report.json`, `data/security.json` |
| Generated registry/intelligence | **78 sources, 94 apps, 234 timeline events** | `data/source_registry.json`, `data/*intelligence*.json` |
| Client feed generation | **pass**; five clients, 94 single-app, five collections | `feeds/clients/`, `feeds/single/`, `feeds/collections/` |
| Reproducibility | **checked by CI gate** | `scripts/check_reproducible.py` |
| Modern web build | **pass**; Next.js typecheck, lint and production build | `.github/workflows/website.yml`, `web/README.md` |
| Web dependency audit | **0 vulnerabilities** after the PostCSS override | `web/package.json`, `web/package-lock.json` |

The audit deliberately distinguishes a passing local structural/security test
from live uptime, live provider rate limits, and a production Next.js host;
those require a scheduled environment and are recorded in status artifacts.

## Architecture findings

### Strengths

1. The catalog is projected into multiple client/API formats from shared
   renderers rather than hand-maintained copies.
2. The discovery trust boundary is explicit: structural candidates are held in
   `data/quarantine/`, and `verify_sources.py` must fetch and validate the
   remote feed before an explicit `PUBLISHED` transition.
3. `source_registry.py`, canonical identity, release history, enrichment,
   mirrors, monitoring, analytics, event bus, feature flags, and observability
   are separate domain concerns.
4. `Repository` is a database-neutral port with Memory, JSON, SQLite, and
   DB-API adapters. JSON transactions now stage all mutations and use guarded
   multi-file publication; DB transactions do not commit inside `put/delete`.
5. Generated security reports include hash coverage, provenance and duplicate
   binary detection, with bounded SHA-256/SHA-512 streaming verification.
6. Backward compatibility is retained for `apps.json`, API v2, the static PWA,
   legacy root pages and existing client feed envelopes.

### Scope compliance

- The website provides Home, Apps, Sources, Collections, Categories, Developers,
  Statistics, Status, Security, Search and About surfaces.
- English, Bangla, Arabic, Spanish, French, German, Japanese and Chinese are
  lazy-loaded with English fallback; Arabic is RTL.
- No account, rating, review, comment, social, marketplace, behavioral profile,
  or activity-based recommendation feature is part of the modern surface.
  Legacy trending/compare artifacts remain only where compatibility requires
  them; trending is not linked in the modern navigation and new API docs use
  release/status/intelligence views instead.
- Feature flags are canonical under `config/feature_flags.json` so the root
  publish surface remains limited to the catalog/install feed/sitemap. The
  loader accepts the former root location as a compatibility fallback.

## Automation audit

| Workflow | Trigger | Review result |
|---|---|---|
| `discovery.yml` | 12-hour schedule/manual | repository/feed discovery, validation, verification, explicit publication projection and quarantine commit |
| `validation.yml` / `validate.yml` | PR/push/manual | feed, metadata, source, translation, tests and reproducibility gates |
| `monitoring.yml` / `health-check.yml` | 30-minute/daily | health probes, status, self-healing and issue signal |
| `security.yml` | daily and security changes | report, optional integrity mode and critical fail-closed gate |
| `analytics.yml` | daily/manual | daily/weekly/monthly operational rollups |
| `publish.yml` / `sync.yml` | feed/data changes and schedule | canonical, release, enrichment, registry, intelligence, client feed and API projections |
| `website.yml` / `build-site.yml` | web/site changes | Node web checks and static deployment artifact |
| `backup.yml` | daily/weekly/monthly/manual | verified metadata-only disaster recovery artifact |
| compatibility builders | manual/legacy triggers | uYou/tweak and merge compatibility paths, isolated from the catalog publication gate |

Workflow review found job timeouts, concurrency groups, permissions declarations,
quoted/env-passed inputs, strict shell blocks in modernization workflows, HTTPS
allowlists, scoped GitHub authentication, and narrow generated commit paths.
Remaining legacy workflows have one-line shell steps and artifact-retention
improvements are tracked below.

## Non-blocking findings and fixes still required

1. **P0 — complete a multi-writer transaction failure drill.** The JSON and
   DB-API boundaries are now atomic for normal rollback and tested, but a
   deployed service still needs filesystem/database fault injection and a
   migration/lock policy before multiple writers are enabled.
2. **P0 — provider contract tests.** Add mocked GitHub rate-limit, 403/429,
   malformed JSON, oversized body, redirect, and partial-release fixtures.
3. **P1 — production scale benchmark.** Measure a target deployment at 1,000
   sources/10,000 apps, including memory, wall time, API calls, cache hit rate,
   and monitoring worker saturation.
4. **P1 — hash coverage.** Thirty newest app records lack publisher-provided
   SHA-256 values. They are low-severity findings, not synthesized hashes;
   publisher metadata or opt-in binary verification is needed.
5. **P1 — mirror operations.** The failover implementation is present, but
   `data/mirrors.json` needs real, independently reviewed mirrors and a
   rotation/retention owner.
6. **P1 — workflow hygiene.** Add artifact retention and job summaries to the
   legacy analytics/discovery/monitoring/publish/validate/merge workflows and
   convert remaining one-line shell steps to strict blocks.
7. **P2 — static/Next visual testing.** Add Playwright keyboard, RTL, reduced
   motion, contrast and mobile regression checks.
8. **P2 — generated legacy features.** Retire or clearly label remaining
   static compare/trending/community-compatible artifacts once downstream
   clients have migrated; they must not become new product surfaces.
9. **P2 — durable event outbox and migrations.** JSONL event retention and
   database migration tooling are ready as interfaces, not yet an operated
   service.

## Production-readiness decision

**82/100 — controlled publication ready; multi-writer hosted service not yet
ready.**

The repository is suitable for scheduled GitHub Actions discovery, validation,
security scanning, monitoring, deterministic feed generation, static API
publication, and controlled web deployment. Before offering a multi-writer
service, close the two P0 items, run the external-provider contract suite,
complete a backup restore drill, and measure the declared scale target.

See [docs/FINAL-DELIVERABLES.md](docs/FINAL-DELIVERABLES.md) for the concise
technical-debt register and scorecard, and [docs/OPERATIONS.md](docs/OPERATIONS.md)
for runbooks.
