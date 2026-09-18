# FIX-PROMPT — OmniSource remediation (paste-ready)

> Paste everything below into a fresh session working on `iamsmmh/OmniSource`.
> Evidence for every claim lives in `AUDIT-2026-09-18.md` (same directory) —
> read it before touching code; it carries the commands, the counts and the
> exact annotations. Finding numbers F1–F8 were assigned in that audit and are
> not inherited from any earlier numbering.

---

## Hard constraints

1. **Generated paths.** Per `CONTRIBUTING.md` ("Change rules"): treat `feeds/`,
   `api/`, `data/` and website build outputs as **generated**. Hand-edit only
   the generator (`src/omnisource/**`, `scripts/**`), then regenerate. Never
   hand-edit a generated file to make a test pass. `catalog.json` is the one
   generated-looking file that is hand-edited on purpose.
2. **Stdlib-only Python.** `requirements.txt` says so explicitly ("OmniSource
   runtime uses only the Python standard library"); `pyproject.toml` declares
   `dependencies = []`. Do not add a runtime dependency. Local *tooling*
   (ruff, jsdom) may be installed outside the repo or in `web/node_modules`.
3. **Do not weaken tests.** No deleting, skipping, `xfail`-ing, loosening an
   assertion, or narrowing a glob to make a gate pass. New behaviour gets a new
   test. The baseline is **582 passing tests, 0 skipped** — the count may only
   go up.
4. **Do not touch the update-consent protocol.** Carry this as a standing
   instruction: whatever currently asks the user before applying updates stays
   exactly as it is — same trigger, same wording, same default. (For the
   record: `grep -rn -i "consent"` finds nothing in this tree, so the protocol
   is not a file in the repo. Do not go looking for one and do not invent one;
   if a change you are about to make alters any user-facing update prompt,
   stop and ask instead.)
5. **Legacy surface is load-bearing.** `CONTRIBUTING.md`: "Preserve legacy root
   feeds and API v2 behavior whenever possible." `feeds/api/v2/manifest.json`
   pins the sha256 of every v2 document and `tests/test_api_v2.py` checks the
   pins — a partially regenerated corpus fails. Regenerate fully or not at all.
6. **HTTPS only, validated before request** (`CONTRIBUTING.md`), and new
   integrations go in as plugins/providers — no importing arbitrary Python
   from discovery.
7. **Branch discipline.** Work on the session branch only; never force-push or
   retarget `main`.

---

## Environment facts (verified 2026-09-18 — do not rediscover these)

* **GitHub API works**: `gh auth status` → `arena-ai-coding-agent[bot] (GH_TOKEN)`.
  Run lists, job steps and **annotations** are all reachable.
* **Actions job logs do NOT**: `gh run view <id> --log-failed` → `EOF` from
  `results-receiver.actions.githubusercontent.com` (Azure blob redirect).
  Use `gh api repos/iamsmmh/OmniSource/check-runs/<job_id>/annotations` — it
  carries the failing step's error message and is how F1 was diagnosed.
* **No browser, no rendering engine, no network to `iamsmmh.github.io`**
  (`curl` → `SSL_ERROR_SYSCALL`, HTTP `000`; same for `raw.githubusercontent.com`).
  **Nothing you conclude about layout, contrast, scroll position or pixel
  geometry can be verified here.** Say so rather than asserting it.
* **PyPI and npm are reachable.** `ruff` is not preinstalled (`make lint` fails
  on a bare checkout): `python3 -m venv /home/user/.ruff-venv &&
  /home/user/.ruff-venv/bin/pip install ruff`. Keep the venv **outside** the
  repo — `.gitignore` covers `.venv/` only, so `.venv-ruff/` would show up as
  untracked.
* **jsdom is not preinstalled**, so `tests/test_js_runtime.py` skips. Node is
  present (`v22.22.3`). `cd web && npm install jsdom --no-save` turns the skip
  into a real test — do this before claiming the JS layer is covered.
* `scripts/smoke_test.py` **rewrites** `feeds/ipa-downloader.json` and
  `ipa-downloader/index.html` (a `generatedAt` bump). Revert those two files
  after any smoke run unless the task is about them.

---

## What is already done (do not redo)

Completed on `arena/01a0b463-omnisource` on 2026-09-18, all verified against the
gate at the bottom of this file:

| Task | Change | Test that pins it |
| --- | --- | --- |
| **F1** | `api/v3` source documents keyed by `slug` via a new `safe_doc_id()`; `build_api_v3.py` prune made recursive | 8 tests in `tests/test_api_v3.py`, two of which fail on the pre-fix tree (12 illegal paths, 119 stray nested entries) |
| **Scroll yank** (reader-reported) | `js/site.js` ends the deep-link re-pin on *any* scroll it did not cause, and a new hash re-arms it | `tests/js/scroll_pin.cjs` — fails on the pre-fix tree with `"the page re-pinned after the reader scrolled away: [\"catalog\"]"` |
| **stableScroll gap** | `js/core.js` falls back to `<main>` when a render replaces both the landmark and its section, instead of silently making no correction | `tests/js/scroll_pin.cjs` (2 checks) |
| **F3** | `.github/workflows/validate.yml` installs jsdom before the suite, so `tests/test_js_runtime.py` runs instead of skipping; the matrix job documents why it does not | both harnesses run under `python3 -m unittest` |
| **F6** | `domain.today()` also honours `SOURCE_DATE_EPOCH`; `OMNISOURCE_TODAY` still wins | 3 tests in `tests/test_domain.py` |
| **F2 (half)** | `sync.yml` tees the pipeline to a log, and on failure puts its tail in the annotations and uploads it as an artifact | YAML parsed and structurally checked; **actionlint could not run here** |
| **Stale reports** | `WEBSITE-BUG-REPORT.md` gained a 2026-09-18 status table; `ISSUES-REPORT.md`'s false "`domain.today()` has no environment hook" claim is corrected in place | — |

Also verified **already fixed in this tree**, so nobody should "fix" them again:
`WEBSITE-BUG-REPORT.md` items **#1, #2, #3, #8, #9, #10**. Item #8 in
particular: `js/core.js:357` sets `SEARCH_MIN_CHARS = 1` with a dedicated
`SEARCH_SHORT_THRESHOLD = 0.12`, and the comment there cites the report.

What is **not** done: F1 still needs its PR opened and the next scheduled
`Backup and Recovery` run confirmed green; F2's root cause is still unknown (the
observability added above is what makes it knowable); F4's remaining items,
F5, F7 and F8 are untouched.

