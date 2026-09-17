#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# jq-only lint + AltStore v2 structural validation for every JSON file.
#
# Runs offline and read-only, so it is safe on pull requests from forks. It is
# intentionally written in pure jq + POSIX shell (no Python, no dependencies)
# and is invoked by .github/workflows/validate.yml on every push and PR.
#
#   1. Syntax            - every tracked *.json must parse (`jq empty`).
#   2. Formatting        - no tabs, no trailing whitespace, one trailing newline.
#   3. AltStore v2 shape - feeds/*.json must carry the required fields with
#                          valid types (bundleIdentifier, version, ISO versionDate,
#                          localizedDescription, tintColor, size, versions[]).
# -----------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
err() { printf '::error file=%s::%s\n' "$1" "$2"; fail=1; }

# Assert a jq filter is truthy for a file; otherwise record a structural error.
assert_jq() { # <file> <jq-filter> <message>
  local file="$1" filter="$2" message="$3"
  if ! jq -e "$filter" "$file" >/dev/null 2>&1; then
    err "$file" "$message"
  fi
}

# Ignore tracked paths staged for deletion during repository cleanups.
mapfile -t all_json < <(git ls-files '*.json' | while IFS= read -r file; do
  [ -f "$file" ] && printf '%s\n' "$file"
done)

# ---------------------------------------------------------------------------
# 1 + 2. Syntax and formatting for every tracked JSON file.
# ---------------------------------------------------------------------------
for f in "${all_json[@]}"; do
  if ! jq empty "$f" >/dev/null 2>"/tmp/jq-lint.err"; then
    err "$f" "invalid JSON: $(cat /tmp/jq-lint.err)"
    continue
  fi
  if grep -nP '\t' "$f" >/dev/null; then
    err "$f" "contains tab characters; use spaces"
  fi
  if grep -nP ' +$' "$f" >/dev/null; then
    err "$f" "contains trailing whitespace"
  fi
  if [ -s "$f" ] && [ -n "$(tail -c1 "$f")" ]; then
    err "$f" "missing trailing newline"
  fi
done
rm -f /tmp/jq-lint.err

# ---------------------------------------------------------------------------
# 3. AltStore v2 structural checks for distributable feeds.
# ---------------------------------------------------------------------------
feeds=()
for f in feeds/*.json; do
  case "$(basename "$f")" in
  # pipeline state/health/timeline/badges and derived intelligence documents
  # (discovery, sources, verification, status, duplicates, analytics, trending,
  # related, reputation, download-intelligence, community, install,
  # search-index, compare, screenshots, integrity_report, dead_apps,
  # collections, translation-status, asset-manifest) are machine-readable datasets, not
  # AltStore v2 distribution feeds. Keep in sync with ALTSTORE_NON_FEED in
  # src/omnisource/constants.py (the standalone script keeps an explicit local
  # copy so it stays runnable without importing the package).
  state.json | health.json | updates.json | badge-*.json | discovery.json | sources.json | \
  verification.json | status.json | duplicates.json | analytics.json | trending.json | \
  related.json | reputation.json | download-intelligence.json | community.json | \
  install.json | search-index.json | compare.json | screenshots.json | \
  integrity_report.json | dead_apps.json | collections.json | \
  translation-status.json | asset-manifest.json | ipa-downloader.json) continue ;;
    *) feeds+=("$f") ;;
  esac
done

for f in "${feeds[@]}"; do
  # Top-level envelope.
  assert_jq "$f" 'has("name") and has("identifier") and (.apps|type=="array") and (.apps|length>0)' \
    "missing top-level name/identifier or empty apps array"

  # Every app carries the AltStore v2 required fields with valid types.
  assert_jq "$f" '.apps | all((has("name")) and (has("bundleIdentifier")) and (has("developerName")) and (has("version")) and (has("versionDate")) and (has("downloadURL")) and (has("localizedDescription")) and (has("iconURL")) and (has("tintColor")) and (.size|type=="number") and (.size>0) and (.versions|type=="array") and (.versions|length>0))' \
    "an app is missing required fields (bundleIdentifier, version, versionDate, localizedDescription, tintColor, size, versions) or has a non-positive size"

  # Field formats.
  assert_jq "$f" '.apps | all(.bundleIdentifier|test("^[A-Za-z0-9.-]+$"))' \
    "bundleIdentifier contains invalid characters"
  assert_jq "$f" '.apps | all(.versionDate|test("^[0-9]{4}-[0-9]{2}-[0-9]{2}([Tt ].*)?$"))' \
    "versionDate is not an ISO date (expected YYYY-MM-DD)"
  assert_jq "$f" '.apps | all(.tintColor|test("^[0-9A-Fa-f]{6}$"))' \
    "tintColor must be a 6-digit hex string"
  assert_jq "$f" '.apps | all((.size|type=="number") and (.size==(.size|floor)) and (.size>0))' \
    "size must be a positive integer byte count"
  assert_jq "$f" '.apps | all(.downloadURL|startswith("https://"))' \
    "downloadURL must be an https URL"

  # Flat fields must mirror versions[0] (deprecated v1 fields stay in sync).
  assert_jq "$f" '.apps | all(.version==.versions[0].version and .versionDate==.versions[0].date and .downloadURL==.versions[0].downloadURL and .size==.versions[0].size)' \
    "flat app fields (version/versionDate/downloadURL/size) must mirror versions[0]"

  # Every version entry is complete and well-formed.
  assert_jq "$f" '.apps | all(.versions | all((has("version")) and (has("date")) and (has("downloadURL")) and (has("localizedDescription")) and (.size|type=="number") and (.size>0)))' \
    "a versions[] entry is missing version/date/downloadURL/localizedDescription/size"

  # Every version entry carries a valid ISO date.
  assert_jq "$f" '.apps | all(.versions | all(.date|test("^[0-9]{4}-[0-9]{2}-[0-9]{2}([Tt ].*)?$")))' \
    "a versions[].date is not an ISO date"

  # fallbackDownloadURLs, when present, must be an array of https mirrors.
  assert_jq "$f" '.apps | all((.fallbackDownloadURLs == null) or (.fallbackDownloadURLs|type=="array"))' \
    "fallbackDownloadURLs must be an array or omitted"
  assert_jq "$f" '.apps | all((.fallbackDownloadURLs // []) | all(startswith("https://")))' \
    "fallbackDownloadURLs entries must be https URLs"
done

if [ "$fail" -ne 0 ]; then
  echo "jq validation FAILED"
  exit 1
fi
echo "jq validation OK: $(printf '%s' "${#all_json[@]}") JSON file(s), $(printf '%s' "${#feeds[@]}") feed(s)"
