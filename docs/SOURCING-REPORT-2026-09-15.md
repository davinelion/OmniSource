# Sourcing report — FMHY iOS tools, and the "ARM Store" verdict

Date: 2026-09-15 · Branch: `arena/01a0a3b5-omnisource` · Follows: [SOURCING-REPORT.md](SOURCING-REPORT.md) (2026-09-10)

Inputs reviewed: `https://armconverter.com/store/us` (asked about directly), the `iOS Tools`,
`iOS Jailbreaking`, `iOS Sideloading`, `iOS Adblocking`, `iOS Privacy` and `iOS iPAs` sections of
<https://fmhy.net/mobile>, and every upstream project those sections link to that could
plausibly be catalogued. This report records what was added, what was skipped and why, and makes
the skipped verdicts machine-enforced.

## Inclusion rules applied (unchanged)

1. **Official upstream required** — the release must come from the project's own GitHub/GitLab/
   Codeberg Releases or its own AltStore/Feather feed. Aggregator re-uploads, account-gated
   mirrors and "store" pages are out.
2. **Maintained, or the final official build of a jailbreak tool.**
3. **Installable by the clients the catalog serves** — AltStore, SideStore, Feather, ESign,
   LiveContainer.
4. **No near-duplicates** — shared bundle identifiers must be declared with `alternativeTo`.

Every version number, date, byte size and download URL below was read from the GitHub Releases API
for the tagged release named; every bundle identifier was read from the project's own build files
(`project.pbxproj`, `Config.xcconfig`, `Filter` plist), not from memory or from a store page.

## The "ARM Store" link — excluded, and now blocked in CI

`https://armconverter.com/store/us` is not a sideloading source. What was observed:

| Check | Result |
| --- | --- |
| Page at `/store/us` | A storefront titled "ARM Store" that renders **"Login required / Sign In"**. Nothing else — no app list, no feed, no metadata. |
| `/store/us/apps.json`, `/apps.json` | Same login page; no AltStore/Feather envelope at any of the conventional feed paths. |
| What the site is | FMHY itself lists `armconverter.com/decryptedappstore` under **iOS iPAs → "Decrypted App Store"** — a library of FairPlay-stripped copies of App Store apps, alongside `decrypt.34306.lol` and `anyipa.me`. |
| Provenance available | None. No repository, no published SHA-256/SHA-512 digest, no per-app version channel, no changelog, no way to re-verify a build later. |

Failing inclusion rule 1 twice over (account-gated *and* a store page rather than the
publisher's release channel), there is nothing to validate: an entry would have to be *authored
from the storefront's word alone*, which is precisely what this catalog's design refuses — every
field must be traceable to the developer who built the binary. Redistributing decrypted copies of
other people's apps is also not something this project republishes, whatever the app.

The verdict is therefore recorded, not just written down: `data/source_policy.json` now carries
`account-gated-decrypted-store`, and three gates enforce it —

* discovery drops matching candidates before they enter `data/discovered_sources.json`
  (and prunes any already there — four records went out with this change);
* `assert_publishable()` refuses them, so no hand-edit can smuggle one into promotion;
* `scripts/validate.py` fails the build if any `catalog.json` entry resolves from, or mirrors, a
  blocked host — and `.github/workflows/build-tweak.yml` refuses to *download* a base IPA or
  `.deb` from one, so the patcher cannot be aimed at that storefront either.

`armconverter.com` outside `/store` and `/decryptedappstore` is a file-size converter and is not
blocked — the rule is path-scoped on purpose so that a verdict about a storefront is not a verdict
about an unrelated host.

## "Inject the tweaks yourself" — what was done, and where it stops

The repo already has the machinery to patch a tweak into an IPA (`build-tweak.yml`, `cyan`). What
was **not** done is run it to produce new binaries for this PR: nothing in `feeds/` here was
built by this repository. A catalog entry is the *tweak project's own* published build, so
provenance stays with the person who wrote the tweak, and the 3-app/re-signing reality of a
patched-by-us binary does not become our claim to maintain.

What was done instead for the tweak family:

* **Three tweak-side upstreams catalogued** — `trollfools` (in-place dylib injection with
  `insert_dylib`/ChOma), `lara` (DarkSword toolbox) and `snmessenger` (a `.deb` tweak whose repo
  also publishes its own ready-to-sideload IPA). `snmessenger` follows the same shape as
  `bhtwitter`: the tweak author's own installable build, with the pinned base-app version stated in
  `compatibility.notes`.
* **A curated `Tweaks` collection** (`src/omnisource/collections.py` → `feeds/collections.json` and
  `collections/tweaks/index.html`), because 23 tweak-family entries were previously findable only
  through search or three different categories. The collection text states explicitly that
  OmniSource does not patch binaries itself.
* **`upstream.assetNamePattern` / `versionPattern` tightened** for the new entries so only the
  installable artifact is tracked: TrollFools' `.tipa` (its `.deb` siblings skipped), Orbot's
  `Orbot.ipa` (not `Orbot.pkg`), Table Habit's `mhabit-*.ipa` (not the APKs/flatpaks/dmg/msix in
  the same release).