---

## Tasks, in priority order

### F1 — **DONE, awaiting PR**. `api/v3` source documents named after raw ids

Already implemented on this branch; do not redo it. For the record:
`src/omnisource/api_v3.py` gained `SAFE_DOC_ID_CHARS` + `safe_doc_id()` and now
keys source documents by `slug` (sanitised `id` as fallback, `-2`/`-3` on
collision); `scripts/build_api_v3.py`'s prune is `rglob`-based and removes the
directories it empties. This was the cause of six consecutive
`Backup and Recovery` failures (2026-09-13 → 2026-09-18): `upload-artifact`
refused `api/v3/sources/https:/…json.json` — *"Contains the following
character: Colon :"*.

**What is left of F1 is delivery, not code:**
1. Commit the branch (`src/omnisource/api_v3.py`, `scripts/build_api_v3.py`,
   `tests/test_api_v3.py`, and the `api/v3` regeneration: 114 deletions, 114
   additions) and open the PR.
2. After merge, confirm the next scheduled `Backup and Recovery` run goes green
   — `gh api "repos/iamsmmh/OmniSource/actions/workflows/backup.yml/runs?per_page=3"`.
   Until that run is green, **F1 is not verified end to end**, and you should
   say so.
3. Optional hardening while you are in there: `backup.yml`'s upload step is the
   only place a path-portability regression surfaces, and it surfaces once a day.
   A cheap earlier tripwire is a test that walks `api/` for NTFS-illegal
   characters — `CommittedApiSurfaceTests.test_no_committed_api_path_is_illegal_on_ntfs`
   already does this and runs in `validate.yml` on every PR.

### F2 — HIGH. `Sync & Publish` fails several times a day, then passes  *(observability landed; root cause still unknown)*

**Evidence:** seven scheduled failures on 2026-09-16/17
(`35272983595`, `35243651115`, `35212062790`, `35175573461`, `35148224045`,
`35118209405`, `35085751317`); the 2026-09-18T10:19:47Z run succeeded. The
annotation is only `Process completed with exit code 1.` — **the root cause is
unverified**; do not propose a fix before you have read a real failure.

* **Targets:** `.github/workflows/sync.yml` and `src/omnisource/pipeline.py`
  (`stage_sync`, `run`).
* **Step 1 (investigation, not a fix):** pull annotations for the failed jobs —
  `gh api repos/iamsmmh/OmniSource/actions/runs/<id>/jobs --jq '.jobs[]|select(.conclusion=="failure")|.id'`,
  then `gh api repos/iamsmmh/OmniSource/check-runs/<job>/annotations`. If they
  are as uninformative as `35272983595`'s, the real task is **observability**:
  add a step to `sync.yml` that uploads the pipeline log as an artifact on
  failure (`if: failure()`, `actions/upload-artifact`), so the next failure is
  diagnosable without Azure blob access.
