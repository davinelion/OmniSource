"""Sourcing policy: the decision layer behind ``docs/SOURCING-REPORT*.md``.

OmniSource publishes an app only when the build comes from that app's own
release channel (its GitHub/GitLab/Codeberg releases, or the developer's own
AltStore/Feather feed). Everything else — account-gated "decrypted app store"
frontpages, aggregator IPA libraries, cracked-package repos — is rejected at
review time. That rejection used to live only in prose, which meant the
discovery pipeline could keep re-proposing the same hosts forever and a human
had to re-litigate each one.

This module turns the prose into data: ``data/source_policy.json`` lists the
rules, and every entry point that could pull a third-party source toward a
client feed consults it.

* :func:`decide` — is this URL / record name blocked, and by which rule?
* :func:`catalog_violations` — offline ``scripts/validate.py`` gate: no
  catalog entry may resolve from, or mirror, a blocked host.
* :func:`blocked_candidate` — used by discovery to drop a candidate before it
  is even merged into ``data/discovered_sources.json``.
* :func:`assert_publishable` (:mod:`omnisource.remote_validation`) refuses a
  blocked record, so a rule cannot be bypassed by a hand-edited store.
* ``.github/workflows/build-tweak.yml`` refuses to download a base IPA from a
  blocked host (see :func:`main`), so the patcher cannot be pointed at a
  cracked-app storefront.

Matching is deliberately narrow and auditable: a rule blocks an exact host (or
its subdomains), an optional path prefix under that host, a specific GitHub
repository, or a name keyword. Nothing here decides what is *good* — absence
of a rule means "no automatic verdict, review it like before".

Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

POLICY_VERSION = 1
POLICY_RELATIVE_PATH = Path("data/source_policy.json")
SCHEMA_RELATIVE_PATH = Path("schemas/source-policy.schema.json")

# GitHub hosts whose *path* identifies the repository that publishes a feed.
_REPO_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
_REPO_PATH_RE = re.compile(r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/|$)")


@dataclass(frozen=True)
class Rule:
    """One sourcing verdict."""

    id: str
    description: str
    reason: str
    hosts: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()
    name_keywords: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    decided_at: str = ""

    def matches_url(self, host: str, path: str) -> bool:
        """Match an entry of ``hosts``, each written as ``host`` or ``host/prefix``.

        A bare host blocks the whole site (and its subdomains); a host written
        with a path blocks only that subtree, so a site that is otherwise
        unrelated can still be reviewed on its other paths.
        """
        host = (host or "").casefold().rstrip(".")
        path = path or "/"
        for entry in self.hosts:
            blocked_host, _, tail = entry.casefold().rstrip("/").partition("/")
            prefix = "/" + tail if tail else ""
            if not (host == blocked_host or host.endswith("." + blocked_host)):
                continue
            if not prefix or path.startswith(prefix):
                return True
        return False

    def matches_repo(self, repo: str) -> bool:
        repo = (repo or "").casefold()
        return bool(repo) and repo in {item.casefold() for item in self.repos}

    def matches_name(self, name: str) -> bool:
        name = (name or "").casefold()
        return any(keyword.casefold() in name for keyword in self.name_keywords if keyword)


@dataclass
class Policy:
    """The loaded rule set plus the record of what it rejected."""

    rules: list[Rule] = field(default_factory=list)
    path: Path | None = None
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.rules)


@dataclass(frozen=True)
class Decision:
    """Outcome of :func:`decide`."""

    blocked: bool
    rule_id: str = ""
    reason: str = ""
    matched_on: str = ""

    @property
    def detail(self) -> str:
        if not self.blocked:
            return "allowed by sourcing policy"
        return f"blocked by sourcing policy rule '{self.rule_id}' ({self.matched_on}): {self.reason}"


_ALLOWED = Decision(blocked=False)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def default_policy_path(root: Path) -> Path:
    return root / POLICY_RELATIVE_PATH


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def parse_policy(document: Any, *, path: Path | None = None) -> Policy:
    """Build a :class:`Policy` from an already-parsed document.

    A malformed policy is reported through ``Policy.error`` instead of raising:
    the callers are validation gates, and a gate that crashes on bad input
    would be a way to make CI red for the wrong reason.
    """
    if document is None:
        return Policy(rules=[], path=path)
    if not isinstance(document, dict):
        return Policy(rules=[], path=path, error="policy root must be a JSON object")
    version = document.get("version")
    if version not in (None, POLICY_VERSION):
        return Policy(rules=[], path=path, error=f"unsupported policy version {version!r}")
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list):
        return Policy(rules=[], path=path, error="'rules' must be an array")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            return Policy(rules=[], path=path, error=f"rules[{index}] must be an object")
        rule_id = str(raw.get("id") or "").strip()
        if not rule_id:
            return Policy(rules=[], path=path, error=f"rules[{index}]: 'id' is required")
        if rule_id in seen:
            return Policy(rules=[], path=path, error=f"rules[{index}]: duplicate rule id '{rule_id}'")
        seen.add(rule_id)
        rule = Rule(
            id=rule_id,
            description=str(raw.get("description") or ""),
            reason=str(raw.get("reason") or ""),
            hosts=_as_tuple(raw.get("hosts")),
            repos=_as_tuple(raw.get("repos")),
            name_keywords=_as_tuple(raw.get("nameKeywords")),
            references=_as_tuple(raw.get("references")),
            decided_at=str(raw.get("decidedAt") or ""),
        )
        if not (rule.hosts or rule.repos or rule.name_keywords):
            return Policy(
                rules=[],
                path=path,
                error=f"rule '{rule_id}' matches nothing: fill hosts, repos or nameKeywords",
            )
        if not rule.reason:
            return Policy(rules=[], path=path, error=f"rule '{rule_id}' must state a reason")
        # Entries are written as 'host' or 'host/prefix'. An entry that is not
        # shaped like that can never match, so accepting it would let a rule look
        # enforced while blocking nothing — reject it at load time instead.
        for entry in rule.hosts:
            host = entry.split("/", 1)[0]
            if "://" in entry or " " in entry or "." not in host or not all(part for part in entry.split("/")):
                return Policy(
                    rules=[],
                    path=path,
                    error=f"rule '{rule_id}' host entry must be 'host' or 'host/prefix', got {entry!r}",
                )
        rules.append(rule)
    return Policy(rules=rules, path=path)


def load_policy(root: Path) -> Policy:
    """Load ``data/source_policy.json``; a missing file is an empty policy."""
    path = default_policy_path(root)
    if not path.is_file():
        return Policy(rules=[], path=path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return Policy(rules=[], path=path, error=f"{path.name}: invalid JSON ({error})")
    return parse_policy(document, path=path)


def validate_policy(document: Any) -> list[str]:
    """Schema-ish checks for the policy file itself (used by the validator)."""
    policy = parse_policy(document)
    errors: list[str] = []
    if policy.error:
        errors.append(f"{POLICY_RELATIVE_PATH}: {policy.error}")
    for rule in policy.rules:
        if not rule.description:
            errors.append(f"{POLICY_RELATIVE_PATH}: rule '{rule.id}' needs a description")
        if not rule.references:
            errors.append(f"{POLICY_RELATIVE_PATH}: rule '{rule.id}' needs at least one reference")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rule.decided_at or ""):
            errors.append(f"{POLICY_RELATIVE_PATH}: rule '{rule.id}' needs an ISO decidedAt")
        for url in rule.references:
            if not url.startswith("https://"):
                errors.append(f"{POLICY_RELATIVE_PATH}: rule '{rule.id}' reference must be HTTPS: {url}")
    return errors


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------
def _repo_in_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.casefold() not in _REPO_HOSTS:
        return ""
    match = _REPO_PATH_RE.match(parsed.path or "")
    if not match:
        return ""
    return f"{match.group('owner')}/{match.group('repo')}"


def decide(url: str = "", *, name: str = "", policy: Policy | None = None) -> Decision:
    """Return the policy verdict for a candidate source URL / record name."""
    if policy is None or not policy.rules:
        return _ALLOWED
    parsed = urlparse(url or "")
    host = parsed.netloc.casefold()
    repo = _repo_in_url(url or "")
    for rule in policy.rules:
        if rule.repos and repo and rule.matches_repo(repo):
            return Decision(True, rule.id, rule.reason, f"repository {repo}")
        if rule.hosts and host and rule.matches_url(host, parsed.path or "/"):
            return Decision(True, rule.id, rule.reason, f"host {host}{parsed.path if '/' in host else ''}")
        if rule.name_keywords and name and rule.matches_name(name):
            return Decision(True, rule.id, rule.reason, f"name {name!r}")
    return _ALLOWED


def partition_by_policy(records: Any, policy: Policy | None) -> tuple[list[Any], list[Any]]:
    """Split discovery records into ``(allowed, excluded)``.

    Discovery uses this before validation so that an already-adjudicated host is
    dropped as a *decision* (with its rule id in the log) rather than as a
    malformed payload, and so the store stops re-collecting it.
    """
    items = list(records or [])
    if not policy or not policy.rules:
        return items, []
    allowed: list[Any] = []
    excluded: list[Any] = []
    for record in items:
        url = str(record.get("url") or "") if isinstance(record, dict) else str(record or "")
        name = str(record.get("name") or "") if isinstance(record, dict) else ""
        target = excluded if decide(url, name=name, policy=policy).blocked else allowed
        target.append(record)
    return allowed, excluded


# ---------------------------------------------------------------------------
# Catalog gate
# ---------------------------------------------------------------------------
def _app_urls(app: dict[str, Any]) -> list[tuple[str, str]]:
    """Every externally-fetched URL an app entry points at, with a label."""
    found: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            found.append((label, value))

    add("upstreamURL", app.get("upstreamURL"))
    add("sourceURL", app.get("sourceURL"))
    upstream = app.get("upstream")
    if isinstance(upstream, dict):
        add("upstream.feedURL", upstream.get("feedURL") or upstream.get("feedUrl"))
        add("upstream.url", upstream.get("url"))
        add("upstream.host", upstream.get("host"))
        mirrors = upstream.get("mirrors")
        if isinstance(mirrors, list):
            for index, mirror in enumerate(mirrors):
                if isinstance(mirror, dict):
                    add(
                        f"upstream.mirrors[{index}]",
                        mirror.get("url") or mirror.get("feedURL") or mirror.get("feedUrl"),
                    )
    manual = app.get("manualRelease")
    if isinstance(manual, dict):
        add("manualRelease.downloadURL", manual.get("downloadURL"))
    for key in ("fallbackDownloadURLs",):
        value = app.get(key)
        if isinstance(value, list):
            for index, item in enumerate(value):
                add(f"{key}[{index}]", item)
    return found


def catalog_violations(catalog: Any, policy: Policy | None) -> list[str]:
    """Errors for catalog entries that resolve from a blocked source.

    This is the fail-closed half of the policy: discovery can be re-run and
    re-discover a blocked feed, but no catalog entry can ever be *merged* while
    it points at one, so the blocked host cannot reach a client feed.
    """
    if not policy:
        return []
    errors: list[str] = []
    apps = catalog.get("apps") if isinstance(catalog, dict) else None
    if not isinstance(apps, list):
        return errors
    for app in apps:
        if not isinstance(app, dict):
            continue
        slug = str(app.get("slug") or "?")
        for label, url in _app_urls(app):
            decision = decide(url, policy=policy)
            if decision.blocked:
                errors.append(f"catalog.json: {slug}.{label} {url} is {decision.detail}")
        verification = app.get("verification")
        publisher = str(verification.get("publisher") or "") if isinstance(verification, dict) else ""
        decision = decide("", name=publisher, policy=policy)
        if decision.blocked:
            errors.append(f"catalog.json: {slug}.verification.publisher is {decision.detail}")
    return errors


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def policy_summary(policy: Policy) -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "path": str(policy.path) if policy.path else "",
        "error": policy.error,
        "rules": [
            {
                "id": rule.id,
                "description": rule.description,
                "reason": rule.reason,
                "hosts": list(rule.hosts),
                "repos": list(rule.repos),
                "nameKeywords": list(rule.name_keywords),
                "references": list(rule.references),
                "decidedAt": rule.decided_at,
            }
            for rule in policy.rules
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """``python3 -m omnisource.source_policy`` — inspect and enforce the policy.

    ``check-url`` exits 1 when a URL is blocked, which is how the tweak
    patcher workflow refuses an account-gated cracked-app storefront as its
    base-IPA source.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python3 -m omnisource.source_policy", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    sub = parser.add_subparsers(dest="command", required=True)

    explain = sub.add_parser("explain", help="print every rule and what it blocks")
    explain.add_argument("--json", action="store_true", help="machine-readable output")

    check = sub.add_parser("check-url", help="apply the policy to one or more URLs")
    check.add_argument("urls", nargs="+")

    catalog = sub.add_parser("check-catalog", help="apply the policy to catalog.json")
    catalog.add_argument("--catalog", type=Path)

    args = parser.parse_args(argv)
    policy = load_policy(args.root)
    if policy.error:
        print(f"::error::sourcing policy is unusable: {policy.error}", file=sys.stderr)
        return 2

    if args.command == "explain":
        if args.json:
            print(json.dumps(policy_summary(policy), indent=2, ensure_ascii=False))
        else:
            for rule in policy.rules:
                print(f"{rule.id}  ({', '.join(rule.hosts) or 'no host rule'})")
                print(f"    {rule.description}")
                print(f"    reason: {rule.reason}")
                print(f"    decided: {rule.decided_at}")
        return 0

    if args.command == "check-url":
        failures = 0
        for url in args.urls:
            decision = decide(url, policy=policy)
            if decision.blocked:
                failures += 1
                print(f"BLOCKED {url}\n        {decision.detail}")
            else:
                print(f"OK      {url}")
        return 1 if failures else 0

    catalog_path = args.catalog or (args.root / "catalog.json")
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    errors = catalog_violations(document, policy)
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} catalog entr(y/ies) resolve from a blocked source")
        return 1
    print("OK: no catalog entry resolves from a blocked source")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
