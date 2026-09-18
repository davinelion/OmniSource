# Tweak Factory

The Tweak Factory turns the manual *Build & Inject Tweak* lane into a scheduled
pipeline: it **collects** the newest official `.deb` of every registered tweak
from the tweak's own upstream (its GitHub releases or its own apt repository),
**builds** a ready-to-sideload IPA by injecting that deb — or an ordered
*bundle* of them — into a base app the repository operator supplies, and
**publishes** the result to OmniSource as a provenance-tagged release with
SHA-256 digests — then records the build and triggers a feed sync.

Workflows: [`tweak-factory.yml`](../.github/workflows/tweak-factory.yml)
(schedule: Mondays 02:50 UTC, plus manual dispatch) reusing
[`build-tweak.yml`](../.github/workflows/build-tweak.yml).
Orchestrator: `scripts/tweak_factory.py` (stdlib-only).

## The policy line (what the factory will and will not do)

- The **`.deb` always comes from the tweak's own official upstream**: its GitHub
  releases or the `Packages` index of its own apt repository. The plan step
  re-checks every resolved URL — each bundle member's, not just the first —
  against `data/source_policy.json` before anything is downloaded.
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

## Collecting a tweak (two lanes)

A registered tweak declares where its **own** upstream publishes the `.deb`:

| `deb.source` | Fields | Behavior |
| --- | --- | --- |
| `github-release` | `repo`, `assetRegex`, optional `archPreference`, `pinVersion`, `packageId` | Newest non-draft release whose asset matches; arm64 → arm64e → arm by default. |
| `apt-repository` | `indexUrl` **or** `repo`+`suite`(+`component`), `package`, optional `pinVersion` | Reads the developer's own `Packages` index (plain, `.gz`, `.bz2` or `.xz`) and takes the newest stanza of that package — plus the **SHA-256 the index publishes for it**. |

Most single-maintainer tweaks never cut a GitHub release; they publish only to
their own Cydia/ElleKit repository, so `apt-repository` is the lane that reaches
them. Two rules come with it:

- the resolved `.deb` must stay **on the index's own host** — an index cannot
  point the factory at somewhere else, and
- the digest read out of the index is verified by the build before injection; a
  mismatch fails the run instead of publishing an unexplained artifact.

`omnisource/apt_index.py` implements the reading: a deb822 stanza parser plus
dpkg's version ordering (`1.0~rc1 < 1.0 < 1.0-1`), with no dependency solver and
no installation — the factory collects files, it never runs them.

## Bundles: one base app, several tweaks

`debs: [...]` (instead of `deb: {...}`) injects an ordered set of tweaks in
**one** build. A bundle has no version of its own upstream, so its identity is
its inputs:

```text
bundle digest = sha256 over the sorted "label|version|url" of every member
version       = <base app version>-<first 8 of the digest>
release tag   = <tagPrefix>/v<version>       # tweak-build/<slug>/v21.36.6-9f3c2a1b
```

Any member moving (or a repository re-hosting the same version) changes the
digest, so the plan rebuilds exactly once per distinct input set and skips a
quiet week entirely.

Two guardrails, because "inject everything" is how these builds stop being
usable:

- **`conflictGroups`** refuse a bundle whose members include two package ids
  from the same group, *before* a runner is claimed. Stacking two complete
  enhancers (two settings hosts, two player patchers, two download managers) is
  the double-menu / no-playback state that "all features in one IPA" bundles ship
  with, so the plan says so and skips instead of publishing it.
- **advisories** are reported and never block: a tweak whose `Depends` names
  another tweak the bundle does not inject, and members whose index
  `Architecture` differs. Injection has no dependency solver, so those are
  exactly the lines that separate "it installed" from "it works".

## Registering a tweak (`data/tweak-builds.json` → `builds`)

Each entry pins the slug, the `catalogApp` it may serve, the upstream, an asset
regex selecting the `.deb`, an arch preference (`arm64` → `arm64e` → `arm` by
default) and the publish tag prefix. `catalogApp` must exist in `catalog.json` —
the validator enforces it — **unless it is empty**, which marks an
*operator-only* build: still built, still published under the reserved tag
namespace and recorded in the state file, but no catalog entry points at it, so
it can never reach a feed. Example — the shipped single-tweak seed:

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

