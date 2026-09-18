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

## The tweak catalog (`data/tweak-builds.json` → `tweaks`)

`tweaks` is the picker list: every entry an operator can select when building
a custom app. An entry pins the id, display name, the tweak's own upstream
repo and the asset regex that selects its `.deb` — exactly like a build's
primary deb, without a base or a tag. Only tweaks whose `.deb` resolves from
their **official GitHub releases** belong here; anything else (Cydia-repo
debs, a friend's build) enters a build as an `extraDebs` URL entry or on the
command line as `--custom-deb NAME=URL`.

## Registering a build (`data/tweak-builds.json` → `builds`)

Each entry pins the slug (must exist in `catalog.json` — a test enforces
this), the upstream repo of its **primary** `.deb`, an asset regex, an arch
preference (`arm64` → `arm64e` → `arm` by default) and the publish tag
prefix. A build may additionally co-inject other tweaks into the same IPA in
one Cyan pass via `extraDebs` — each entry is either a catalog id (optionally
pinned: `{"id": "ytlite", "version": "5.2.2"}`) or an operator-supplied
`{"name", "url"}` `.deb`. The inject workflow receives them newline-joined as
`extra_deb_urls`, and the state file records each extra's URL, version and
SHA-256 (changing any of them forces a rebuild). Example — the shipped seed:

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
  "base": "youtube",
  "extraDebs": [
    { "id": "youmod" },
    { "name": "Gonerino", "url": "https://repo.example.me/Gonerino.deb" }
  ]
}
```

Validate locally with `python3 scripts/tweak_factory.py validate`, and
rehearse a full plan (read-only) with
`python3 scripts/tweak_factory.py plan`. The plan prints exactly what would
be built, what is up to date, and why anything is skipped — with a configured
base app, `ytlite` resolves to the official `v5.2.2` arm64 deb and the
release tag `tweak-build/ytlite/v5.2.2`.

## Building a custom app with the `build` helper (the uProVid flow)

The scheduled factory builds whatever the registry says. For a one-off,
operator-driven build — *my decrypted YouTube, my tweaks, my name* — the
`build` subcommand is the same pipeline with a human in the loop:

```
python3 scripts/tweak_factory.py build \
    --base ~/Downloads/YouTube.ipa \
    --tweaks youmod,ytlite \
    --custom-deb Gonerino=https://…/Gonerino.deb \
    --app-name uProVid \
    --bundle-id com.iamsmmh.uprovid \
    --save-registry \
    --run
```

`--base` takes a local `.ipa` or an https link; `--tweaks` picks ids from the
`tweaks` catalog (`all`, `none`); `--custom-deb NAME=URL` (repeatable) adds
direct `.deb` links; the custom options are `--app-name`, `--bundle-id`
(default: the base's), `--tag-prefix` and `--notes`. Run it without flags for
the interactive form: it asks for the base, shows the numbered tweak list
(`c` adds a custom `.deb` URL), then the app name / bundle ID / tag prefix,
and prints the resolved plan. What the flags do:

1. **base** — a local `.ipa` is validated (`Payload/*.app/Info.plist` is
   parsed for bundle ID and version, and the file is SHA-256'd); an https
   link is checked against the sourcing policy and probed.
   `--publish-base` uploads a local base as a release (`<tagPrefix><version>`)
   into the configured dumps repo so the macOS runner can download it.
2. **tweaks** — catalog picks resolve to the newest official `.deb` from the
   tweak's own GitHub releases; custom picks pass the policy check as-is.
3. **`--run`** — dispatches the reusable *Build & Inject Tweak* workflow with
   the primary deb plus `extra_deb_urls`; the patched IPA lands as a workflow
   artifact (14-day retention). This lane is for the operator's own install
   and never publishes a catalog release.
4. **`--save-registry` / `--factory`** — writes the selection into
   `data/tweak-builds.json` (primary deb, `extraDebs`, app name, bundle ID,
   tag prefix; a link base repoints `baseApps` at it) and, with `--factory`,
   dispatches the scheduled Tweak Factory, which builds, publishes the
   `tweak-build/uprovid/*` release and syncs the feeds. **Commit and push the
   registry change first** — the factory reads it from the repository.

The `uprovid` catalog entry is wired to exactly that namespace: until the
first factory release exists, it serves the `manualRelease` stand-in from
`catalog.json`; once `tweak-build/uprovid/*` exists, your build takes over
automatically on the next sync.

## How a build flows

1. **plan** (Ubuntu): resolve deb + base per entry; an empty matrix claims no
   macOS runner. Skipped and up-to-date builds are listed in the run summary
   with their reasons.
2. **build** (macOS, one job per stale entry): the reusable *Build & Inject
   Tweak* workflow sanitizes inputs, re-runs the sourcing policy gate,
   downloads all artifacts and injects them (primary .deb plus any extraDebs) with Cyan. The factory job then
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