## Added (7 apps — catalog 94 → 101)

| Entry | Upstream | Release used | Evidence for the fields that matter |
| --- | --- | --- | --- |
| `trollfools` | `Lessica/TrollFools` | `v4.3-253` · `TrollFools_4.3-253.tipa` · 6 214 108 B · 2026-04-23 | `wiki.qaq.TrollFools` and `IPHONEOS_DEPLOYMENT_TARGET = 14.0` from `TrollFools.xcodeproj/project.pbxproj`; `.deb` line skipped |
| `lara` | `rooootdev/lara` | `0.2` · `lara_v0.2.ipa` · 5 474 210 B · 2026-05-22 | `com.roooot.lara` from `lara.xcodeproj/project.pbxproj`; iOS 17.0 – 18.7.1 / 26.0 – 26.0.1 support and the M5/A19 exclusion taken from the project README |
| `onionbrowser` | `OnionBrowser/OnionBrowser` | `v3.4.1` · `OnionBrowser.ipa` · 39 611 914 B · 2026-08-11 | `com.miketigas.OnionBrowser` from `Config.xcconfig` (`PRODUCT_BUNDLE_IDENTIFIER[config=Release]`); deployment target 15.0 |
| `orbot` | `guardianproject/orbot-ios` | `v1.12.0` · `Orbot.ipa` · 48 820 768 B · 2026-06-30 | `org.torproject.orbot` from `Shared/Config.xcconfig` (`APP_BUNDLE_ID[config=Release]`); `Orbot.pkg` skipped |
| `tablehabit` | `FriesI23/mhabit` | `v1.27.6+195` · `mhabit-unsigned.ipa` · 14 790 504 B · 2026-09-07 | `io.github.friesi23.mhabit` from `ios/Runner.xcodeproj/project.pbxproj`; deployment target 15.0 |
| `byetunes` | `EduAlexxis/ByeTunes` | `v2.5` · `ByeTunes.ipa` · 16 815 108 B · 2026-08-31 | `com.EduAlexxis.MusicManager` from `MusicManager.xcodeproj/project.pbxproj`; iOS 16.0 per the project README |
| `snmessenger` | `NguyenASang/SNMessenger` | `v2.0.0` · `Messenger_517.0.0.SNMessenger.v2.0.0-2.ipa` · 74 865 911 B · 2026-07-31 | `com.facebook.Messenger` from the tweak's own filter plist; iOS 12.4 and the rootful/rootless/sideload matrix from its README |

Bundle-ID collision found and declared while adding these: `snmessenger` and `msgplusx` both carry
the stock Messenger identifier, so each now names the other in `alternativeTo` (the validator would
have failed the build otherwise) and `messenger-flow` — which keeps its own `.flow` id — is called
out as installable alongside them.

Icons were taken from each project's own asset catalog (`AppIcon.appiconset`, `misc/appicon.png`,
`orbot-ios-1024.png`, the marketing PNG in `Artworks`) and normalized to 512×512 PNG + WebP like
the rest of `assets/`. No icon was generated or substituted.

## Skipped — with reasons

