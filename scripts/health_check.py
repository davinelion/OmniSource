#!/usr/bin/env python3
"""Automated download-link health check.

Reads every distributable feed under ``feeds/``, collects each app's
``downloadURL`` (primary) and ``fallbackDownloadURLs`` (mirrors), and performs
an HTTP HEAD request against every one of them — falling back to a one-byte
ranged GET for hosts that reject HEAD, so a probe never downloads a whole IPA.

A Markdown report is written to stdout and, when running on GitHub Actions, to
the job summary. With ``--report-issue`` the report is filed as a GitHub Issue
(created on first failure, appended to the open issue afterwards), giving the
maintainer a durable, linkable record of every broken download. When a later
run finds every link reachable again, that open issue is commented on and
closed, so the tracker never keeps an issue that upstream has already fixed.

Exit codes
----------
    0  every link reachable, or broken links were reported to an issue
    1  broken links found and ``--report-issue`` was not given
    2  usage / environment error
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS in sys.path:
    sys.path.remove(_SCRIPTS)
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
sys.path.insert(0, _SRC)

from omnisource.constants import ALTSTORE_NON_FEED
from omnisource.http import HttpClient
from omnisource.io import read_json

REPO_ROOT = Path(__file__).resolve().parents[1]
FEEDS_DIR = REPO_ROOT / "feeds"
# Shared with the pipeline so intelligence documents are never probed as feeds.
NON_FEED_FILES = ALTSTORE_NON_FEED
ISSUE_LABEL = "broken-link"
ISSUE_TITLE = "🔗 Broken download links detected"

# The same prober the sync pipeline uses (HEAD, one-byte ranged-GET fallback,
# no credentials on download URLs), so both jobs always agree on reachability.
_CLIENT = HttpClient()


def probe_url(url: str, *, timeout: float = 15.0, retries: int = 2) -> tuple[bool, str]:
    """Return ``(reachable, detail)`` for a download URL, without credentials."""
    result = _CLIENT.probe(url, timeout=timeout, retries=retries)
    return result.reachable, result.detail


def iter_targets() -> list[dict[str, str]]:
    """Collect (app, kind, url) triples from every per-app feed."""
    feeds = sorted(p for p in FEEDS_DIR.glob("*.json") if p.name not in NON_FEED_FILES and p.name != "apps.json")
    if not feeds:
        return []

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(app_name: str, kind: str, urls: Any) -> None:
        for url in urls or []:
            if not isinstance(url, str) or not url:
                continue
            key = (app_name, kind, url)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"app": app_name, "kind": kind, "url": url})

    for path in feeds:
        feed = read_json(path)
        if not isinstance(feed, dict):
            raise SystemExit(f"health-check: cannot read {path}: not a JSON object")
        entries = feed.get("apps", [])
        for app in entries:
            if not isinstance(app, dict):
                continue
            name = str(app.get("name", path.stem))
            add(name, "primary", [app.get("downloadURL")])
            add(name, "fallback", app.get("fallbackDownloadURLs"))
            for version in app.get("versions", []):
                if not isinstance(version, dict):
                    continue
                add(name, "primary", [version.get("downloadURL")])
                add(name, "fallback", version.get("fallbackDownloadURLs"))
    return targets


def render_report(results: list[dict[str, Any]], broken: list[dict[str, Any]]) -> str:
    lines = [
        "## Download health check",
        "",
        f"- **URLs probed:** {len(results)}",
        f"- **Reachable:** {len(results) - len(broken)}",
        f"- **Broken:** {len(broken)}",
        "",
    ]
    if not broken:
        lines.append("✅ All download URLs are reachable.")
        return "\n".join(lines)

    lines += [
        "| App | Type | URL | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for item in broken:
        lines.append(f"| **{item['app']}** | {item['kind']} | [`{item['url']}`]({item['url']}) | `{item['detail']}` |")
    lines += [
        "",
        "The primary URL for a broken entry is returned to your users by the "
        "sideloading clients; configure a `fallbackDownloadURLs` mirror in "
        "`catalog.json` to keep the app installable while the upstream link is down.",
    ]
    return "\n".join(lines)


def ensure_label(gh_bin: str, env: dict[str, str], *, repo: str, label: str) -> None:
    """Best-effort creation of the tracking label.

    ``gh issue create --label`` fails outright (HTTP 422) when the label does
    not exist yet, which would turn a broken-link report into a broken
    workflow. Creating a label only needs the ``issues: write`` scope the
    workflow already grants, so the checker provisions it on demand instead of
    assuming repository settings. Re-creating an existing label errors
    harmlessly and is ignored.
    """
    subprocess.run(
        [
            gh_bin,
            "label",
            "create",
            label,
            "--repo",
            repo,
            "--color",
            "D93F0B",
            "--description",
            "A download link in a published feed is unreachable",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _gh_binary() -> str:
    gh_bin = shutil.which("gh")
    if not gh_bin:
        raise SystemExit("health-check: --report-issue requires the 'gh' CLI to be installed")
    return gh_bin


def _gh_env() -> dict[str, str]:
    env = dict(os.environ, GH_TOKEN=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    if not env["GH_TOKEN"]:
        raise SystemExit("health-check: --report-issue requires GH_TOKEN/GITHUB_TOKEN")
    return env


def open_issue_number(gh_bin: str, env: dict[str, str], *, repo: str, label: str) -> str:
    """Number of the most recent open issue carrying ``label`` (``""`` when none)."""
    list_proc = subprocess.run(
        [
            gh_bin,
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            label,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    number = list_proc.stdout.strip()
    return number if number.isdigit() else ""


def report_issue(report: str, *, repo: str, label: str, title: str) -> None:
    """File a new issue, or append to the most recent open one with the label."""
    gh_bin = _gh_binary()
    env = _gh_env()
    number = open_issue_number(gh_bin, env, repo=repo, label=label)
    if number:
        subprocess.run([gh_bin, "issue", "comment", "--repo", repo, number, "--body", report], env=env, check=True)
        print(f"health-check: appended report to existing issue #{number}")
    else:
        ensure_label(gh_bin, env, repo=repo, label=label)
        subprocess.run(
            [gh_bin, "issue", "create", "--repo", repo, "--title", title, "--label", label, "--body", report],
            env=env,
            check=True,
        )
        print("health-check: filed a new broken-link issue")


def resolve_issue(report: str, *, repo: str, label: str) -> None:
    """Close the open broken-link tracker once every link is reachable again.

    The reporter only ever created or appended to the issue, so a link that
    upstream fixed kept the tracker open forever — the repository's own only
    open issue was a 404 that had already left the feed. Closing is idempotent:
    with no open issue the function does nothing.
    """
    gh_bin = _gh_binary()
    env = _gh_env()
    number = open_issue_number(gh_bin, env, repo=repo, label=label)
    if not number:
        print("health-check: no open broken-link issue to close")
        return
    footer = "Every download URL in `feeds/` is reachable again, so this tracker is closed automatically."
    body = f"{report}\n\n---\n\n{footer}"
    subprocess.run([gh_bin, "issue", "comment", "--repo", repo, number, "--body", body], env=env, check=True)
    subprocess.run([gh_bin, "issue", "close", "--repo", repo, number, "--reason", "completed"], env=env, check=True)
    print(f"health-check: closed resolved issue #{number}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument("--workers", type=int, default=8, help="concurrent probes")
    parser.add_argument("--report-issue", action="store_true", help="file/update a GitHub Issue with the report")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/repo for gh")
    parser.add_argument("--label", default=ISSUE_LABEL, help="issue label")
    parser.add_argument("--title", default=ISSUE_TITLE, help="issue title")
    args = parser.parse_args(argv)

    targets = iter_targets()
    if not targets:
        print("health-check: no download URLs found in feeds/")
        return 2

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(probe_url, target["url"], timeout=args.timeout): target for target in targets}
        for future in concurrent.futures.as_completed(futures):
            target = futures[future]
            reachable, detail = future.result()
            results.append({**target, "reachable": reachable, "detail": detail})
            status = "ok" if reachable else "BROKEN"
            print(f"{status:6} {target['kind']:8} {target['app']:18} {target['url']}  ({detail})")

    broken = [item for item in results if not item["reachable"]]
    report = render_report(results, broken)
    print(f"\n{report}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report + "\n")

    if not broken:
        if args.report_issue:
            if not args.repo:
                print("health-check: --report-issue requires --repo (or GITHUB_REPOSITORY)")
                return 2
            resolve_issue(report, repo=args.repo, label=args.label)
        return 0

    if args.report_issue:
        if not args.repo:
            print("health-check: --report-issue requires --repo (or GITHUB_REPOSITORY)")
            return 2
        report_issue(report, repo=args.repo, label=args.label, title=args.title)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
