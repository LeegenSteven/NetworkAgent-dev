# S2-04 full Critical/High closure evaluation

Status: **BLOCKED** (2026-08-31)

S2-04 asks whether the local container can satisfy the repository's existing
G2/Gate B rule that the complete Trivy Critical/High result is `0/0`.  This
evaluation does not treat `--ignore-unfixed`, a vulnerability allowlist, or
removal of package-manager provenance as closure.

## Reproducible scan boundary

All candidate scans below used Trivy `0.74.0` with the same local database
snapshot and `linux/amd64` image selection:

- Trivy executable SHA-256:
  `4c532e1f28f53282dc364671e87381cd77760fa9cafab143f576449c2207cdd5`
- Trivy database SHA-256:
  `3c4a28b6331fb79c8e3faeb7742c3816b42a8aac682c372b27ef014905e084e3`
- database metadata SHA-256:
  `cf5131e29a27bbf775ba5bc9f013a1c1ce1a6e6853d8c1156fcc2278db183215`
- database `UpdatedAt`: `2026-08-30T13:05:01.49156526Z`
- database `DownloadedAt`: `2026-08-30T14:45:58.4683597Z`

The command shape was a remote image scan with `--scanners vuln`,
`--severity CRITICAL,HIGH`, and `--skip-db-update`. The raw reports were not
committed; their hashes are recorded so the decision cannot silently drift.

| Candidate | Python/runtime contract | Complete result | Raw report SHA-256 | Decision |
|---|---|---:|---|---|
| `python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579` | Current CPython 3.12/glibc baseline | `5 Critical + 29 High`; all 34 unfixed | `54f4d99469c8a63fc5a29dd4e6a43f027ab8419e2b08f99721c15fb7dad4722b` | Fails G2/Gate B |
| `python:3.12.14-slim-trixie@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217` | Preserves CPython 3.12 and current manylinux wheel ABI | `3 Critical + 16 High`; 3 fixable and 16 unfixed | `4e07048432be36fc9103e5966ee2beebaadeff94e332ce31b626472631e28087` | Lower count, but still fails the full `0/0` rule and initially regresses the existing fixable gate |
| `gcr.io/distroless/python3-debian13:nonroot` as pulled for this evaluation | Python 3.13, not the current CPython 3.12 lock | `0 Critical + 17 High`; all unfixed | `a1c6500070f4ae7b1a19cbfa08cb90b64db1ab1b7b9ffcc772ee44be47f16798` | Fails both the compatibility and vulnerability rules |
| `cgr.dev/chainguard/python:latest` as pulled for this evaluation | Public image was Python 3.14.7; the project currently requires Python `<3.14` | `0 Critical + 0 High` | `057acbeed84b9d4d858d80db596dfec552d5d6531a0236cec224b03ccb8692e2` | Not an admissible drop-in base; public `3.12` and `3.13` tags were unavailable |

Mutable tags in the last two rows are diagnostic candidate probes, not release
identities.  They are included to show why a superficially clean scan does not
by itself satisfy the runtime contract.

## Rejected shortcuts

- Copying `/usr/local` from a Python image into `scratch` or Distroless without
  a package identity would make interpreter CVEs less visible to Trivy rather
  than remove them.
- Copying untracked system libraries would weaken SBOM and package provenance.
- Alpine/musl is not compatible with the frozen manylinux/cp312 native wheel
  set without a separate dependency and ABI migration.
- The public zero-finding Chainguard image changes the interpreter to Python
  3.14, outside every first-party package's current `<3.14` contract.

## Closure and reopening conditions

S2-04 is therefore `BLOCKED`, not `DONE`.  S2, Workflow B, Gate B, and G2
remain open.  This does not invalidate the S2-03 runner-local evidence; it
corrects its limited policy from “zero fixable Critical/High” to the stricter
global requirement that is still unmet.

The work package may be reopened only when one of these is approved and proven
on the same commit and database snapshot:

1. a provenance-preserving CPython 3.12/glibc image whose complete scan is
   genuinely `0/0`; or
2. a separately reviewed Python/base migration with new hash locks, native
   wheel and ABI validation, dual-version regressions, complete SBOM identity,
   and the full container governance/restart/reset suite.

No production claim, registry publication, signing, or Gate B/G2 completion is
authorized by this evaluation.
