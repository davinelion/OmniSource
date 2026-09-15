# Source builds

`catalog.json` answers one question for every app it lists: *where does the
developer publish their own build?* Some worthwhile iOS projects never answer
it. A tweak whose only release is a `.deb`, a VNC server whose bundle is built
by its own GitHub Actions workflow, an on-device patcher that tells you to patch
it into an app yourself. Those are not publishable through an AltStore-style
feed, and pretending otherwise — hand-typed metadata for a binary nobody
released — is the failure mode this repository exists to prevent.

`data/source_builds.json` is the **separate** lane for them: reviewed recipes,
not releases. A recipe is never merged into a feed, never becomes an app record,
and OmniSource never signs or hosts what it produces.

## What makes a recipe auditable

| Field | Promise |
| --- | --- |
| `pin.commit` | full 40-character commit SHA. Tags and commits are allowed; a branch is rejected, because a moving reference cannot be reviewed |
| `pin.committedAt` | the date of that commit, so staleness is visible |
| `source.archiveURL` | the exact archive the reviewer downloaded |
| `source.sha256` / `source.bytes` | digest and size of that archive, computed by fetching it — not copied from anyone's summary |
| `build.commands` | taken from the project's own Makefile/README, with `source.upstreamWorkflow` naming the CI file that proves them |
| `signing` | who signs the result, always the builder. `model: "none"` is only legal for artifacts nobody signs (a tweak `.deb`, a build system) |
| `verification.evidence` | every claim that was checked and how. Anything not checked must not be implied |

`scripts/validate.py` enforces the shape, the pin rules and the cross-links
(`validate_source_builds`), and the sourcing policy applies here too: a recipe
may not fetch from, or point at, a host recorded in `data/source_policy.json`.
An unparseable recipe file is an error, never a silent "no recipes today".

## Commands

```console
$ python3 scripts/build_source.py list            # one line per recipe
$ python3 scripts/build_source.py check           # offline gate; also runs inside validate.py
$ python3 scripts/build_source.py plan trollvnc   # reproducible transcript, digest check included
$ python3 scripts/build_source.py fetch trollvnc  # download the pinned archive, verify the digest
$ python3 scripts/build_source.py verify autoflex --archive ./file.tar.gz
$ python3 scripts/build_source.py hash ./file.tar.gz   # when authoring a recipe
```

`plan` prints a transcript you can paste into a shell. It deliberately ends with
a comment about signing rather than a signing command, and it verifies the digest
*before* anything is compiled, so a silently updated or mirrored archive cannot
be built by accident.

## The current list

| Slug | Kind | Upstream | What you build | Signing |
| --- | --- | --- | --- | --- |
| `trollvnc` | app | `owngoal-dev/TrollVNC` | Theos package for a VNC server | TrollStore / SideStore / Feather |
| `filzaplus` | tweak | `Skittyblock/FilzaPlus` | `.deb` for Filza | none needed |
| `ersatz` | tweak | `Skittyblock/Ersatz` | system-wide text replacement `.deb` | none needed |
| `sixls` | tweak | `Skittyblock/SixLS` | iOS 6 lock screen `.deb` | none needed |
| `autoflex` | tweak | `pwnless/AutoFLEX` | FLEX loader `.deb`, patched into a target app | TrollFools / Sideloadly |
| `sideloader` | signer | `Dadoum/Sideloader` | Linux/macOS/Windows installer that signs with your Apple ID | your own account |
| `impactor` | signer | `claration/Impactor` | cross-platform sideloading app | your own account |
| `signtools` | signer | `SignTools/SignTools` | self-hosted signing service (Go) + macOS builder | your own certs, your box |
| `theos-toolchain` | toolchain | `theos/theos` | the build system every tweak above needs | n/a |

Versions, commits and digests are in the data file — this table intentionally
quotes none of them, so it cannot go stale.

## Finding candidates

`scripts/discovery/find_source_builds.py` searches the forges for iOS projects
that publish source but no distributable artifact, and prints a *draft* per
hit — plus a note for every repo it deliberately did not draft:

```console
$ python3 scripts/discovery/find_source_builds.py --limit 5 --min-stars 200 --hash
  note  Lessica/TrollFools: publishes TrollFools_4.3-253.tipa — belongs in catalog.json, not here
  note  swaggyP36000/TrollStore-IPAs: publishes AIChatGP-2.263.ipa — belongs in catalog.json, not here
  note  JJTech0130/TrollRestore: no recognised build system at 1.0
  draft AeonLucid/SnapHide      make       pin=master    digest=98ab1cfd8cac
  draft claration/Impactor      cargo      pin=v2.6.3    digest=ad10a4b85c55
  draft altstoreio/AltStore     xcodebuild pin=v1.6.3    digest=c75033e62e9d
```

That refusal list is the useful half: a repository whose releases contain a
real `.ipa`/`.tipa`/`.deb` is a *catalogue* candidate, and the finder says so
instead of drafting a recipe for it. `--hash` downloads each pinned source
archive and digests it, so the draft already carries the integrity field a
reviewer would otherwise compute by hand. Nothing is written without `--out`,
the output never touches `data/discovered_sources.json`, and the sourcing
policy is applied first — a blocked repository is reported as excluded, and an
unparseable policy aborts the run.

## Adding a recipe

1. Confirm upstream really publishes no iOS artifact: check its latest release's
   assets. If it publishes one, it belongs in `catalog.json` with a normal
   `upstream` block — not here.
2. Pick a revision (tag preferred, commit if there is no tag) and download the
   forge's source archive for exactly that revision:
   `python3 scripts/build_source.py hash <archive>` → `source.sha256` + `bytes`.
3. Take the commands from the project's own Makefile or CI file and record that
   file in `source.upstreamWorkflow`. Do not invent flags you have not run.
4. Fill `verification.evidence` with what you actually checked, and `notes` with
   why this is a recipe rather than a catalogue entry.
5. `python3 scripts/build_source.py check` and `python3 scripts/validate.py` must
   both stay clean; `schemas/source-builds.schema.json` documents the shape.

`gitlab`, `codeberg`, `forgejo` and `gitea` are valid `forge` values — the
catalogue already resolves releases from them (see `upstream.provider` and
`scripts/discovery/discover_forges.py`). A recipe for a non-GitHub forge is fine
as long as whoever adds it computed the digest from that forge, on a network that
can reach it.

## Refusals

* **No decrypted base apps.** A "recipe" that starts from somebody else's
  decrypted App Store binary is not a build-from-source story; it is the class
  `data/source_policy.json` rejects (`account-gated-decrypted-store`,
  `cracked-package-repository`), and
  [the sourcing report](SOURCING-REPORT-2026-09-15.md) records why. The manual
  patcher refuses such a base IPA before downloading it.
* **No third-party signing services.** Signing with a rented certificate — an
  ARM Store / ESign storefront-style service, a shared enterprise cert — puts
  your apps' identity, your Apple ID and a 7-day re-sign loop in somebody else's
  hands, and produces artifacts no feed, health check or digest can attest. The
  lane above is the alternative: build the app *and* the signer
  (`sideloader`, `impactor`, `signtools`) from pinned, digested source, and sign
  with an identity you control.
* **No binaries from us.** Nothing built from a recipe is uploaded, mirrored or
  referenced by a feed. If you publish your build somewhere, that somewhere is
  your release channel, and only then does it become a catalogue candidate.
