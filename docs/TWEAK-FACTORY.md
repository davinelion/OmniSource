# Tweak Factory

The Tweak Factory turns the manual *Build & Inject Tweak* lane into a scheduled
pipeline: it **collects** the newest official `.deb` of every registered tweak
from the tweak's own upstream releases, **builds** a ready-to-sideload IPA by
injecting that deb into a base app the repository operator supplies, and
**publishes** the result to OmniSource as a provenance-tagged release with
SHA-256 digests — then records the build and triggers a feed sync.

Workflows: [`tweak-factory.yml`](../.github/workflows/tweak-factory.yml)
(schedule: Mondays 02:50 UTC, plus manual dispatch) reusing
[`build-tweak.yml`](../.github/workflows/build-tweak.yml).
Orchestrator: `scripts/tweak_factory.py` (stdlib-only).

## The policy line (what the factory will and will not do)

- The **`.deb` always comes from the tweak's own official upstream** (GitHub
  releases today; apt repositories are a planned source type). The plan step
  re-checks every resolved URL against `data/source_policy.json` before
  anything is downloaded.
- The **base app is operator-supplied**: the factory never downloads a base
  from a public "decrypted app store" — those hosts are blocked by the same
  policy, and the pipeline has no way (and no permission) to decrypt apps.
  You configure where your decrypted bases come from; see below.
- Builds publish to **this repository's releases** under a reserved
  `tweak-build/…` tag namespace, marked as prereleases, with the deb version,
  base version, both digests and the workflow run link in the notes. Nothing
  is ever promoted silently: a catalog app only serves a factory build once
  its hand-curated entry points at that tag namespace (see below).

## Configuring a base app (`data/tweak-builds.json` → `baseApps`)

A base app is a decrypted `.ipa` of the host app a tweak hooks (YouTube for
YTLite, and so on). One base app is shared by every tweak that hooks it.
Three source types are supported:

| `source` | Fields | Behavior |
| --- | --- | --- |
| `release` (recommended) | `repo`, `tagPrefix`, `assetGlob` | The newest release in your `repo` whose tag starts with `tagPrefix` and that carries an asset matching `assetGlob`. Example: a `iamsmmh/base-ipas` repo with releases `youtube-19.19.3`, `youtube-20.x`… each holding the decrypted IPA. |
| `variable` | `envVar` | The URL is read from a repository **variable** (Settings → Secrets and variables → Actions → Variables) through the API at plan time. |
| `url` | `url` | A fixed https URL. |

Notes:

- The base repo must be readable with the workflow's own token — use your own
  public (or same-org) dumps repo. Private repos with token-gated asset URLs
  are not supported in v1 because the injection workflow downloads with plain
  `curl`.
- Changing the base version (a new `youtube-*` release) automatically
  triggers a rebuild of every tweak that depends on that base app.

## Registering a tweak (`data/tweak-builds.json` → `builds`)

Each entry pins the slug (must exist in `catalog.json` — a test enforces
this), the upstream repo, an asset regex selecting the `.deb`, an arch
preference (`arm64` → `arm64e` → `arm` by default) and the publish tag
prefix. Example — the shipped seed:

```json
{
  "slug": "ytlite",
  "name": "YTLite",
  "enabled": true,
  "catalogApp": "ytlite",
  "deb": {
    "source": "github-release",
    "repo": "Dayanch96/YTLite",
    "assetRegex": "^com\\.dvntm\\.ytlite_[^/]+_iphoneos-(arm64|arm64e|arm)\\.deb$"
  },
  "base": "youtube"
}
```

Validate locally with `python3 scripts/tweak_factory.py validate`, and
rehearse a full plan (read-only) with
`python3 scripts/tweak_factory.py plan`. The plan prints exactly what would
be built, what is up to date, and why anything is skipped — with a configured
base app, `ytlite` resolves to the official `v5.2.2` arm64 deb and the
release tag `tweak-build/ytlite/v5.2.2`.

## How a build flows

1. **plan** (Ubuntu): resolve deb + base per entry; an empty matrix claims no
   macOS runner. Skipped and up-to-date builds are listed in the run summary
   with their reasons.
2. **build** (macOS, one job per stale entry): the reusable *Build & Inject
   Tweak* workflow sanitizes inputs, re-runs the sourcing policy gate,
   downloads both artifacts and injects with Cyan. The factory job then
   publishes the release (`gh release create tweak-build/<slug>/v<version>`)
   and uploads a state fragment.
3. **commit** (Ubuntu): merges fragments into
   `data/tweak-builds-state.json` (deterministic, newest-wins), commits with
   `[skip ci]`, and dispatches `sync.yml`.

Cost control: a build is skipped when its resolved deb *and* base versions
are unchanged **and** the release tag already exists, so a quiet week costs
one Ubuntu plan job and nothing else. Dispatch with *force* to rebuild anyway.

## Publishing a factory build into the source

The pipeline never edits `catalog.json`. To serve a factory build from the
source, point the catalog app's upstream at this repository's tag namespace —
the same pattern `uyouenhanced` already uses for its self-built releases:

```json
"upstream": {
  "method": "github-release",
  "repo": "iamsmmh/OmniSource",
  "tagPrefix": "tweak-build/ytlite/"
}
```

The next sync resolves the newest `tweak-build/ytlite/*` release as the app's
version, and the IPA Downloader index picks it up automatically.

## State

`data/tweak-builds-state.json` records every published build: tweak version,
deb URL + SHA-256, base app/version/URL, release tag, asset name, IPA
SHA-256 + size, build date and the workflow run link. `make`-style checks do
not regenerate it (it is a CI-owned data file); inspect it with
`python3 scripts/tweak_factory.py status`.
