# Migration guide

## From the legacy static catalog

1. Keep existing `catalog.json` entries. They remain the hand-maintained source
   declaration and are backward compatible with `apps.json`.
2. Run `python3 scripts/build_canonical.py` to create canonical app
   identities and inspect `data/canonical_apps.json`.
3. Run `python3 scripts/build_release_history.py` to create the
   append-only release ledger.
4. Run `python3 scripts/enrich_metadata.py` to normalize metadata.
5. Build `data/source_registry.json` and intelligence with:

   ```bash
   python3 scripts/registry/build_registry.py
   python3 scripts/intelligence/build_intelligence.py
   ```

6. Generate client, single-app, collection, and API outputs:

   ```bash
   python3 scripts/build_client_feeds.py
   python3 scripts/build_api_v3.py
   ```

7. Validate before replacing a deployment's feed:

   ```bash
   python3 scripts/validation/validate_feed.py feeds/apps.json --allow-duplicates
   python3 scripts/validation/validate_metadata.py feeds/apps.json
   python3 -m unittest discover -s tests
   ```

The legacy root `apps.json`, `api/`, `feeds/*.json`, and `js/` remain supported
and are generated from the same catalog. Do not manually migrate by copying
individual generated records.

## Introducing discovery

Discovery is additive. Run a dry pass first:

```bash
GITHUB_TOKEN=... python3 scripts/discovery/discover_sources.py --dry-run
```

Review `data/quarantine/sources.json`. A candidate must pass structural
validation and explicit verification before it is considered publishable. A
candidate with a valid shape but no verification evidence remains `VALIDATING`;
that is intentional.

## Repository persistence

The domain layer depends on `Repository` rather than files. Existing JSON
snapshots can continue using `JsonRepository`. For a service deployment,
bootstrap a SQLite database with the same document keys, then switch the
adapter through configuration. PostgreSQL/MySQL deployments can implement the
DB-API contract without changing validators or feed renderers. Preserve
`data/release_history.json` as an append-only export during the migration.

## Website migration

GitHub Pages deployments continue to use `index.html`, `js/`, and generated
root assets. Node deployments can build `web/` independently:

```bash
cd web
npm ci
npm run typecheck && npm run lint && npm run build
```

The app consumes local generated snapshots and uses relative URLs; no
localhost API endpoint is required in production.

## Rollback

A publication is a generated commit. Roll back the commit or restore the last
verified metadata snapshot from `scripts/backup/create_backup.py`. Never
restore an unreviewed quarantine record directly to a public feed.