| Candidate (all linked from fmhy.net/mobile) | Reason |
| --- | --- |
| `armconverter.com/store/us`, `decrypt.34306.lol`, `anyipa.me` | Account-gated decrypted-app storefronts; see the verdict above. Policy: `account-gated-decrypted-store`. |
| CyPwn IPA Library (`ipa.cypwn.xyz/cypwn.json`), DriftyWinds' AltStore repo, `ipalibrary.me`, iOSVizor, AppTesters, "Alan's Gigantic Repo" (fastsign.dev), `Neoncat-OG/TrollStore-IPAs`, `baretsky/baretsky-tweaked-ipas-library`, `Win98Plus/signed_apple-apps`, `Stuffed18/ipa-archive-updated` | Aggregators and re-host libraries. Two of them (`cypwn.json`, the DriftyWinds feed) were **live and healthy** in `data/discovered_sources.json`; both were re-classified as policy exclusions and pruned. Policy: `tweaked-app-aggregator`. |
| ReJail, PDALife, 4PDA, PlatinMods, iOSObscura on archive.org, the FMHY Telegram IPA channels | Cracked/paid-for-free distribution. Policy: `cracked-package-repository` — archive.org is blocked **only** under `/details/iosobscura`, since this catalog legitimately uses archive.org mirrors. |
| Misaka | `straight-tamago/misaka` (the repo FMHY links) is the product/Pages site: 66 paths, no source, no `Info.plist`, so no authoritative bundle id. Its successors (`misakaX`, `misaka26`) publish macOS and Windows zips only. Skipped for the same reason as 2026-09-10 — the note in the old report is now *verified* rather than assumed. |
| touchHLE, iDescriptor, Nugget, SignTools, AltServer-Linux, SideInstaller, TrollStore, Cyan, ipatool, SideStore-JS tooling | Desktop/CLI tooling, or TrollStore-installers, with no sideloadable iOS app in their own releases (touchHLE ships macOS/Android/Windows; iDescriptor ships dmg/AppImage/msi; Nugget ships dmg/zip — whereas TrollFools does ship an installable container, which is why it is in). |
| `NSAntoine/Santander`, `net00-1/SW-DLT`, `RetroArch`, `libretro`, `Watusi`, `iSponsorBlock`, `SCInsta`, `EeveeSpotify(Revived)`, `dayanch96/YTLite` / `YTMusicUltimate` (source repos), `Birb`, `Enmity` | No release asset a client can install: either no releases at all, or `.deb`/source-only. `RetroArch` v1.22.2 publishes a source tarball; `SCInsta` v1.1.1 publishes one rootless `.deb`. |
| AltStore itself, Ksign, Feather, LiveContainer, ESign, iTorrent, PikaTorrent, PureKFD, Bootstrap, MuffinStore, Dopamine, Taurine, Odyssey, unc0ver, UTM, Delta, Provenance, PPSSPP, iSH, Chatsen, Thunder, Monal, Chan, Voyager, RedditFilter, BHTwitter, Yattee, AnimeGen, Cosmos, SpotiFLAC | Already in the catalog (or, for AltStore and ESign, listed as *clients* the source targets rather than distributed apps). |
| App Store / TestFlight-only entries FMHY lists (Keka, Scriptable, BlackMagic Camera, PicsArt, Reeder, Bear, Drafts, Twodos, Parcel, KeyPad, ChatGPT/Gemini/DeepSeek, Mollama, PocketPal, Sia Storage, fGet, …) | No self-published sideloadable build; cataloguing them would mean taking a re-upload as the source. |

`FrizzleM/SideInstaller` was the near-miss worth recording: its `v1.0.0` release does ship
`SideInstaller.ipa` (9 243 749 B), but the repository publishes no `xcodeproj` for the iOS target —
the build settings live in a workflow that never names a bundle identifier — and its README badge
(0.9.0) disagrees with the tag (1.0.0). Without an authoritative `CFBundleIdentifier`, an entry
would have had to invent one, so it stays out.

## Caveats and follow-ups

* **No byte-level verification was possible in this working sandbox.** `github.com` and
  `api.github.com` are reachable; `objects.githubusercontent.com` (release assets) is not, so the
  new IPAs could not be streamed and hashed. Consequences: `verification.checksumPublished` is
  `false` and `checksum` is `null` for all seven (no upstream here publishes a digest file next to
  the IPA), and the sync ran with `--no-health`, so the fresh download links carry no probe result
  of their own yet. `health-check.yml` / `verify.yml` close both gaps on the schedule.
* **`lara` is 0.x** and is entered as `status: "beta"` with its unsupported-firmware list in the
  notes; if the maintainers move it to a stable release, only `status` changes.
* **`orbot` needs a Network Extension entitlement**, which a free personal Apple ID cannot sign.
  That is stated in its notes instead of silently promising it works for everyone.
* `snmessenger`'s sideload build is nearly a year old while the tweak repo is still moving: if the
  next release drops the `.ipa` asset and ships only `.deb`, the sync will keep serving the last
  installable build rather than fall back to somebody else's re-upload.
* Follow-up worth a separate change: `neofreebird` tracks `NeoFreeBird/app`, but the active
  repository (`theacrat/NeoFreeBird`, v6.0.4 with its own sideloaded/trollstore asset pairs) is where
  releases are published now. Untouched here because it was out of scope, not checked out as wrong.