A bundle entry is the same shape with one object per tweak, and the two
collection lanes can be mixed in one bundle (this is the shipped
`youtube-open-bundle`, trimmed to two members):

```json
{
  "slug": "youtube-open-bundle",
  "name": "YouTube Open Bundle",
  "enabled": true,
  "catalogApp": "",
  "debs": [
    {
      "label": "youmod",
      "source": "github-release",
      "repo": "Tonwalter888/YouMod",
      "packageId": "dev.water888.youmod",
      "assetRegex": "^dev\\.water888\\.youmod_[^/]+_iphoneos-(arm64|arm64e|arm)\\.deb$"
    },
    {
      "label": "youpip",
      "source": "apt-repository",
      "indexUrl": "https://poomsmart.github.io/repo/Packages",
      "package": "com.ps.youpip"
    }
  ],
  "base": "youtube",
  "publish": { "tagPrefix": "tweak-build/youtube-open-bundle", "prerelease": true }
}
```

`packageId` is what a `github-release` member needs to take part in a
`conflictGroup`, since only an apt index states the package id.

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

## What a "ship everything, one app" request looks like here

The recurring ask is a rebranded YouTube build ("uPro/YTPlus-style") with a
list of injected tweaks in the release note, published into the source. The
factory automates the *build* half of that and stops at three lines, each of
which is a policy the repository already argues elsewhere:

1. **The base app is never acquired here.** A decrypted YouTube `.ipa` is
   somebody else's app with its protection removed; `data/source_policy.json`
   blocks the storefronts that redistribute them and the pipeline has no
   decryption path at all. Only the operator's configured `baseApps` entry is
   read, and it is re-checked before download.
2. **Nothing is promoted silently.** A factory build reaches a client only when
   a hand-curated `catalog.json` entry points at its tag namespace. An
   operator-only bundle therefore stays operator-only by construction
   (`catalogApp: ""`, enforced by the validator), and re-publishing somebody
   else's app as an OmniSource release remains a human decision with a takedown
   risk attached to it — not something a scheduled job can opt into.
3. **Only free, officially published tweak artifacts are collected** — a GitHub
   release asset or a deb inside the developer's own `Packages` index, recorded
   with the digest that upstream published. A Patreon-gated build (newer
   `YouTube Plus` releases) has no such artifact, so it is not collectable; its
   last free release, `v5.2.2`, still is, and that is what the `ytlite` entry
   pins.

The third point is also the *stability* answer. Those release notes list one
enhancer's feature set as if it were eight tweaks: YTLite's own settings carry
the framerate override, playback speed, quality selection, mute, downloads and
SponsorBlock, and it hosts the preference panes of YouPiP / YouQuality / YTUHD
instead of competing with them — `Quality 1.3.6` in a note is literally
`com.ps.youquality 1.3.6`, and `YTHUD 2.6.0` is `com.ps.ytuhd 2.6.0`. Injecting
YTLite **and** YouMod **and** YTKACE into one app instead stacks three settings
hosts, three player patchers and three download managers, which is exactly the
combination `conflictGroups` refuses rather than shipping and finding out
per-user. Stable here means one coherent tweak set per build plus a second
build for the alternative set — not more tweaks crammed into the first one.

## State

`data/tweak-builds-state.json` records every published build: tweak version,
deb URL + SHA-256, base app/version/URL, release tag, asset name, IPA
SHA-256 + size, build date and the workflow run link. A bundle record replaces
`debURL` with a `members` array (per-member version, URL, name and the digest
measured on the file that was actually injected) and puts the bundle digest in
`debSha256`: digests are taken from the bytes the build consumed, never from a
second download of a URL that may have moved since. `make`-style checks do
not regenerate it (it is a CI-owned data file); inspect it with
`python3 scripts/tweak_factory.py status`.