* **Test needed:** a unit test in `tests/test_pipeline.py` (or `tests/test_ops.py`)
  pinning whatever retry/backoff or error-propagation behaviour you add. If you
  add the log-upload step, add the workflow-lint assertion the repo already has
  for workflow shape (`tests/test_workflows*.py` — check what exists first).
* **Verification:** `PYTHONPATH=src python3 -m unittest tests.test_pipeline -v`,
  plus a manual `gh workflow run` if the workflow supports dispatch.

### F3 — **DONE**. The only JS behaviour test never ran in CI

**Evidence:** `.github/workflows/validate.yml` runs
`PYTHONPATH=src python3 -m unittest discover -s tests` (lines 52 and 108) and
contains no `npm` invocation, so `tests/test_js_runtime.py` hits
`self.skipTest("jsdom is not installed (run 'cd web && npm ci')")` on every CI
run. The drawer behaviours it pins (scroll lock that un-sticks the header,
drawer that will not scroll, page scrolling behind the backdrop, focus left on
the abandoned button) are asserted nowhere automatically.

* **Targets:** `.github/workflows/validate.yml` (the "Run unit tests" step at
  line ~50), `tests/test_js_runtime.py:26` (`JSDOM = ROOT / "web" / "node_modules" / "jsdom"`).
* **Change:** install jsdom before the suite in `validate.yml` — either
  `cd web && npm ci` (full toolchain, slower) or a targeted
  `npm install --no-save --prefix web jsdom`. Keep it offline-safe and do not
  make the job fail if the registry is unreachable: a skipped test is better
  than a red gate, but then say so in the step name.
* **Test needed:** the existing test is the test. Assert it *ran*: locally,
  `python3 -m unittest discover -s tests 2>&1 | grep -c skipped` must be `0`
  when jsdom is present. Consider adding an explicit marker in CI (e.g. a step
  that fails if the suite reports `skipped=1` for `test_js_runtime`).
* **Verification:** `cd web && npm install jsdom --no-save &&
  PYTHONPATH=src python3 -m unittest tests.test_js_runtime -v` → `ok`, not
  `skipped`; then the full suite → `Ran 582 tests … OK` with 0 skipped.

### F4 — MEDIUM. Website report items that are still open *(#1, #2, #3, #8, #9, #10 verified fixed; #4, #5, #11, #12 not re-checkable here)*

`WEBSITE-BUG-REPORT.md` is stale. Already fixed in this tree, **verified at code
level**: #1 drawer (`components.css:3691` `html.nav-open` lock), #2 favourites
hoisting (`js/site.js:39-40` above `var state`), #3 deep-link re-pinning
(`js/site.js` ≈ 2816 `repinTarget`/`repinHash()`/`armHashRepin()`), #9 sitemap
(`sitemap.xml:76`), #10 duplicate titles (114 pages, 114 distinct titles).
Update the report to say so, or a future session will "fix" them again.

Still open: **#4** hero clipping ≤ 340 px, **#5** `/sources/` card overflow at
320 px, **#6** home weight (~2.4 MB JSON, ~1.5 s main thread), **#7** hot-linked
third-party media, **#8** search palette ignoring 1-character queries, **#11**
small-text contrast, **#12** target size / `<img>` sizing.

* **#8 is the one to take** — it is pure logic, offline-checkable:
  `js/modules/search.js` + the palette renderer in `js/site.js`. No
  `length < 2`-style guard was found by grep, but that is not a reproduction;
  reproduce first (jsdom + `tests/js/`), then fix.
* **#6 is measurable offline** too: count bytes and cards the home page parses
  (`index.html` + the feeds it fetches) and pin a budget in a test.
* **#4, #5, #11, #12 need a layout engine.** In this sandbox that means they
  cannot be verified. Either state that plainly or do the work where a browser
  exists. Do **not** claim a pixel or contrast fix from reading CSS.
* **Test needed:** extend `tests/js/*.cjs` (the jsdom harness pattern in
  `tests/js/drawer_smoke.cjs`) and drive it from `tests/test_js_runtime.py`.
  `tests/test_website_shell.py` is the place for markup/CSS assertions.

### F5 — MEDIUM. Reputation cadence (ISSUES-REPORT item 4, partly fixed)

70 sources still publish `updateFrequencyDays: null` because
`state[slug]["versions"]` is capped at `upstream.keepVersions` (1 for most
apps), so no interval exists to average. The report calls it a retention
decision, not a formula bug — do not "fix" the formula.

* **Targets:** `src/omnisource/utils/dates.py` (`version_dates(..., include_history)`),
  `src/omnisource/reputation*` / `scripts/reputation/score.py`, and the
  retention knob `upstream.keepVersions` in `src/omnisource/config.py` +
  `catalog.json`.
