# Operations: Monitoring, Self-Healing, Mirrors, Analytics

Runbook for the autonomous operations layer (all `data/`, all scheduled).

## Health monitoring (`monitoring.yml`, every 30 min)

```
scripts/monitoring/report_status.py  → data/status.json (online/degraded/offline)
scripts/monitoring/check_sources.py  → per-source reachability table (stdout, exit 1 on failure)
scripts/monitoring/check_downloads.py → per-download reachability table
```

`data/status.json` keeps a 30-sample rolling `history` plus a `failing[]`
list. States: `online` (all reachable), `degraded` (partial), `offline`
(total). Probes are anonymous (no credentials), concurrent
(`--workers 16`), HEAD-first with GET fallback, and treat 2xx/3xx as
alive. `--offline` re-emits the previous document untouched.

The legacy daily `health-check.yml` (issue filing) still runs alongside.

## Self-healing (`scripts/selfheal.py`, runs inside `monitoring.yml`)

1. **Detect** — `broken_download` (from health probes),
   `missing_metadata` (critical display fields),
   `removed_release` (via `data/release_history.json`).
2. **Plan** — one action per issue: `retry` (transient errors),
   `repair` (recorded fallback URL), `replace` (mirror — needs review),
   `rebuild` (flag for full resync).
3. **Report** — `data/selfheal_report.json` with `suggestedPatches`.
   `--apply` writes `data/healed_apps.json` (fallback swaps only, for
   review) — pipeline outputs are never rewritten in place.
4. **Re-probe** — `--reprobe` re-checks downloads live before planning.

## Mirror management (`data/mirrors.json`)

Tiers: `github-release` (primary) → `github-pages` → `cdn` → `backup`.
`src/omnisource/mirrors.py` orders candidates (healthy first, then tier)
and `failover()` picks the next URL after a failure.
`scripts/mirror_check.py` writes `data/mirror_status.json`
(protected/unprotected per app).

To configure a mirror, add to `data/mirrors.json`:

```json
{"id": "r2-main", "type": "cdn", "urlTemplate": "https://cdn.example.com/{slug}.ipa", "apps": ["*"]}
```

`apps: ["*"]` covers everything; list slugs to scope. Disabled entries
carry `"enabled": false`. The registry ships as a skeleton — failover
logic is implemented and tested, real endpoints are operator-provided.

## Analytics (`analytics.yml`, daily)

`scripts/analytics_rollup.py` rolls `feeds/analytics.json` history (+ the
monitoring ledger) into `data/analytics_rollup.json`:

- `daily` — last 30 days · `weekly` — last 12 ISO weeks ·
  `monthly` — last 12 calendar months (per-period averages).

Content-stable: no commit when nothing changed. Served by the Statistics
page (both sites) and `GET /api/v3/analytics`.

## Release ledger & rollback

`data/release_history.json` (built by `publish.yml`) is append-only:
entries flip `current` → `superseded` (replaced) or `removed` (yanked
newest). Rollback plan for any previously seen version:

```bash
python3 scripts/build_release_history.py --rollback ytlite --to 2.1.0
```

## Verification & quarantine

Discovery has two separate gates. Structural candidates are stored as
`VALIDATING` or `QUARANTINED`; they do not enter the source registry or any
client feed. The scheduled verifier fetches the candidate HTTPS JSON feed,
validates the envelope/apps, records a canonical feed SHA-256, then promotes
only clean records to `VERIFIED` and (with `--publish`) `PUBLISHED`:

```bash
python3 scripts/validation/validate_source.py
python3 scripts/discovery/verify_sources.py --publish
python3 scripts/registry/build_registry.py
```

`data/published_sources.json` is the only discovered-source projection used by
registry/intelligence builders. A repository or GitHub Pages page without a
verified JSON feed stays quarantined. Scores below 25 are also retained as
quarantine evidence; no missing hash or mirror is fabricated. Weights and
signals are documented in `src/omnisource/reputation.py`.

## Backups and rollback

Create and verify a metadata-only recovery snapshot before a deployment or
incident drill:

```bash
# --label is the retention tier: daily | weekly | monthly | manual (a drill is
# a manual snapshot). create prints the snapshot directory it wrote.
python3 scripts/backup/create_backup.py create --label manual --destination /tmp/omni-backup
# verify and restore take that snapshot directory as a positional argument.
python3 scripts/backup/create_backup.py verify /tmp/omni-backup/manual-20260913T120000Z
# restore is a dry run unless --apply is passed; --root is the tree to restore
# into (only a snapshot whose checksums all verify is restored at all).
python3 scripts/backup/create_backup.py restore /tmp/omni-backup/manual-20260913T120000Z --root /tmp/restore
```

`backup.yml` labels each scheduled snapshot with the tier of the cron that
fired it (`daily` 01:45 UTC, `weekly` Sunday 02:00, `monthly` day 1 at 02:15)
and a manual dispatch as `manual`.

Snapshots exclude IPA/TIPA payloads, caches, credentials, and build outputs.
CI retains daily/weekly/monthly artifacts for 90 days.

## Incident quick-reference

| Symptom | Check | Action |
|---|---|---|
| `data/status.json` → `degraded` | `failing[]`, `monitoring.yml` logs | `selfheal_report.json` plans; `retry` auto-clears transients |
| `security.yml` red | `data/security.json` findings | Fix `critical` (malformed digest) or `--fail-on high` breach, re-run |
| `publish.yml` red | `build_client_feeds --check`, `validate.py` | Never hand-edit `feeds/`; fix `catalog.json`, let `sync` rebuild |
| `discovery.yml` red | `validate_source.py` output | Invalid records self-quarantine (`--quarantine-invalid`); red means one could not be isolated or a verified/published record is invalid — fix it, re-run |
| Empty `monitoring` commit loop | `history` cap is 30 — by design | None; each run appends one sample |
