# Security report

**Assessment date:** 2026-09-12
**Report artifacts:** `data/security.json` and `security-report.json`
**Gate:** critical findings block publication

## Scope

The security gate covers workflow execution, source/feed inputs, download
metadata, provenance, release assets, mirrors, and generated outputs. It does
not claim that an upstream IPA is safe; it makes provenance and integrity
observable and fails closed on evidence of corruption.

## Controls

- GitHub Actions default to least privilege and use job-level permissions.
- Workflow shell blocks use `set -euo pipefail`, quoted variables, HTTPS URL
  allowlists, input sanitization, bounded downloads, and explicit commit
  allow-lists.
- GitHub API credentials are attached only to the configured API origin.
- The extension hooks `integrity.stream_sha256()`, `security.verify_file()`,
  and `mirrors.tier_rank()` are retained as provider/test entry points for
  deployments that verify local binaries or rank declared mirrors.
  Anonymous health probes and binary downloads never receive a token.
- Discovery candidates are isolated in `data/quarantine/` until validation and
  verification complete. Quarantine is not an input to feed generation.
- Feed, source, release, and metadata validators reject malformed bundle IDs,
  versions, dates, URLs, screenshots, icons, sizes, and digests.
- SHA-256 and SHA-512 metadata is checked for format and coverage. The optional
  `--verify-downloads` security mode streams each HTTPS binary in bounded
  chunks, computes both hashes, checks size, and records mismatches as
  critical findings. The same pass runs on a schedule as
  `.github/workflows/verify.yml` (weekly), which also writes
  `lastFullVerification` into `feeds/state.json` so the integrity report can
  distinguish "digest format looks right" from "digest was checked against the
  downloaded bytes".
- Mirror failover uses only declared, recorded URLs and never invents a
  replacement download.
- Generated files are written atomically and checked for reproducibility.
- Events and logs redact common token, secret, password, authorization, and
  webhook fields before persistence.

## Current snapshot

The checked-in report records the current catalog totals, hash coverage,
duplicate binary findings, download probe state, provenance classification,
and per-app findings. Missing upstream hashes are low-severity findings; they
are tracked rather than silently presented as verified. Critical corruption,
malformed digest data, and failed opt-in binary verification block the gate.

Regenerate it with:

```bash
python3 scripts/security/scan.py
# expensive binary verification — also what the weekly verify.yml job runs:
python3 scripts/security/scan.py --verify-downloads
python3 scripts/omnisource.py --no-sync --no-health --verify-downloads  # records the verdict in state
```

## Threat model and residual risk

| Threat | Control | Residual risk |
|---|---|---|
| Malicious discovery URL | HTTPS, schema validation, quarantine | A valid HTTPS publisher can still be malicious |
| Workflow command injection | env indirection, strict shell, sanitized input | Trusted GitHub actions and upstream tools remain supply-chain dependencies |
| Token leakage to a CDN | host-scoped auth rules and anonymous probes | A future provider must preserve the rule |
| Replaced release binary | append-only history, hashes, optional stream verification | No hash cannot prove byte identity |
| Dead primary download | health probes and declared mirror failover | Real mirrors must be configured and independently trusted |
| Duplicate/rebranded app | canonical identity and bundle conflict checks | iOS bundle collisions are sometimes intentional tweaks |
| Corrupt generated output | atomic writes, pre-publication validation, reproducibility check | A compromised CI runner could still alter the workspace |

OmniSource is metadata infrastructure, not a code-signing authority. Users
must independently evaluate the developer, signing service, and sideloading
client they choose.