* **Test needed:** `tests/test_intelligence.py` already pins both directions of
  the dead-app classifier; add a cadence test there (or in a reputation test
  module) that builds a synthetic multi-release history and asserts a real
  `updateFrequencyDays` rather than `null`.
* **Verification:** `PYTHONPATH=src python3 -m unittest tests.test_intelligence -v`,
  then `make derived` + `check_reproducible.py --diff` (scores are published
  into source pages, so regeneration is mandatory).

### F6 — LOW. `docs/` drift (ISSUES-REPORT item 10, half done)  *(`SOURCE_DATE_EPOCH` half is DONE)*

The `verify.yml` half is closed; the `docs/` reorganisation half is not, and
there is no generated-docs pipeline in this checkout (`scripts/update_docs.py`
does not exist). Also stale in prose, verified this session:
`ISSUES-REPORT.md` claims *"`domain.today()` has no environment hook, so a build
cannot be pinned to a date"* — false: `src/omnisource/domain.py:67` reads the
`OMNISOURCE_TODAY` pin. What is genuinely missing is the conventional
`SOURCE_DATE_EPOCH` spelling (`grep -rn "SOURCE_DATE_EPOCH" src scripts` →
empty); adding it as an alias is a small, well-scoped change with a clear test
(`tests/test_domain.py`: set `SOURCE_DATE_EPOCH`, assert `today()` honours it).

### F7 — DOCUMENTED, deliberately open. Shared bundle IDs

15 apps share 4 bundle IDs in the single installable feed. `validation.py`
fails the build on an undeclared shared bundle ID, so it cannot drift silently;
de-duplicating would change published `bundleIdentifier` values for installed
clients. **Do not change these.** Leave the note in `ISSUES-REPORT.md` item 7.

### F8 — LOW. Reproducibility hygiene

`check_reproducible.py` passes today (915 files stable, both modes) because it
pins `OMNISOURCE_TODAY`. Two follow-ups, both small: the `SOURCE_DATE_EPOCH`
alias from F6, and making the `validate.py` screenshot-host advisories (15
warnings: `repo.ikghd.me`, `apps.sidestore.io`, `aidoku.app`) either resolved
or explicitly allow-listed so `0 errors, 15 warnings` stops being the
unexamined steady state.

---

## Definition of done

All of these must be run and their output reported — not assumed. `make check`
is the composite; the individual commands are listed because `make lint` needs
ruff installed and `make smoke` dirties two files.

```bash
cd /home/user/OmniSource
export PYTHONPATH=src

# 1. lint + format  (ruff must be installed first — see Environment facts)
/home/user/.ruff-venv/bin/ruff check src scripts tests            # → All checks passed!
/home/user/.ruff-venv/bin/ruff format --check src scripts tests   # → N files already formatted

# 2. unit suite — 582 passing, 0 skipped is the floor
python3 -m unittest discover -s tests                             # → Ran 582 tests … OK

# 3. the JS runtime test must RUN, not skip (needs jsdom in web/node_modules)
python3 -m unittest tests.test_js_runtime -v                      # → ok (not 'skipped')

# 4. catalog + feed validation
python3 scripts/validate.py                                       # → OK: 0 errors, 15 warning(s)
bash scripts/validate_jq.sh                                       # → exit 0

# 5. published-surface consistency
python3 scripts/publish_root.py --check                           # → mirrors feeds/ byte-for-byte
python3 scripts/merge_feeds.py --check                            # → unified from 130 modular feed(s)

# 6. reproducibility (mandatory after ANY generator change)
python3 scripts/check_reproducible.py --diff                      # → 915 generated file(s) stable

# 7. site smoke (both modes)
python3 scripts/smoke_test.py                                     # → all checks passed ✔
python3 scripts/smoke_test.py --root --no-build                   # → all checks passed ✔

# 8. undo smoke's incidental churn
git checkout -- feeds/ipa-downloader.json ipa-downloader/index.html

# 9. the diff is scoped: only intended files, no venv, no node_modules
git status --porcelain
```

Plus: `git diff --stat` shows no test deletions or weakened assertions; no new
runtime dependency in `pyproject.toml`/`requirements.txt`; generated files were
regenerated rather than hand-edited.

---

## Reporting rule

State what you did **not** verify, in the same breath as what you did. In this
sandbox specifically:

* no rendered-page, layout, contrast, scroll-position or pixel measurement is
  possible — no browser, no network to `iamsmmh.github.io`;
* Actions job logs are unreachable, so a workflow root cause that is not in the
  annotations is unverified;
* a scheduled workflow's next run cannot be observed before merge.

A clean exit code is not a pass when the output is wrong, and a described
change is not a delivered one. Name the function or code path your check
actually executed; if you cannot name one, you have not verified anything.
