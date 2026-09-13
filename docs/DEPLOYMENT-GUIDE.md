# Deployment guide

## GitHub Pages (static)

1. Enable Pages from GitHub Actions.
2. Run `publish.yml` or push to `main`; `build-site.yml` publishes the static
   site when the legacy site changes.
3. Confirm `apps.json`, `/api/v3/index.json`, `/feeds/clients/`, and the PWA
   manifest return 200 responses.
4. Set the canonical URL in `config/site.json` if the repository moves.

The static path needs no runtime secrets. It publishes generated feeds and
committed API snapshots only.

## Node / container deployment

Build the modern app in `web/` with Node 22:

```bash
npm ci
npm run typecheck
npm run lint
npm run build
npm run start
```

Put a CDN or reverse proxy in front of the app. Set `HOSTNAME=0.0.0.0` in
container environments and expose a health endpoint through the deployment
platform. Browser requests must remain relative to the deployment origin.

## Scheduled automation

Configure these GitHub Actions permissions and secrets:

- `GITHUB_TOKEN`: the built-in token, used only with GitHub API hosts;
- optional provider secrets: host-specific variables documented in `.env.example`;
- Pages deployment permission for the website/publish workflow;
- no secret is needed for public feed downloads or monitoring.

The workflows are safe to run without optional provider credentials: discovery
uses public endpoints and reports rate-limit or auth failures rather than
publishing unverified data. Review `data/status.json`,
`security-report.json`, and workflow artifacts after first installation.

## Recovery drill

```bash
# A drill is a manual snapshot: --label takes daily | weekly | monthly | manual.
python3 scripts/backup/create_backup.py create --label manual --destination /tmp/omnisource-backup
# create prints the snapshot directory; verify and restore take it positionally.
python3 scripts/backup/create_backup.py verify /tmp/omnisource-backup/manual-20260913T120000Z
# restore is a dry run unless --apply is passed; --root is the tree to restore into.
python3 scripts/backup/create_backup.py restore /tmp/omnisource-backup/manual-20260913T120000Z --root /tmp/restore
```

Backups intentionally contain metadata, manifests, configuration, and source
history; they exclude IPA/TIPA payloads, caches, credentials, and build
artifacts. Keep a retention policy appropriate to the hosting organization.
