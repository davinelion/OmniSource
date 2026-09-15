"""Autonomous source-discovery engine.

Discovers third-party AltStore / SideStore / Feather / ESign / LiveContainer
sources without human intervention:

* GitHub code search for feed filenames (``source.json``, ``apps.json``) and
  ecosystem keywords (``altsource``, ``altstore source``, …).
* Direct probing of candidate feed URLs with client-type classification.
* Release-repository detection (repos shipping ``.ipa`` assets).
* Web-catalog scraping (HTML pages that link to feeds).

Findings are stored in ``data/discovered_sources.json`` using the discovery
record schema (``schemas/discovery.schema.json``). Records are never
published to user-facing feeds automatically: the validation engine
(:mod:`omnisource.remote_validation`) must accept a record first, and a
human (or a pinned policy) promotes it into ``catalog.json``.

Stdlib only. Network access is isolated in the ``fetch_*`` helpers so the
pure classification / merge logic stays unit-testable offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnisource.constants import USER_AGENT
from omnisource.errors import ProviderError
from omnisource.io import read_json, write_json

SEARCH_TERMS = (
    "source.json",
    "apps.json",
    "altsource",
    "altstore source",
    "sidestore source",
    "feather source",
)

GITHUB_API = "https://api.github.com"
GITHUB_PAGES_SUFFIX = "github.io"

# Feed filenames worth probing when only a repository or Pages site is known.
FEED_FILENAME_HINTS = (
    "apps.json",
    "source.json",
    "altsource.json",
    "altstore.json",
    "feather.json",
    "esign.json",
    "source/apps.json",
)

KNOWN_TYPES = ("altstore", "sidestore", "feather", "esign", "livecontainer", "unknown")

HEALTH_UNKNOWN = "unknown"
HEALTH_ONLINE = "online"
HEALTH_OFFLINE = "offline"

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_FEED_HREF_RE = re.compile(r"\.json(?:[?#]|$)", re.IGNORECASE)


def utcnow() -> str:
    """Current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def source_id_for_url(url: str) -> str:
    """Stable, filesystem-safe identifier derived from a feed URL.

    The result always satisfies ``SOURCE_ID_RE`` (and therefore
    ``schemas/discovery.schema.json``): every component is reduced to
    ``[a-z0-9-]``, the id is capped at 80 characters and never ends on a
    separator. Hostnames are sanitized like the path stem, so an IDN or an
    otherwise exotic host yields a valid id instead of a quarantined record.
    """
    parsed = urllib.parse.urlparse(url)
    host = re.sub(r"[^a-z0-9-]+", "-", (parsed.hostname or "feed").lower()).strip("-") or "feed"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"[^a-z0-9-]+", "-", parsed.path.strip("/").lower()).strip("-") or "root"
    return f"{host}-{stem[:32]}-{digest}"[:80].rstrip("-")


