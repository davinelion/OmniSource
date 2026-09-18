"""Install card generator.

The website and per-app pages need a uniform "install with" card for every
supported client. The catalog only declares the supported clients; this
module combines that with each app's per-app feed URL to produce a fully
typed ``feeds/install.json`` document that the front-end can render without
recomputing deep links.

The result is intentionally not hard-coded — every URL is derived from
``baseURL`` and the per-app feed, so a future host rename is a single edit
to the catalog.

The document also carries the *advice*: ``clients[].bestFor`` (one line per
client, from :data:`CLIENT_GUIDE`) and a ``recommender`` decision tree
(:data:`RECOMMENDER`) that the installation center walks to answer "which
client should I use?". ``clients[].recommended`` marks the default pick
(SideStore: free, and refreshes on the device after a one-time computer setup),
which is what the install cards badge everywhere else.

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
    "flarestore": {
        "id": "flarestore",
        "name": "FlareStore",
        "scheme": "flarestore://source?url={url}",
        "deepLinkable": True,
        "instructions": ("Tap to add the source to FlareStore. If nothing happens, paste the feed URL in Sources."),
        "manualSetup": False,
        "description": (
            "An AltStore-compatible store with extra signing and repo tools. "
            "OmniSource recommends FlareStore for one-tap source install."
        ),
        "requirements": "A signing certificate, TrollStore, or FlareStore's own sideload path.",
        "steps": [
            "Install FlareStore from flarestore.app and trust its certificate if asked.",
            "Add this source: the link below opens FlareStore's importer, or use Sources → Add and paste the URL.",
            "Browse the catalog and install. Re-sign when your certificate expires.",
        ],
    },
    "ksign": {
        "id": "ksign",
        "name": "Ksign",
        "scheme": "ksign://addsource?url={url}",
        "deepLinkable": True,
        "instructions": ("Tap to add the source to Ksign. If nothing happens, open Ksign sources and paste the URL."),
        "manualSetup": False,
        "description": (
            "An ESign-style on-device signer: import AltStore sources, re-sign IPAs with your certificate, and install."
        ),
        "requirements": "A certificate you trust on the device (Apple Account, self-signed or enterprise).",
        "steps": [
            "Install Ksign and trust the signing certificate under Settings → General → VPN & Device Management.",
            "Add this source: the link below asks Ksign to import it; otherwise paste the URL in Sources.",
            "Open the source and tap install. Re-sign from the same menu before the certificate expires.",
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


# ---------------------------------------------------------------------------
# Client guidance
# ---------------------------------------------------------------------------
# Who each client is actually for, and which one OmniSource recommends when the
# reader has no opinion. The website renders this verbatim (install center
# "Which client?" panel, client cards, README), so the advice lives here — one
# place, versioned with the catalog, instead of prose that drifts per page.
#
# The default recommendation is SideStore: free (no certificate to buy), and
# after a one-time computer setup it refreshes its own apps on the device, which
# is the lowest-friction path that does not depend on paying a third party.
# FlareStore is the recommendation when the reader wants no computer *at all*
# and accepts a third-party signing certificate; AltStore is the reference
# client (and the only one Apple notarizes, as AltStore PAL in the EU); Feather
# is the right answer on a jailbroken or TrollStore device, where no signing
# limit applies.
CLIENT_GUIDE: dict[str, dict[str, Any]] = {
    "altstore": {
        "recommended": False,
        "bestFor": "The reference client, and the EU option (AltStore PAL, iOS 17.4+) that installs with no computer.",
        "effort": "AltServer on a Mac/PC, or an EU Apple Account",
        "refresh": "Automatic over Wi-Fi (AltServer) or on-device (PAL)",
        "cost": "Free Apple Account (7-day re-sign) or a paid developer account (365 days)",
    },
    "sidestore": {
        "recommended": True,
        "bestFor": (
            "Most people: free, and after one computer setup it refreshes on the phone with no computer attached."
        ),
        "effort": "A computer once (iloader) + LocalDevVPN kept on",
        "refresh": "Automatic on-device through its local VPN",
        "cost": "Free Apple Account (7-day re-sign) or a paid developer account (365 days)",
    },
    "flarestore": {
        "recommended": False,
        "bestFor": "No computer at all, or no weekly re-signing: it can sell/attach a ~1-year signing certificate.",
        "effort": "Install the app and import a certificate",
        "refresh": "Certificate lasts up to a year; no weekly refresh",
        "cost": "Free with your own certificate, otherwise a paid certificate",
    },
    "feather": {
        "recommended": False,
        "bestFor": "Jailbroken or TrollStore devices, where apps are not limited by Apple's 7-day signing window.",
        "effort": "Jailbreak, TrollStore/ERE, or your own certificate",
        "refresh": "Depends on how the app was signed; TrollStore apps never expire",
        "cost": "Free on TrollStore/jailbreak; otherwise your own certificate",
    },
    "esign": {
        "recommended": False,
        "bestFor": "Signing IPAs by hand with a certificate you already trust.",
        "effort": "A certificate on the device",
        "refresh": "Manual — re-sign before it expires",
        "cost": "Free with your own certificate",
    },
    "ksign": {
        "recommended": False,
        "bestFor": "An ESign-style on-device signer with source import.",
        "effort": "A certificate on the device",
        "refresh": "Manual — re-sign before it expires",
        "cost": "Free with your own certificate",
    },
    "livecontainer": {
        "recommended": False,
        "bestFor": "Running many apps under one signature without spending App ID slots.",
        "effort": "Installed through AltStore/SideStore/TrollStore first",
        "refresh": "Inherits the host client's refresh",
        "cost": "Free (rides on the host client's signature)",
    },
}

# Decision tree for "Which client should I use?".
#
# Data, not code: every question either points at another question (`next`) or
# ends at a client (`result`), so the install center can walk it without a
# second copy of the advice, and a test can assert every path terminates at a
# client that actually exists. Keep ids in sync with CLIENT_PROFILES.
RECOMMENDER: dict[str, Any] = {
    "default": "sidestore",
    "title": "Which client should I use?",
    "intro": "Four questions, one recommendation. Nothing is installed by answering.",
    "questions": [
        {
            "id": "device",
            "label": "What is your device running?",
            "options": [
                {"id": "stock", "label": "Stock iOS, no jailbreak", "next": "computer"},
                {"id": "trollstore", "label": "Jailbroken or TrollStore", "result": "feather"},
            ],
        },
        {
            "id": "computer",
            "label": "Can you connect the phone to a Mac or PC once?",
            "options": [
                {"id": "yes", "label": "Yes", "next": "after-setup"},
                {"id": "no", "label": "No computer at all", "next": "no-computer"},
            ],
        },
        {
            "id": "after-setup",
            "label": "After that one-time setup, how should apps stay alive?",
            "options": [
                {
                    "id": "on-device",
                    "label": "Refresh on the phone — leave the computer out of it",
                    "result": "sidestore",
                },
                {"id": "over-wifi", "label": "Refresh over Wi-Fi from the computer", "result": "altstore"},
                {
                    "id": "year",
                    "label": "Don't care, as long as it lasts — no weekly re-signing",
                    "result": "flarestore",
                },
            ],
        },
        {
            "id": "no-computer",
            "label": "No computer — which of these fits?",
            "options": [
                {"id": "eu", "label": "I'm in the EU on iOS 17.4+", "result": "altstore"},
                {"id": "cert", "label": "I have (or will buy) a signing certificate", "result": "flarestore"},
                {"id": "none", "label": "Neither — keep it simple", "result": "sidestore"},
            ],
        },
    ],
}


def _prune_recommender(
    recommender: dict[str, Any],
    client_ids: set[str],
) -> dict[str, Any] | None:
    """Drop recommender branches that name a client this catalog does not carry.

    The tree is written against the full client set, but ``catalog.json`` is the
    source of truth: a catalog that lists no FlareStore must not recommend one.
    Options whose result names an absent client are removed, questions that lose
    every option are removed, and only questions reachable from the first
    question survive. Returns ``None`` when nothing usable is left — the
    document then simply omits ``recommender`` and the install center falls back
    to the plain client grid.
    """
    source = [dict(question) for question in recommender.get("questions", [])]
    root_id = str(source[0].get("id")) if source else ""
    if not root_id:
        return None

    questions: dict[str, dict[str, Any]] = {}
    for question in source:
        options = []
        for option in question.get("options", []):
            result = str(option.get("result") or "")
            nxt = str(option.get("next") or "")
            if (result and result in client_ids) or (nxt and nxt in {str(q.get("id")) for q in source}):
                options.append(dict(option))
        questions[str(question.get("id"))] = {**question, "options": options}

    # Remove questions that lost every option, then re-filter, until stable.
    changed = True
    while changed:
        changed = False
        for question_id, question in list(questions.items()):
            kept = [
                option for option in question["options"] if not option.get("next") or str(option["next"]) in questions
            ]
            if kept != question["options"]:
                question["options"] = kept
                changed = True
            if not kept:
                del questions[question_id]
                changed = True

    # Keep only what the root can reach, preserving the declared order.
    reachable: set[str] = set()
    pending = [root_id]
    while pending:
        question_id = pending.pop()
        if question_id in reachable or question_id not in questions:
            continue
        reachable.add(question_id)
        for option in questions[question_id]["options"]:
            if option.get("next"):
                pending.append(str(option["next"]))
    ordered = [
        {**questions[str(question.get("id"))], "options": questions[str(question.get("id"))]["options"]}
        for question in source
        if str(question.get("id")) in reachable
    ]
    if root_id not in reachable or not ordered:
        return None

    default = str(recommender.get("default") or "")
    if default not in client_ids:
        # The default pick is gone: use the first client the tree can actually
        # recommend, so `default` is never a client the page cannot link.
        default = ""
        for question in ordered:
            for option in question["options"]:
                if option.get("result"):
                    default = str(option["result"])
                    break
            if default:
                break
    if not default:
        return None
    return {**recommender, "default": default, "questions": ordered}


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
        # `url` is the catalog's "get the client" homepage; install.json used to
        # drop it, so the install center's "Get <client> ↗" link never rendered.
        guide = dict(CLIENT_GUIDE.get(cid, {}))
        site = str(client.get("url") or "")
        profile = CLIENT_PROFILES.get(cid)
        if profile is None:
            clients.append(
                {
                    "id": cid,
                    "name": str(client.get("name") or cid.title()),
                    "icon": str(client.get("icon") or ""),
                    "url": site,
                    "deepLinkable": False,
                    "manualSetup": True,
                    "recommended": False,
                    "bestFor": "",
                    "instructions": f"Open {client.get('name') or cid} and add the source manually.",
                    "scheme": "",
                }
            )
        else:
            clients.append(
                {
                    **profile,
                    **guide,
                    "icon": str(client.get("icon") or profile.get("icon", "")),
                    "url": site or str(profile.get("url") or ""),
                }
            )

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
                    "recommended": bool(client.get("recommended", False)),
                    "bestFor": client.get("bestFor", ""),
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
    recommender = _prune_recommender(RECOMMENDER, {str(client["id"]) for client in clients})
    document: dict[str, Any] = {
        "schemaVersion": INSTALL_SCHEMA_VERSION,
        "generatedAt": today(),
        "baseURL": base,
        "clients": clients,
        "master": master,
        "apps": apps,
    }
    if recommender is not None:
        document["recommender"] = recommender
    return document
