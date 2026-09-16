.PHONY: all build publish site serve smoke validate metadata test format lint check audit clean version discovery monitoring security analytics derived registry intelligence backup web recipes recipes-find

PYTHON ?= python3
PORT ?= 8000
export PYTHONPATH := src

all: build check

# Print the installed OmniSource version.
version:
	$(PYTHON) -m omnisource --version

# Refresh generated source feeds from their configured upstreams.
build:
	$(PYTHON) scripts/omnisource.py

# Publish /apps.json + the api/ mirror into the repository root.
publish:
	$(PYTHON) scripts/publish_root.py

# Assemble the exact static bundle deployed to GitHub Pages.
site:
	$(PYTHON) scripts/build_site.py

# Preview the assembled website locally at http://localhost:$(PORT).
serve: site
	$(PYTHON) -m http.server $(PORT) --bind 0.0.0.0 --directory _site

# Build the site, serve it locally and verify every page/feed/API URL works.
# `--root` additionally verifies the repository tree surface (/apps.json,
# feeds/, api/) that GitHub Pages serves without a build step.
smoke:
	$(PYTHON) scripts/smoke_test.py
	$(PYTHON) scripts/smoke_test.py --root --no-build

validate:
	$(PYTHON) scripts/validate.py
	bash scripts/validate_jq.sh

metadata:
	$(PYTHON) scripts/validation/validate_metadata.py feeds/apps.json
	$(PYTHON) scripts/validation/validate_source.py

registry:
	$(PYTHON) scripts/registry/build_registry.py

intelligence: registry
	$(PYTHON) scripts/intelligence/build_intelligence.py

backup:
	$(PYTHON) scripts/backup/create_backup.py create --label manual --destination .backups

test:
	$(PYTHON) -m unittest discover -s tests

format:
	$(PYTHON) -m ruff format src scripts tests

lint:
	$(PYTHON) -m ruff check src scripts tests

check: lint validate test
	$(PYTHON) scripts/publish_root.py --check
	$(PYTHON) scripts/merge_feeds.py --check
	$(PYTHON) scripts/check_reproducible.py --diff
	$(PYTHON) scripts/smoke_test.py
	$(PYTHON) scripts/smoke_test.py --root --no-build
	$(PYTHON) -m ruff format --check src scripts tests

# Structural audits (dead code, duplication, i18n, performance, workflows,
# accessibility, links/SEO) into reports/*.md — read-only over the tree.
audit:
	$(PYTHON) scripts/audit.py

# Autonomous pipeline stages (each is offline-safe except live monitoring).
discovery:
	$(PYTHON) scripts/discovery/discover_sources.py --dry-run
	$(PYTHON) scripts/validation/validate_source.py

monitoring:
	$(PYTHON) scripts/monitoring/report_status.py --offline
	$(PYTHON) scripts/selfheal.py
	$(PYTHON) scripts/mirror_check.py --offline

security:
	$(PYTHON) scripts/validation/validate_feed.py feeds/apps.json --allow-duplicates
	$(PYTHON) scripts/security/scan.py

analytics:
	$(PYTHON) scripts/analytics_rollup.py

# Derived artifacts: canonical DB, release ledger, enrichment, reputation,
# per-client feeds and the static API v3 surface.
derived:
	$(PYTHON) scripts/build_canonical.py
	$(PYTHON) scripts/build_release_history.py
	$(PYTHON) scripts/enrich_metadata.py
	$(PYTHON) scripts/reputation/score.py
	$(PYTHON) scripts/build_client_feeds.py
	$(PYTHON) scripts/build_api_v3.py
	$(PYTHON) scripts/registry/build_registry.py
	$(PYTHON) scripts/intelligence/build_intelligence.py

# Source-build lane (data/source_builds.json): recipes for iOS projects whose
# upstream publishes source but no artifact this project could attribute.
recipes:
	$(PYTHON) scripts/build_source.py check
	$(PYTHON) scripts/build_source.py list

recipes-find:
	$(PYTHON) scripts/discovery/find_source_builds.py --limit 10 --min-stars 100

# Modern website (Next.js): install, typecheck, lint, production build.
web:
	cd web && npm ci && npm run typecheck && npm run lint && npm run build

clean:
	rm -rf _site