def new_record(
    *,
    url: str,
    name: str = "",
    feed_type: str = "unknown",
    discovered_at: str | None = None,
    health: str = HEALTH_UNKNOWN,
    reputation: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one discovery record in the canonical store schema."""
    first_seen = discovered_at or utcnow()
    normalized_type = feed_type if feed_type in KNOWN_TYPES else "unknown"
    score = max(0, min(100, int(reputation)))
    record: dict[str, Any] = {
        # Legacy keys remain part of the public discovery contract.
        "source_id": source_id_for_url(url),
        "name": name or url,
        "url": url,
        "type": normalized_type,
        "discovered_at": first_seen,
        "last_checked": first_seen,
        "health": health,
        "reputation": score,
        # Canonical registry/quarantine aliases. Keeping both spellings lets
        # older consumers upgrade without a flag day.
        "source_name": name or url,
        "source_type": normalized_type,
        "source_url": url,
        "first_seen": first_seen,
        "last_seen": first_seen,
        "health_status": health,
        "verification_status": "unverified",
        "reputation_score": score,
    }
    if extra:
        record["meta"] = dict(extra)
    return record


def classify_feed(payload: Any) -> tuple[str, int]:
    """Return ``(client_type, app_count)`` for a decoded JSON document.

    Every supported client consumes the AltStore Source v2 envelope; the
    classifiers below only refine the label from tell-tale markers.
    """
    if not isinstance(payload, dict):
        return "unknown", 0
    apps = payload.get("apps")
    if not isinstance(apps, list):
        return "unknown", 0
    blob = json.dumps(payload)[:20_000].lower()
    if "livecontainer" in blob or "live_container" in blob:
        return "livecontainer", len(apps)
    if "esign" in blob or "e-sign" in blob:
        return "esign", len(apps)
    if "feather" in blob:
        return "feather", len(apps)
    identifier = str(payload.get("identifier", "")).lower()
    if "sidestore" in identifier or "sidestore" in blob:
        return "sidestore", len(apps)
    if payload.get("identifier") and apps:
        return "altstore", len(apps)
    return "unknown", len(apps)


def client_for_url(url: str) -> str:
    """Guess the most likely client family from a feed URL alone."""
    lowered = url.lower()
    for token, client in (
        ("livecontainer", "livecontainer"),
        ("esign", "esign"),
        ("feather", "feather"),
        ("sidestore", "sidestore"),
        ("altstore", "altstore"),
    ):
        if token in lowered:
            return client
    return "unknown"


def _request(url: str, *, token: str | None = None, timeout: int = 20) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch_json(
    url: str,
    *,
    token: str | None = None,
    timeout: int = 20,
    max_bytes: int = 2_000_000,
) -> Any:
    """GET a JSON document, raising :class:`ProviderError` on any failure."""
    try:
        with urllib.request.urlopen(_request(url, token=token, timeout=timeout), timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"GET {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ProviderError(f"GET {url}: {exc}") from exc
    if len(raw) > max_bytes:
        raise ProviderError(f"GET {url}: payload exceeds {max_bytes} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProviderError(f"GET {url}: invalid JSON") from exc


def fetch_text(url: str, *, timeout: int = 20, max_bytes: int = 1_000_000) -> str:
    """GET a text/HTML document, raising :class:`ProviderError` on failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"GET {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ProviderError(f"GET {url}: {exc}") from exc
    if len(raw) > max_bytes:
        raise ProviderError(f"GET {url}: payload exceeds {max_bytes} bytes")
    return raw.decode("utf-8", errors="replace")


def github_code_search(
    term: str,
    *,
    token: str | None = None,
    per_page: int = 30,
    max_pages: int = 3,
    delay: float = 2.0,
) -> list[dict[str, Any]]:
    """Search github.com code for ``term``; return raw ``items`` entries."""
    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"q": term, "per_page": per_page, "page": page})
        payload = fetch_json(f"{GITHUB_API}/search/code?{query}", token=token)
        if not isinstance(payload, dict):
            break
        batch = payload.get("items", [])
        if not isinstance(batch, list) or not batch:
            break
        items.extend(entry for entry in batch if isinstance(entry, dict))
        if len(batch) < per_page:
            break
        time.sleep(delay)
    return items


def raw_url_for_code_hit(hit: dict[str, Any]) -> str | None:
    """Best-effort raw / Pages URL for one GitHub code-search hit."""
    repo = hit.get("repository") or {}
    path = hit.get("path") or ""
    full_name = repo.get("full_name") or ""
    default_branch = repo.get("default_branch") or "main"
    if not full_name or not path:
        return None
    if not path.lower().endswith(".json"):
        return None
    return f"https://raw.githubusercontent.com/{full_name}/{default_branch}/{path}"


