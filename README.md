<div align="center">

<img src="assets/brand/hero.svg" width="100%" alt="OmniSource — an automated, source-first aggregation platform for iOS sideloading clients">

<br>

[![Website](https://img.shields.io/website?url=https%3A%2F%2Fiamsmmh.github.io%2FOmniSource%2F&label=website)](https://iamsmmh.github.io/OmniSource/)
[![Apps](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-apps.json)](https://iamsmmh.github.io/OmniSource/)
[![Download health](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fiamsmmh%2FOmniSource%2Fmain%2Ffeeds%2Fbadge-health.json)](https://iamsmmh.github.io/OmniSource/status/)
[![Validate](https://github.com/iamsmmh/OmniSource/actions/workflows/validate.yml/badge.svg)](https://github.com/iamsmmh/OmniSource/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/iamsmmh/OmniSource)](LICENSE)

<br>

<a href="https://iamsmmh.github.io/OmniSource/install/"><img src="assets/brand/btn-add-source.svg" alt="Add the OmniSource source"></a>

<br>

<a href="https://iamsmmh.github.io/OmniSource/install/?add=altstore"><img src="assets/brand/btn-add-altstore.svg" alt="Add to AltStore" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=sidestore"><img src="assets/brand/btn-add-sidestore.svg" alt="Add to SideStore" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=feather"><img src="assets/brand/btn-add-feather.svg" alt="Add to Feather" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=esign"><img src="assets/brand/btn-add-esign.svg" alt="Add to ESign" height="46"></a>
<a href="https://iamsmmh.github.io/OmniSource/install/?add=livecontainer"><img src="assets/brand/btn-add-livecontainer.svg" alt="Add to LiveContainer" height="46"></a>

**One installable source for AltStore, SideStore, Feather, ESign and LiveContainer.**

</div>

<img src="assets/brand/divider.svg" width="100%" alt="">

## The source

```text
https://iamsmmh.github.io/OmniSource/apps.json
```

Paste that into your client's *Add Source* screen. On a phone, the buttons above
open the [install center](https://iamsmmh.github.io/OmniSource/install/) — one
tap, and a QR code when the client is not installed.

## What this is

Every app in the catalog is resolved from the developer's own upstream — GitHub
releases or the project's own feed — then validated, digested where the
publisher publishes a digest, probed for availability, and republished as a
deterministic AltStore v2 feed. One hand-maintained `catalog.json`, everything
else generated, and a GitHub Actions pipeline that keeps it honest: no
accounts, no ads, no tracking, no re-hosted binaries.

There is also a **Tweak Factory**: bring a decrypted base IPA and a set of
official tweaks, and the pipeline injects them with Cyan and publishes a
provenance-tagged release — that is how [uProVid](https://iamsmmh.github.io/OmniSource/apps/uprovid/)
ships *your* selection of YouTube mods. See
[`docs/TWEAK-FACTORY.md`](docs/TWEAK-FACTORY.md).

## The website is the documentation

Catalog, app pages with screenshots and release history, per-app hashes and
provenance, source reputation, status and the machine API all live at
**[iamsmmh.github.io/OmniSource](https://iamsmmh.github.io/OmniSource/)** —
[app pages](https://iamsmmh.github.io/OmniSource/apps/delta/) ·
[install center](https://iamsmmh.github.io/OmniSource/install/) ·
[sources](https://iamsmmh.github.io/OmniSource/sources/) ·
[status](https://iamsmmh.github.io/OmniSource/status/) ·
[docs](https://iamsmmh.github.io/OmniSource/docs/) ·
[API](https://iamsmmh.github.io/OmniSource/api/index.json)

## For maintainers

```bash
export PYTHONPATH=src                    # stdlib-only, Python 3.11+
python3 -m unittest discover -s tests    # the test suite
python3 scripts/validate.py              # catalog + feed validation
make check                               # the whole gate: lint, tests, reproducibility, smoke
```

`catalog.json` is the only app file you hand-edit — then `make build` (feeds)
and `make derived` (client feeds, API v3, reputation) regenerate the rest.
Fifteen workflows keep it running; the map is in
[`.github/workflows/README.md`](.github/workflows/README.md).
Rules in [CONTRIBUTING.md](CONTRIBUTING.md), threat model in
[SECURITY.md](SECURITY.md).

---

GPL-3.0. App names, icons and trademarks belong to their owners; OmniSource
aggregates metadata and links to public publishers — it does not re-host
upstream binaries.
