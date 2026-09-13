"""Install card generator.

The website and per-app pages need a uniform "install with" card for every
supported client. The catalog only declares the supported clients; this
module combines that with each app's per-app feed URL to produce a fully
typed ``feeds/install.json`` document that the front-end can render without
recomputing deep links.

The result is intentionally not hard-coded — every URL is derived from
``baseURL`` and the per-app feed, so a future host rename is a single edit
to the catalog.

Markdown contexts that strip non-http schemes (GitHub README rendering, most
chat apps) cannot link the client URLs above directly. They instead link the
installation center with an ``add`` parameter —
``/install/?add=<client-id>[&app=<slug>]`` — and the page (``js/site.js``,
``Install.handleAutoAdd``) renders a status banner and performs the scheme
hand-off once, falling back to a manual retry and the feed URL when the
client is not installed. The README badge buttons and the client table use
that form by design.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from omnisource.domain import Catalog, today

INSTALL_SCHEMA_VERSION = 1

# Each client is described with: id, name, icon, supported URL scheme(s),
# and whether the source URL is *deep-linkable*. All five clients support
# a one-tap "add source" scheme; keep the raw feed URL in the query string
# (the feed URLs never contain ``&`` or spaces, and this matches the format
# AltStore/SideStore clients expect). If a future scheme breaks, flip the
# profile back to ``deepLinkable: False`` and the UI falls back to copy.
#
# ``description`` and ``steps`` are the install advice shown on /install/ and on
# every app page. They are per client on purpose: the five tools do different
# things. AltStore and SideStore are signing *hosts* that keep apps alive in the
# background; ESign is a signer with no background refresh; LiveContainer is a
# container that has to be installed through another client first and then hosts
# the apps itself. A single generic triple ("install X, it stays as your
# sideloading host") was wrong for the last two and told a reader nothing the
# first two did not already know.
CLIENT_PROFILES = {
    "altstore": {
        "id": "altstore",
        "name": "AltStore",
        "scheme": "altstore://source?url={url}",
        "deepLinkable": True,
        "instructions": "Tap to add the source to AltStore.",
        "manualSetup": False,
        "description": (
            "The reference sideloading client: it signs apps with your own Apple Account and keeps them "
            "installed by refreshing them over Wi-Fi."
        ),
        "requirements": (
            "AltServer on a Mac or PC (AltStore Classic), or an EU Apple Account on iOS 17.4+ (AltStore PAL)."
        ),
        "steps": [
            "Install AltStore — AltStore Classic needs AltServer on a Mac or PC; AltStore PAL (EU, iOS 17.4+) "
            "installs directly and is notarized by Apple.",
            "Add this source: the link below opens AltStore on the Add Source screen, or use "
            "AltStore → Settings → Sources → Add Source.",
            "Install from the catalog. A free Apple Account keeps two apps installed alongside AltStore and "
            "re-signs them every 7 days; a paid developer account extends that to 90.",
        ],
    },
    "sidestore": {
        "id": "sidestore",
        "name": "SideStore",
        "scheme": "sidestore://source?url={url}",
        "deepLinkable": True,
        "instructions": "Tap to add the source to SideStore.",
        "manualSetup": False,
        "description": (
            "An AltStore fork that refreshes on the device through its own local VPN — you need a computer once, "
            "to install it, and never again to keep apps alive."
        ),
        "requirements": "A computer once, for iloader; LocalDevVPN installed and connected on the device.",
        "steps": [
            "Install SideStore with iloader on a computer (iOS 15+ and a device passcode are required), then "
            "install the LocalDevVPN app and keep it switched on while installing or refreshing.",
            "Add this source: the link below opens SideStore on the Add Source screen, or use "
            "SideStore → Sources → Add Source.",
            "Install from the catalog; SideStore refreshes its own apps on-device, so nothing has to be plugged "
            "in. The free Apple Account limits still apply.",
        ],
    },
    "feather": {
        "id": "feather",
        "name": "Feather",
        "scheme": "feather://source/{host}{path}",
        "deepLinkable": True,
        "instructions": "Tap to add the source to Feather.",
        "manualSetup": False,
        "description": (
            "A source browser and installer for jailbroken, TrollStore-equipped or certificate-holding devices: "
            "Feather lists sources, installs from them and leaves refreshing to you."
        ),
        "requirements": "A jailbreak, TrollStore/ERE, or your own signing certificate.",
        "steps": [
            "Install Feather — it needs a signing path of its own (jailbreak, TrollStore/ERE, or your own "
            "developer certificate).",
            "Add this source: the link below opens Feather's source importer, or use Feather → Sources → Add "
            "Source and paste the URL.",
            "Browse the catalog and install. Apps installed through TrollStore never expire; anything signed "
            "with a certificate has to be re-signed from Feather when it runs out.",
        ],
    },
    "esign": {
        "id": "esign",
        "name": "ESign",
        "scheme": "esign://addsource?url={url}",
        "deepLinkable": True,
        "instructions": (
            "Tap to add the source to ESign. If nothing happens, open ESign → App Sources → + and paste the URL."
        ),
        "manualSetup": False,
        "description": (
            "A signer rather than a store: ESign reads AltStore-format sources, then re-signs and installs each "
            "app itself — no companion computer, but also no background refresh."
        ),
        "requirements": "A certificate you trust on the device (Apple Account, self-signed or enterprise).",
        "steps": [
            "Install ESign (signed with an Apple Account, a self-signed certificate or an enterprise "
            "certificate) and trust that certificate under Settings → General → VPN & Device Management.",
            "Add this source: the link below asks ESign to import it; otherwise open ESign → App Sources → + and "
            "paste the URL.",
            "Open the source in ESign and tap install — ESign signs and drops the app on your home screen. "
            "Re-sign from the same menu before the certificate expires; nothing happens automatically.",
        ],
    },
    "livecontainer": {
        "id": "livecontainer",
        "name": "LiveContainer",
        "scheme": "livecontainer://sources?url={url}",
        "deepLinkable": True,
        "instructions": (
            "Tap to add the source to LiveContainer. If nothing happens, "
            "open LiveContainer → Settings → Sources and paste the URL."
        ),
        "manualSetup": False,
        "description": (
            "Runs sideloaded apps inside containers, so one signature covers all of them — but LiveContainer is "
            "itself installed through another client, so add it where you install apps today."
        ),
        "requirements": (
            "AltStore 2.2.1+, SideStore 0.6.2+, TrollStore or a jailbreak — to install LiveContainer itself."
        ),
        "steps": [
            "Install LiveContainer with AltStore 2.2.1+ or SideStore 0.6.2+ (the LiveContainer+SideStore build "
            "goes in through Impactor or iloader; TrollStore and a jailbreak work too).",
            "Add this source in LiveContainer → Settings → Sources — version 3.7.0 and newer also accept the "
            "one-tap link below.",
            "Install apps from inside LiveContainer: they share its signature, so they do not each take one of "
            "your Apple Account slots.",
        ],
    },
}


def _build_url(profile: dict[str, Any], feed_url: str) -> str:
    template = profile.get("scheme") or ""
    if not template:
        return ""
    if profile.get("id") == "feather":
        parts = urlsplit(feed_url)
        host_path = parts.netloc + parts.path or feed_url.split("://", 1)[-1]
        return f"feather://source/{host_path}"
    return template.format(url=feed_url)


def install_url(client_id: str, feed_url: str) -> str:
    """Return the deep link that adds ``feed_url`` to ``client_id``.

    Unknown client ids return ``""`` — callers fall back to copy-paste.
    """
    profile = CLIENT_PROFILES.get(client_id)
    if profile is None:
        return ""
    return _build_url(profile, feed_url)


def build_install_doc(
    catalog: Catalog,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Build ``feeds/install.json``."""
    base = (base_url or catalog.base_url).rstrip("/")
    # Use the clients declared in the catalog as the source of truth; the
    # CLIENT_PROFILES table is a fallback so a misconfigured catalog does
    # not silently drop a client.
    clients = []
    for client in catalog.clients:
        cid = str(client.get("id") or "")
        if not cid:
            continue
        profile = CLIENT_PROFILES.get(cid)
        if profile is None:
            clients.append(
                {
                    "id": cid,
                    "name": str(client.get("name") or cid.title()),
                    "icon": str(client.get("icon") or ""),
                    "deepLinkable": False,
                    "manualSetup": True,
                    "instructions": f"Open {client.get('name') or cid} and add the source manually.",
                    "scheme": "",
                }
            )
        else:
            clients.append({**profile, "icon": str(client.get("icon") or profile.get("icon", ""))})

    def _cards(feed_url: str) -> list[dict[str, Any]]:
        cards = []
        for client in clients:
            cid = client["id"]
            cards.append(
                {
                    "client": cid,
                    "name": client["name"],
                    "icon": client.get("icon", ""),
                    "compatible": True,
                    "recommended": cid in {"altstore", "sidestore"},
                    "manualSetup": bool(client.get("manualSetup", False)),
                    "url": _build_url(client, feed_url),
                    "feedURL": feed_url,
                    "instructions": client.get("instructions", ""),
                }
            )
        return cards

    apps = []
    for app in catalog.apps:
        feed_url = f"{base}/feeds/{app.slug}.json"
        apps.append(
            {
                "slug": app.slug,
                "name": app.name,
                "feedURL": feed_url,
                "cards": _cards(feed_url),
            }
        )
    # Catalog-level "add the whole OmniSource" card (built once, not per app).
    master_feed = f"{base}/apps.json"
    master = {
        "name": str(catalog.source.get("name", "OmniSource")),
        "feedURL": master_feed,
        "cards": _cards(master_feed),
    }
    return {
        "schemaVersion": INSTALL_SCHEMA_VERSION,
        "generatedAt": today(),
        "baseURL": base,
        "clients": clients,
        "master": master,
        "apps": apps,
    }