def pages_url_for_repo(full_name: str, path: str) -> str | None:
    """Guess the GitHub Pages URL serving ``path`` of ``full_name``."""
    parts = full_name.split("/")
    if len(parts) != 2 or not path:
        return None
    owner, repo = parts
    candidate = path
    for prefix in ("docs/", "public/", "static/"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    if repo.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/{candidate}"
    return f"https://{owner}.github.io/{repo}/{candidate}"


def discover_from_github(
    *,
    token: str | None = None,
    terms: tuple[str, ...] = SEARCH_TERMS,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    """Run every search term and return candidate discovery records."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms:
        try:
            hits = github_code_search(term, token=token, max_pages=max_pages)
        except ProviderError:
            continue
        for hit in hits:
            repo = hit.get("repository") or {}
            full_name = str(repo.get("full_name") or "")
            path = str(hit.get("path") or "")
            url = raw_url_for_code_hit(hit)
            if not url or url in seen:
                continue
            seen.add(url)
            pages = pages_url_for_repo(full_name, path)
            records.append(
                new_record(
                    url=url,
                    name=f"{full_name} — {path}",
                    feed_type=client_for_url(url),
                    extra={
                        "via": "github-code-search",
                        "term": term,
                        "repository": full_name,
                        "path": path,
                        "pages_url": pages,
                        "stars": repo.get("stargazers_count", 0),
                    },
                )
            )
    return records


def probe_feed_url(url: str, *, timeout: int = 20) -> dict[str, Any]:
    """Fetch ``url`` and classify it; never raises."""
    try:
        payload = fetch_json(url, timeout=timeout)
    except ProviderError as exc:
        return {"url": url, "ok": False, "type": "unknown", "apps": 0, "error": str(exc)}
    feed_type, count = classify_feed(payload)
    name = payload.get("name") if isinstance(payload, dict) else ""
    return {
        "url": url,
        "ok": feed_type != "unknown" and count > 0,
        "type": feed_type,
        "apps": count,
        "name": name or url,
        "error": "" if feed_type != "unknown" else "not a recognized feed",
    }


def discover_feeds(urls: list[str], *, timeout: int = 20) -> list[dict[str, Any]]:
    """Probe candidate feed URLs and return records for the live ones."""
    records: list[dict[str, Any]] = []
    for url in urls:
        probe = probe_feed_url(url, timeout=timeout)
        if not probe["ok"]:
            continue
        records.append(
            new_record(
                url=url,
                name=str(probe.get("name") or url),
                feed_type=str(probe.get("type") or "unknown"),
                health=HEALTH_ONLINE,
                extra={"via": "feed-probe", "apps": probe.get("apps", 0)},
            )
        )
    return records


def candidate_feed_urls(base_url: str) -> list[str]:
    """Expand a repository / Pages / site URL into likely feed URLs."""
    base = base_url.rstrip("/")
    if base.lower().endswith(".json"):
        return [base]
    return [f"{base}/{hint}" for hint in FEED_FILENAME_HINTS]


def releases_for_repo(full_name: str, *, token: str | None = None) -> list[dict[str, Any]]:
    """Return the GitHub Releases list for ``owner/repo`` (may be empty)."""
    payload = fetch_json(f"{GITHUB_API}/repos/{full_name}/releases?per_page=10", token=token)
    return list(payload) if isinstance(payload, list) else []


def has_ipa_assets(release: dict[str, Any]) -> bool:
    """True when any release asset looks like an installable iOS binary."""
    assets = release.get("assets") or []
    return any(
        isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith((".ipa", ".tipa")) for asset in assets
    )


def discover_releases(
    repos: list[str],
    *,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Detect repositories that publish installable ``.ipa`` releases."""
    records: list[dict[str, Any]] = []
    for full_name in repos:
        try:
            releases = releases_for_repo(full_name, token=token)
        except ProviderError:
            continue
        ipa_releases = [entry for entry in releases if isinstance(entry, dict) and has_ipa_assets(entry)]
        if not ipa_releases:
            continue
        newest = ipa_releases[0]
        records.append(
            new_record(
                url=f"https://github.com/{full_name}/releases",
                name=full_name,
                feed_type="unknown",
                health=HEALTH_ONLINE,
                extra={
                    "via": "release-scan",
                    "repository": full_name,
                    "tag": newest.get("tag_name", ""),
                    "ipa_releases": len(ipa_releases),
                },
            )
        )
    return records


def extract_feed_links(html: str, base_url: str) -> list[str]:
    """Pull absolute ``*.json`` links out of an HTML catalog page."""
    links: list[str] = []
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if not _FEED_HREF_RE.search(href):
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
    return links


def discover_web_catalogs(pages: list[str], *, timeout: int = 20) -> list[dict[str, Any]]:
    """Scrape catalog pages for linked feeds and probe each candidate."""
    candidates: list[str] = []
    for page in pages:
        try:
            html = fetch_text(page, timeout=timeout)
        except ProviderError:
            continue
        for link in extract_feed_links(html, page):
            if link not in candidates:
                candidates.append(link)
    records = discover_feeds(candidates, timeout=timeout)
    for record in records:
        meta = record.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["via"] = "web-catalog"
    return records


def merge_records(existing: list[dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge ``found`` into ``existing`` keyed by URL; refresh ``last_checked``."""
    by_url = {str(entry.get("url")): dict(entry) for entry in existing if entry.get("url")}
    now = utcnow()
    for entry in found:
        url = str(entry.get("url", ""))
        if not url:
            continue
        if url in by_url:
            current = by_url[url]
            current["last_checked"] = now
            current["last_seen"] = now
            if entry.get("health") not in (None, HEALTH_UNKNOWN):
                current["health"] = entry["health"]
                current["health_status"] = entry["health"]
            for key in ("name", "type", "source_name", "source_type", "verification_status"):
                if entry.get(key):
                    current[key] = entry[key]
            if entry.get("reputation") is not None:
                current["reputation"] = entry["reputation"]
                current["reputation_score"] = entry["reputation"]
        else:
            fresh = dict(entry)
            fresh.setdefault("discovered_at", now)
            fresh.setdefault("first_seen", fresh["discovered_at"])
            fresh["last_checked"] = now
            fresh["last_seen"] = now
            fresh.setdefault("source_name", fresh.get("name", url))
            fresh.setdefault("source_type", fresh.get("type", "unknown"))
            fresh.setdefault("source_url", url)
            fresh.setdefault("health_status", fresh.get("health", HEALTH_UNKNOWN))
            fresh.setdefault("verification_status", "unverified")
            fresh.setdefault("reputation_score", fresh.get("reputation", 0))
            by_url[url] = fresh
    return [by_url[key] for key in sorted(by_url)]


def load_store(path: Any) -> dict[str, Any]:
    """Load the discovery store (empty skeleton when missing)."""
    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        return data
    return {"schemaVersion": 1, "generatedAt": utcnow(), "count": 0, "sources": []}


def save_store(path: Any, sources: list[dict[str, Any]], *, root: Any = None) -> bool:
    """Persist ``sources`` to the discovery store.

    With ``root`` set, the recorded sourcing verdicts (``data/source_policy.json``)
    are enforced on the way in: a candidate from a rejected source is dropped, and
    so is any already-stored record for one. Enforcement lives here rather than in
    each caller, because the store has many writers — GitHub search, the GitLab /
    Codeberg / Forgejo sweep, feed scraping, web-catalog crawling, manual
    revalidation — and any one of them forgetting to filter would reintroduce a
    source the project has already reviewed and refused. An unusable policy file
    aborts the write instead of silently skipping the check.
    """
    kept = list(sources)
    if root is not None:
        from omnisource.source_policy import load_policy, partition_by_policy  # local import: no cycle

        policy = load_policy(Path(root))
        if policy.error:
            print(f"discovery: sourcing policy is unusable ({policy.error}); store not written")
            return False
        kept, blocked = partition_by_policy(kept, policy)
        for record in blocked:
            print(f"discovery: not storing {record.get('url')} — excluded by sourcing policy")
    return write_json(
        path,
        {"schemaVersion": 1, "generatedAt": utcnow(), "count": len(kept), "sources": kept},
    )
