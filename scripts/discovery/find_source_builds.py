#!/usr/bin/env python3
"""Find iOS projects worth a *source-build recipe*, and draft the skeleton.

This is the finder for the lane in ``data/source_builds.json``: repositories that
publish source but no artifact a feed could distribute (no ``.ipa``/``.tipa``/
``.deb`` release asset). Those are exactly the projects the catalogue must refuse
and the recipe lane should record, and until now the only way to find them was to
read a list and check each repo by hand.

Nothing is written unless ``--out`` is given, and even then only a *candidate
skeleton* is written: ``summary``, ``commands``, ``signing`` and ``evidence`` stay
blank in the draft, because a recipe whose prose was invented is worse than no
recipe. A human reviews the file and moves entries into ``data/source_builds.json``
(``python3 scripts/build_source.py check`` then has to pass).

Sourcing verdicts apply here too: a repository blocked by ``data/source_policy.json``
is skipped and reported, so an aggregator of other people's IPAs never turns up as a
"build it yourself" suggestion.

Examples:
    python3 scripts/discovery/find_source_builds.py --limit 10
    python3 scripts/discovery/find_source_builds.py --forge codeberg --term theos
    python3 scripts/discovery/find_source_builds.py --hash --out /tmp/candidates.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from omnisource.source_builds import (
    DEFAULT_TERMS,
    FORGE_APIS,
    classify_build_system,
    has_ios_release,
    recipe_skeleton,
)
from omnisource.source_policy import decide, load_policy

ROOT = Path(__file__).resolve().parents[2]
UA = "omnisource-source-build-finder (+https://iamsmmh.github.io/OmniSource)"


def _get(url: str, *, token: str | None = None, raw: bool = False) -> Any:
    """GET a JSON/text payload. Any failure is reported as ``None``, never raised.

    A forge being unreachable or rate-limited must shrink the report, not abort a
    scheduled run: the candidate list is advisory.
    """
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json" if not raw else "text/plain"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            body = response.read()
            return body.decode("utf-8", "replace") if raw else json.loads(body)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _search(forge: str, term: str, limit: int, token: str | None) -> list[dict[str, Any]]:
    api = FORGE_APIS[forge]
    template = api["search"].format(query=urllib.parse.quote_plus(term), limit=max(1, min(limit, 50)))
    payload = _get(template, token=token)
    if forge == "github" and isinstance(payload, dict):
        return [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if forge == "gitlab" and isinstance(payload, list):
        return [
            {
                "full_name": item.get("path_with_namespace"),
                "stargazers_count": item.get("star_count", 0),
                "description": item.get("description"),
                "default_branch": item.get("default_branch"),
                "license": {"spdx_id": (item.get("license") or {}).get("spdx_id")},
                "archived": item.get("archived"),
                "id": item.get("id"),
                "topics": item.get("topics") or [],
            }
            for item in payload
            if isinstance(item, dict)
        ]
    if isinstance(payload, dict):
        return [item for item in payload.get("items", payload.get("data") or []) if isinstance(item, dict)]
    return []


def _root_files(forge: str, repo: str, ref: str, token: str | None) -> list[str]:
    if forge == "github":
        payload = _get(f"https://api.github.com/repos/{repo}/contents?ref={ref}", token=token)
    elif forge == "gitlab":
        payload = _get(
            f"https://gitlab.com/api/v4/projects/{urllib.parse.quote(repo, safe='')}/repository/tree?ref={ref}",
            token=token,
        )
    else:
        payload = _get(f"https://{_host(forge)}/api/v1/repos/{repo}/contents?ref={ref}", token=token)
    if not isinstance(payload, list):
        return []
    return [str(item.get("name")) for item in payload if isinstance(item, dict) and item.get("name")]


def _host(forge: str) -> str:
    hosts = {"github": "github.com", "gitlab": "gitlab.com", "codeberg": "codeberg.org"}
    return hosts.get(forge) or os.environ.get("FORGEJO_HOST", "codeberg.org")


def _makefile_text(repo: str, ref: str, token: str | None) -> str:
    return _get(f"https://api.github.com/repos/{repo}/contents/Makefile?ref={ref}", token=token, raw=True) or ""


def _digest(url: str) -> tuple[str, int]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=180) as blob:
            payload = blob.read()
    except (urllib.error.URLError, OSError):
        return "", 0
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _archive_url(forge: str, repo: str, ref: str, repo_id: str = "", *, tagged: bool = True) -> str:
    """The forge's source archive for ``ref``.

    GitHub distinguishes tag and branch archives by path, so guessing wrong here
    is a 404 rather than an error — which is why the digest step reports "not
    digested" instead of trusting an empty result.
    """
    key = "archive" if forge != "github" or tagged else "archive_branch"
    return FORGE_APIS[forge][key].format(repo=repo, ref=ref, repo_id=repo_id or urllib.parse.quote(repo, safe=""))


def probe(
    forge: str, repo_entry: dict[str, Any], *, token: str | None, hash_archives: bool, policy: Any
) -> tuple[dict[str, Any] | None, str]:
    """Return ``(skeleton, note)``; exactly one of them is set."""
    repo = str(repo_entry.get("full_name") or repo_entry.get("path_with_namespace") or "")
    if not repo or repo_entry.get("archived"):
        return None, f"{repo or '?'}: skipped (archived or unnamed)"
    verdict = decide(f"https://{_host(forge)}/{repo}", name=str(repo_entry.get("description") or ""), policy=policy)
    if verdict.blocked:
        return None, f"{repo}: excluded — {verdict.rule_id}"
    ref = str(repo_entry.get("default_branch") or "")
    if not ref:
        return None, f"{repo}: no default branch reported"
    release = (
        _get(f"https://api.github.com/repos/{repo}/releases/latest", token=token)
        if forge == "github"
        else _get(
            FORGE_APIS[forge]["releases"].format(
                repo=repo, repo_id=repo_entry.get("id") or urllib.parse.quote(repo, safe="")
            ),
            token=token,
        )
    )
    if forge == "gitlab" and isinstance(release, list):
        release = release[0] if release else {}
    published, assets = has_ios_release(release)
    if published:
        return None, f"{repo}: publishes {'/'.join(assets[:2])} — belongs in catalog.json, not here"
    tags = (
        _get(f"https://api.github.com/repos/{repo}/tags?per_page=1", token=token)
        if forge == "github"
        else _get(f"https://{_host(forge)}/api/v1/repos/{repo}/tags?limit=1", token=token)
    )
    tag = ""
    if isinstance(tags, list) and tags and isinstance(tags[0], dict):
        tag = str(tags[0].get("name") or "")
    pin_ref = tag or ref
    files = _root_files(forge, repo, pin_ref, token)
    system = classify_build_system(files, _makefile_text(repo, pin_ref, token) if forge == "github" else "")
    if not system:
        return None, f"{repo}: no recognised build system at {pin_ref}"
    commit = str((tags[0].get("commit", {}) or {}).get("sha", "")) if isinstance(tags, list) and tags else ""
    if forge == "github" and not tag:
        meta = _get(f"https://api.github.com/repos/{repo}/commits/{ref}", token=token)
        commit = str((meta or {}).get("sha", commit))
    archive = _archive_url(forge, repo, pin_ref, str(repo_entry.get("id") or ""), tagged=bool(tag))
    sha256, size = ("", 0)
    if hash_archives and forge == "github":
        sha256, size = _digest(archive)
    skeleton = recipe_skeleton(
        forge=forge,
        repo=repo,
        ref=pin_ref,
        commit=commit,
        committed_at=str(repo_entry.get("pushed_at") or "")[:10],
        name=str(repo_entry.get("name") or repo.rsplit("/", 1)[-1]),
        description=str(repo_entry.get("description") or "").strip(),
        license=str((repo_entry.get("license") or {}).get("spdx_id") or ""),
        system=system,
        archive_url=archive,
        sha256=sha256,
        size=size,
        ref_type="tag" if tag else "commit",
    )
    suggested = {"theos": "make package FINALPACKAGE=1", "dub": "dub build", "go": "go build"}.get(system, "")
    skeleton["build"]["requirements"] = [f"{system} toolchain"]
    skeleton["build"]["commands"] = [suggested] if suggested else []
    return skeleton, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forge", choices=tuple(FORGE_APIS), default="github")
    parser.add_argument("--term", action="append", default=[], help="search term (repeatable)")
    parser.add_argument("--min-stars", type=int, default=25)
    parser.add_argument("--limit", type=int, default=15, help="repositories to probe per term")
    parser.add_argument("--hash", action="store_true", help="download + digest each source archive (GitHub only)")
    parser.add_argument("--out", type=Path, help="write the candidate skeletons here instead of only printing them")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITLAB_TOKEN")
    policy = load_policy(ROOT)
    if policy.error:
        # Fail-closed: without a readable policy the run could suggest a source
        # this project has already refused, which is worse than suggesting nothing.
        print(f"find_source_builds: {policy.error}; refusing to run", file=sys.stderr)
        return 2
    terms = tuple(dict.fromkeys((*DEFAULT_TERMS, *args.term)))
    print(f"find_source_builds: forge={args.forge} terms={', '.join(terms)} min-stars={args.min_stars}")
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    notes: list[str] = []
    for term in terms:
        for entry in _search(args.forge, term, args.limit, token):
            stars = int(entry.get("stargazers_count") or entry.get("star_count") or 0)
            if stars < args.min_stars:
                continue
            repo = str(entry.get("full_name") or entry.get("path_with_namespace") or "")
            if not repo or repo in seen:
                continue
            seen.add(repo)
            skeleton, note = probe(args.forge, entry, token=token, hash_archives=args.hash, policy=policy)
            if skeleton:
                found.append(skeleton)
            elif note:
                notes.append(note)

    for note in notes:
        print(f"  note  {note}")
    for skeleton in found:
        digest = skeleton["source"]["sha256"][:12] if skeleton["source"]["sha256"] else "not digested"
        print(
            f"  draft {skeleton['repo']:<38} {skeleton['build']['system']:<9} "
            f"pin={skeleton['pin']['ref']:<16} digest={digest}"
        )
    print(f"find_source_builds: {len(found)} draft recipe(s), {len(notes)} note(s)")
    print("review each one, fill the blank fields, then add to data/source_builds.json and run:")
    print("    python3 scripts/build_source.py check")
    if args.out:
        document = {"version": 1, "updated": "", "contract": {}, "builds": found}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out} (candidates only — parse/validate before adopting)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
