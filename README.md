<div align="center">

<img src="assets/brand/hero.svg" width="100%" alt="OmniSource — an automated, source-first aggregation platform for iOS sideloading clients">

<br>

[![Validate](https://github.com/iamsmmh/OmniSource/actions/workflows/validate.yml/badge.svg)](https://github.com/iamsmmh/OmniSource/actions/workflows/validate.yml)
[![Security](https://github.com/iamsmmh/OmniSource/actions/workflows/security.yml/badge.svg)](https://github.com/iamsmmh/OmniSource/actions/workflows/security.yml)
[![Website](https://img.shields.io/website?url=https%3A%2F%2Fiamsmmh.github.io%2FOmniSource%2F)](https://iamsmmh.github.io/OmniSource/)
[![Apps](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-apps.json)](https://iamsmmh.github.io/OmniSource/)
[![Verified](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-verified.json)](https://iamsmmh.github.io/OmniSource/status/)
[![Download health](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-health.json)](https://iamsmmh.github.io/OmniSource/status/)
[![Last sync](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-sync.json)](https://iamsmmh.github.io/OmniSource/status/)
[![Source spec](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-version.json)](https://iamsmmh.github.io/OmniSource/apps.json)
[![License](https://img.shields.io/github/license/iamsmmh/OmniSource)](LICENSE)

<br>

<a href="https://iamsmmh.github.io/OmniSource/install/"><img src="assets/brand/btn-add-source.svg" alt="Add the OmniSource source"></a>

<br>

<a href="https://iamsmmh.github.io/OmniSource/install/?add=altstore"><img src="assets/brand/btn-add-altstore.svg" alt="Add to AltStore" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=sidestore"><img src="assets/brand/btn-add-sidestore.svg" alt="Add to SideStore" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=feather"><img src="assets/brand/btn-add-feather.svg" alt="Add to Feather" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=esign"><img src="assets/brand/btn-add-esign.svg" alt="Add to ESign" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=livecontainer"><img src="assets/brand/btn-add-livecontainer.svg" alt="Add to LiveContainer" height="46"></a>

<br>

<a href="https://iamsmmh.github.io/OmniSource/"><img src="assets/brand/btn-website.svg" alt="Website"></a>
<a href="docs/API-V3.md"><img src="assets/brand/btn-api.svg" alt="API v3"></a>
<a href="docs/"><img src="assets/brand/btn-docs.svg" alt="Documentation"></a>
<a href="CONTRIBUTING.md"><img src="assets/brand/btn-contribute.svg" alt="Contributing"></a>
<a href="web/"><img src="assets/brand/btn-webapp.svg" alt="Web app"></a>

**On iPhone?** Tap your client above — the source opens in its Add Source
screen automatically. On desktop, the same link takes you to the
[installation center](https://iamsmmh.github.io/OmniSource/install/),
where you can scan a QR code with your phone.

</div>

<img src="assets/brand/divider.svg" width="100%" alt="">

## 🌐 Website

OmniSource is live on the web — **https://iamsmmh.github.io/OmniSource/**

Browse the full catalog, search for apps, check source health and add the
source to your client, all from the browser. No app or account needed.

| Quick links | What's there |
|---|---|
| [🏠 Website home](https://iamsmmh.github.io/OmniSource/) | trending apps, collections and stats |
| [⚡ Install center](https://iamsmmh.github.io/OmniSource/install/) | one-tap add links, QR codes and per-client guides |
| [📱 App explorer](https://iamsmmh.github.io/OmniSource/#catalog) | filter and search every app in the catalog |
| [🔎 Source explorer](https://iamsmmh.github.io/OmniSource/sources/) | upstreams, maintainers, cadence and reputation |
| [🧾 Collections](https://iamsmmh.github.io/OmniSource/collections/) | curated shelves — YouTube, music, emulators, utilities |
| [🩺 Status](https://iamsmmh.github.io/OmniSource/status/) | live health, verification and sync reports |
| [🔌 API](https://iamsmmh.github.io/OmniSource/api/index.json) | machine-readable feeds and versioned endpoints |

## ✦ What OmniSource does

OmniSource discovers and aggregates public iOS sources, validates their feed
and release metadata, tracks release history, enriches records, verifies
provenance and hashes, monitors availability, and publishes deterministic
feeds and machine-readable health reports — **fully automated end to end**.

Every entry is resolved from the app's own official upstream project — GitHub
Releases or the developer's official feed — and each download link is
re-probed and re-verified automatically before publication.

It is deliberately **not** a social or marketplace product. There are no
accounts, comments, reviews, ratings, behavioral profiles, personalized
recommendation feeds, or marketplace transactions. Catalog collections are
committed, transparent source groupings; analytics describe repository and
release observations rather than user activity.

| 🔗 One feed, five clients | 🧾 Provenance & hashes | 🤖 Automated end to end |
|---|---|---|
| AltStore Source v2 served to AltStore, SideStore, Feather, ESign and LiveContainer | Official-upstream resolution with SHA-256/SHA-512 coverage and published checksums | Discovery, validation, enrichment and publication run unattended on GitHub Actions |
| **🩺 Self-healing** | **🔍 Transparent by construction** | **🔒 No accounts, no tracking** |
| 30-minute probes, quarantine for invalid sources and mirror failover | A hand-maintained `catalog.json` plus append-only ledgers; generated files are never edited | No sign-in, no analytics on users, no marketplace — only metadata and links |

> 🧊 The hero artwork, pill buttons and dividers are generated from the
> repo's own **Liquid Glass · Fluid Edition** tokens in
> [`assets/design-system/`](assets/design-system/) — the same translucent
> materials, chromatic refraction and aurora diffusion that power the website.

## 📑 Contents

- [Website](#-website)
- [Add the source](#-add-the-source)
- [Catalog preview](#-catalog-preview)
- [Architecture](#-architecture)
- [Repository layout](#-repository-layout)
- [Local development](#-local-development)
- [API & website](#-api--website)
- [Automation](#️-automation)
- [Documentation](#-documentation)
- [Contributing](#-contributing)
- [License](#️-license)

## 🔗 Add the source

The compatibility feed — one URL that every supported client accepts:

```text
https://iamsmmh.github.io/OmniSource/apps.json
```

**On your iPhone**, tap your client in the table (or the badge buttons at the
top of this file). The link first opens the installation center, which hands
off directly to the client's Add Source screen. If the client is not installed
yet, that page also gives you the feed URL and a QR code to add it manually.

| Client | One-tap add | Manual path |
|---|---|---|
| <img src="assets/AltStore.webp" width="20" height="20"> AltStore | **[Add to AltStore](https://iamsmmh.github.io/OmniSource/install/?add=altstore)** | Settings → Sources → + |
| <img src="assets/SideStore.webp" width="20" height="20"> SideStore | **[Add to SideStore](https://iamsmmh.github.io/OmniSource/install/?add=sidestore)** | Settings → Sources → + |
| <img src="assets/Feather.webp" width="20" height="20"> Feather | **[Add to Feather](https://iamsmmh.github.io/OmniSource/install/?add=feather)** | Sources → Add |
| <img src="assets/E-Sign.webp" width="20" height="20"> ESign | **[Add to ESign](https://iamsmmh.github.io/OmniSource/install/?add=esign)** | Sources → + |
| <img src="assets/LiveContainer.webp" width="20" height="20"> LiveContainer | **[Add to LiveContainer](https://iamsmmh.github.io/OmniSource/install/?add=livecontainer)** | Settings → Sources |

> GitHub and most chat apps strip app-specific links (`altstore://`,
> `feather://`, …), which is why the buttons above point at the HTTPS
> installation center rather than at the client scheme directly. That page
> performs the one-tap hand-off for you, from anywhere the README is viewed.

Client-specific feeds are published at
`feeds/clients/{altstore,sidestore,feather,esign,livecontainer}.json`,
single-app feeds at `feeds/single/<slug>.json` (also reachable as
`feeds/<slug>.json` — useful when a client cannot co-install apps sharing a
bundle ID), and collection feeds at `feeds/collections/<slug>.json`. All of
them are generated and validated before publication. Release watchers can
subscribe to the [RSS feed](https://iamsmmh.github.io/OmniSource/feeds/feed.xml).

The same one-tap hand-off works for a single app by adding `&app=<slug>` —
for example, on iPhone, [add Delta straight to AltStore](https://iamsmmh.github.io/OmniSource/install/?add=altstore&app=delta)
or [add Feather's source for one app](https://iamsmmh.github.io/OmniSource/install/?add=feather&app=delta).
This is the workaround for clients that cannot co-install apps sharing a
bundle ID.

## 🗂 Catalog preview

A look inside the validated catalog — every icon opens its published detail
page with release history, hashes, provenance and install links. Browse the
full catalog on the [website](https://iamsmmh.github.io/OmniSource/); the live
**apps** badge at the top of this file always shows the current count.

| | | | | | |
|:--:|:--:|:--:|:--:|:--:|:--:|
| [<img src="assets/Delta.webp" width="56" alt="Delta">](https://iamsmmh.github.io/OmniSource/apps/delta/) | [<img src="assets/PPSSPP.webp" width="56" alt="PPSSPP">](https://iamsmmh.github.io/OmniSource/apps/ppsspp/) | [<img src="assets/DolphiniOS.webp" width="56" alt="DolphiniOS">](https://iamsmmh.github.io/OmniSource/apps/dolphinish/) | [<img src="assets/MAME4iOS.webp" width="56" alt="MAME4iOS">](https://iamsmmh.github.io/OmniSource/apps/mame4ios/) | [<img src="assets/Provenance.webp" width="56" alt="Provenance">](https://iamsmmh.github.io/OmniSource/apps/provenance/) | [<img src="assets/UTM.webp" width="56" alt="UTM">](https://iamsmmh.github.io/OmniSource/apps/utm/) |
| [<img src="assets/iTorrent.webp" width="56" alt="iTorrent">](https://iamsmmh.github.io/OmniSource/apps/itorrent/) | [<img src="assets/LiveContainer.webp" width="56" alt="LiveContainer">](https://iamsmmh.github.io/OmniSource/apps/livecontainer/) | [<img src="assets/Feather.webp" width="56" alt="Feather">](https://iamsmmh.github.io/OmniSource/apps/feather/) | [<img src="assets/Apollo.webp" width="56" alt="Apollo">](https://iamsmmh.github.io/OmniSource/apps/apollo/) | [<img src="assets/SpotiFLAC.webp" width="56" alt="SpotiFLAC Mobile">](https://iamsmmh.github.io/OmniSource/apps/spotiflac/) | [<img src="assets/YouTube.webp" width="56" alt="YouTubePlus">](https://iamsmmh.github.io/OmniSource/apps/ytlite/) |
| [<img src="assets/Instagram.webp" width="56" alt="iNKillerPlus">](https://iamsmmh.github.io/OmniSource/apps/inkillerplus/) | [<img src="assets/TikTok.webp" width="56" alt="TTKillerPlus">](https://iamsmmh.github.io/OmniSource/apps/ttkillerplus/) | [<img src="assets/Telegram.webp" width="56" alt="Telegram MxGram">](https://iamsmmh.github.io/OmniSource/apps/telegram-mxgram/) | [<img src="assets/Discord.webp" width="56" alt="RainTweak">](https://iamsmmh.github.io/OmniSource/apps/raintweak/) | [<img src="assets/Reddit.webp" width="56" alt="RedditFilter">](https://iamsmmh.github.io/OmniSource/apps/redditfilter/) | [<img src="assets/SoundCloud.webp" width="56" alt="NexaSC">](https://iamsmmh.github.io/OmniSource/apps/nexasc/) |

<img src="assets/brand/divider.svg" width="100%" alt="">

## 🏗 Architecture

```mermaid
flowchart LR
    A[GitHub search and feed probes] --> B[data/discovered_sources.json]
    B --> C[Validation engine]
    C -->|invalid| Q[data/quarantine/]
    C --> D[Verification and registry]
    D --> E[Canonical app and release ledgers]
    E --> F[Metadata enrichment]
    F --> G[Reputation and security gates]
    G --> H[Incremental feed generation]
    H --> I[AltStore-family feeds]
    H --> J[Static API and Next.js web]
    K[30-minute monitoring] --> L[data/status.json]
    L --> M[Self-healing and mirror failover]
    M --> H
```

The source of truth is the hand-maintained `catalog.json` plus append-only
operational records. Generated feeds and pages are never hand-edited.

| Concern | Implementation | Durable output |
|---|---|---|
| Discovery | `scripts/discovery/` (12-hour GitHub/feed/repository passes) | `data/discovered_sources.json` |
| Quarantine | `src/omnisource/quarantine.py` | `data/quarantine/sources.json` |
| Validation | feed, source, release, metadata validators | rejected candidates never publish |
| Deduplication | bundle/app/repository/release/binary identity keys | `data/canonical_apps.json` |
| Registry | lifecycle, classification, history, health and reputation | `data/source_registry.json` |
| Release tracking | append-only version ledger and rollback planning | `data/release_history.json` |
| Enrichment | normalized developer, descriptions, icons, screenshots and notes | `data/enriched_apps.json` |
| Security | SHA-256/SHA-512 coverage, optional streamed binary verification, provenance | `data/security.json`, `security-report.json` |
| Monitoring | source/feed/download probes and state transitions | `data/status.json` |
| Intelligence | source growth/decline, app churn, cadence and timelines | `data/source_intelligence.json` |
| Search | fuzzy, developer/source/category/tag ranking | `feeds/search-index.json`, API v3 |
| Persistence | JSON adapter now; SQLite and generic DB-API adapters ready | `src/omnisource/repository.py` |
| Extensibility | typed event bus and reviewed plugin ports | `src/omnisource/events.py`, `plugins/` |

## 📦 Repository layout

```text
catalog.json                  hand-maintained application/source declarations
src/omnisource/                domain services, providers, pipeline and adapters
scripts/discovery/             scheduled discovery commands
scripts/validation/            publication gates
scripts/monitoring/            health and status commands
scripts/registry/              source registry build
scripts/intelligence/          source/package intelligence build
scripts/security/              security report and optional binary scan
scripts/backup/                verified disaster-recovery snapshots
feeds/                         canonical generated client and intelligence feeds
api/                           backward-compatible static API mirrors
web/                           Next.js 15 + TypeScript + Tailwind PWA
js/                            zero-dependency GitHub Pages frontend
schemas/                       machine-readable contracts
data/quarantine/              isolated untrusted discovery records
docs/                          architecture, API and operations guides
assets/design-system/          Liquid Glass design tokens (theme, motion, glass)
assets/brand/                  hero, dividers and the pill buttons used above
```

## 🛠 Local development

Runtime code uses Python's standard library. Python 3.11 or newer is required.
Node 22 is used for the modern web application.

```bash
# Run from the repository root
export PYTHONPATH=src
python3 -m unittest discover -s tests
python3 scripts/validate.py
python3 scripts/validation/validate_feed.py feeds/apps.json --allow-duplicates
python3 scripts/validation/validate_metadata.py feeds/apps.json
python3 scripts/audit.py

# Offline deterministic pipeline
make build             # sync configured upstreams and rebuild feeds
make derived           # canonical, history, enrichment, registry and API
make monitoring        # status, self-healing and mirrors (offline-safe)
make security          # validation plus security report
make check             # lint/validation/tests/smoke checks when tools are installed
make serve             # build and serve the static site on 0.0.0.0:8000
```

Do not put IPA payloads, tokens, or HTTP caches in Git. Use `.env` locally
(`.env.example` documents supported overrides); credentials are scoped to
provider API hosts and are never attached to download probes.

## 🌐 API & website

The backward-compatible feed/API surface remains available:

- `apps.json` — AltStore Source v2 feed;
- `api/*.json` — flat machine-readable snapshots and gzip twins;
- `api/v2/` — legacy delta-friendly endpoints;
- `api/v3/` — versioned pagination, filtering, sorting, fuzzy search, ETags,
  cache headers, sources, releases, status, security and analytics;
- `feeds/install.json` — machine-readable per-client deep links and setup
  steps, which the [installation center](https://iamsmmh.github.io/OmniSource/install/)
  renders (the README's add buttons link there with `?add=<client>`).

The modern app in `web/` provides Home, Apps, Sources, Collections, Categories,
Developers, Statistics, Status, Security, Search, About, source timelines,
source detail pages, and app release/security detail pages. It supports English,
Bangla, Arabic, Spanish, French, German, Japanese, and Chinese with lazy
loading and English fallback. The static PWA remains the GitHub Pages safety
path while `web/` can run on any Node host.

```bash
cd web
npm ci
npm run typecheck
npm run lint
npm run build
npm run dev
```

See [docs/API-V3.md](docs/API-V3.md) for the contract and
[web/README.md](web/README.md) for deployment.

<img src="assets/brand/divider.svg" width="100%" alt="">

## ⚙️ Automation

| Workflow | Schedule | Purpose |
|---|---:|---|
| `discovery.yml` | every 12 hours | discover GitHub, GitLab, Codeberg, Forgejo and feed candidates; isolate invalid sources |
| `sync.yml` | every 6 hours | resolve releases, build and publish feeds |
| `validation.yml` / `validate.yml` | PR/push | structural, metadata, translation and regression gates |
| `monitoring.yml` | every 30 minutes | probes, status, self-healing plans and mirrors |
| `security.yml` | daily + supply-chain changes | security report and fail-closed critical gate |
| `analytics.yml` | daily | daily/weekly/monthly rollups |
| `publish.yml` | after feed changes + daily | canonical registry, intelligence, client feeds and API |
| `backup.yml` | daily/weekly/monthly | verified metadata-only recovery artifacts |
| `website.yml` | web changes | Next.js typecheck, lint and production build |

Every shell block uses strict mode, quoted variables, HTTPS allowlists, least
privilege permissions, concurrency controls, and an explicit commit allowlist.
Untrusted workflow inputs are passed through environment variables and
validated before use.

## 📚 Documentation

- [Installation center](https://iamsmmh.github.io/OmniSource/install/) — one-tap links, QR codes, per-client guides
- [Complete audit](audit-report.md)
- [Architecture and diagram](docs/ARCHITECTURE.md)
- [Discovery and quarantine](docs/DISCOVERY.md)
- [API documentation](docs/API.md) · [API v3](docs/API-V3.md)
- [Operations and self-healing](docs/OPERATIONS.md)
- [Security report](docs/SECURITY-REPORT.md)
- [Performance report](docs/PERFORMANCE-REPORT.md)
- [Migration guide](docs/MIGRATION.md)
- [Deployment guide](docs/DEPLOYMENT-GUIDE.md)
- [Final deliverables and known limitations](docs/FINAL-DELIVERABLES.md)
- [Contributing](CONTRIBUTING.md)

## 🤝 Contributing

Edit `catalog.json`, schemas, source modules, or documentation — not generated
feeds, API copies, app pages, or operational snapshots. Add a test for every
new validator or provider. Run `make check` and the relevant workflow command
before opening a pull request. New source formats should use a plugin rather
than modifying core dispatch code.

<img src="assets/brand/divider.svg" width="100%" alt="">

## ⚖️ License

OmniSource is GPL-3.0. App names, icons, trademarks, and upstream releases
belong to their respective owners. OmniSource aggregates metadata and links to
public publishers; it does not claim ownership of upstream binaries.

<div align="center">

<a href="https://iamsmmh.github.io/OmniSource/install/"><img src="assets/brand/btn-add-source.svg" alt="Add the OmniSource source"></a>

</div>
