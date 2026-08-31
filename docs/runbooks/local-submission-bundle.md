# Local submission bundle

> Status: **IN PROGRESS — awaiting the first commit-bound S7-05 RC and
> independent artifact audit.** This runbook and workflow are not themselves a
> successful RC, and no S7-05 artifact identity is claimed yet.

## Scope and fixed entry point

The bundle is a small, offline-readable index over nine already tested narrow
slices. It does not rerun those slices and does not embed their historical
artifact payloads. Run the builder from the repository root with its only
accepted command:

```text
python tools/local-stack/build_submission_bundle.py --offline
```

There is no alternate output directory, ledger argument, implicit online mode,
or accepted extra argument. Success exits `0` and writes one canonical JSON
result to stdout. Failure exits `2`, writes one fixed
`networkagent-local-submission-error/1.0` JSON object to stderr, and does not
echo input values, paths, ledger contents, or underlying exception text.

The command writes `.local/networkagent-submission` with exactly five regular,
single-link files:

| File | Contract |
| --- | --- |
| `submission-index.json` | Safe projection of the nine slice identities, tested RCs, runs, jobs, artifact citations, delivery state, and document provenance metadata. Evidence-document paths are omitted. |
| `limitations.json` | Per-slice `not_claimed` values and the explicit cross-slice non-closure state. |
| `REPRODUCE.md` | Offline reproduction and conflict-handling explanation bundled with the evidence. |
| `index.html` | Script-free, image-free, form-free local navigation page with four relative links and a restrictive CSP. |
| `manifest.json` | Acceptance and ownership marker, written last; records name, bytes, and SHA-256 for the other four files. |

The manifest binds the repository, source commit, committed-ledger SHA-256, and
marker `NETWORKAGENT_LOCAL_SUBMISSION_BUNDLE`. It cannot contain its own digest;
the command result and CI expose the SHA-256 of `manifest.json` as the inner
bundle digest. A byte-identical retry reports `changed=false`. Missing, extra,
linked, hard-linked, differently encoded, or byte-different members fail closed.

## Commit and worktree semantics

The evidence source is the Git object
`HEAD:docs/evidence/local-submission-evidence.v1.json`, not the worktree copy.
The repository root must be the exact Git top level, `HEAD` must be a full
40-character commit, and `GITHUB_SHA`, when present, must equal `HEAD`. The
builder snapshots HEAD, tracked status, and the committed ledger, then repeats
those checks before publication and before returning success.

Any staged or unstaged change to a tracked file is rejected as
`source_not_clean`. Ordinary untracked files are excluded from this source
gate and do not enter the evidence projection. That exclusion is not ownership
permission: an untracked file or directory occupying the output, lock, or
staging names is still rejected by the workspace contract.

`manifest.json` is the only completed-output ownership marker. An existing
output is accepted only when its marker, exact-five membership, member metadata,
manifest digests, and all expected bytes match the newly rendered bundle. An
unknown member, wrong marker, link, stale lock, staging collision, or foreign
bytes must be preserved and investigated; do not blindly delete or overwrite
the conflicting path. Move it aside only after establishing ownership.

## Nine-slice evidence interpretation

The index has one fixed order:

`S4-01`, `S4-02`, `S4-03`, `S4-04`, `S4-05`, `S7-01`, `S7-02`, `S7-03`,
and `S7-04`.

For each slice, `rc_sha` is the tested release-candidate commit. A documentation
commit is separate provenance: it may be a dedicated post-RC document, a shared
retrospective document, or absent by schema. A later documentation snapshot is
therefore not the tested RC and must never replace `rc_sha`.

Unknown or unavailable evidence is explicit rather than inferred. Fields with
an `UNKNOWN`, `ABSENT_BY_SCHEMA`, `NOT_EMITTED`, `PRESENT_NOT_RECORDED`, or
equivalent state retain `null` bytes/SHA/value where the schema requires it.
`null` means “not evidenced by this record”; it is not zero, success, or a
negative measurement.

Historical GitHub artifact entries are citations. Their recorded IDs, names,
bytes, archive digests, jobs, and 14-day retention describe the original audit;
payloads are not copied into this bundle and continuing remote availability is
not asserted (`remote_availability_asserted=false`). Expiry does not alter the
historical record, but an expired artifact cannot be presented as currently
downloadable or independently reverified.

## CI gate and publication

The `telco-submission` workflow runs on push, pull request, and manual dispatch
with only `contents: read`. Its shell steps use the Python standard library and
Git already present on the runner; they do not install packages, invoke network
clients, build containers, or access cloud services. The pinned checkout,
Python-setup, and artifact-upload actions remain GitHub service boundaries, so
“offline” describes the builder and verification logic, not those hosted
actions themselves.

Python 3.12 and 3.13 run in independent jobs. Each runs the contract tests,
builds from its own clean checkout, recomputes the inner manifest digest, and
exports only the validated lowercase digest. A separate Gate requires both
digests to be identical. Pull requests stop after that Gate and publish no
artifact.

For push and manual-dispatch runs only, a fresh Python 3.12 job checks out the
same commit and rebuilds from scratch. Before upload it independently checks:

1. the exact-five regular-file closure and byte budgets;
2. canonical manifest shape and the four payload name/bytes/SHA bindings;
3. repository, `GITHUB_SHA`, marker, and the independently computed committed
   ledger digest;
4. the fixed nine-slice order, limitations, non-closure data, and omission of
   evidence-document paths;
5. absence of URL schemes, active HTML content, links outside the fixed local
   navigation set, and any diagnostic or raw-data member; and
6. equality with the cross-Python inner manifest digest.

Only after those checks does it upload the exact directory as
`networkagent-local-submission-py3.12-attempt-${{ github.run_attempt }}` for 14
days. No test log, temporary build summary, workspace diagnostic, ledger source,
or other directory is included. The publish job is valid only if its final step
also succeeds; an upload produced by a subsequently failed job is not accepted
as S7-05 evidence.

The Actions summary labels two different hashes:

- the **inner manifest SHA-256** binds the project-defined five-file closure;
- the **GitHub outer artifact SHA-256** binds the GitHub-created uploaded
  container.

They cover different bytes and are not expected to match.

## Independent artifact audit

Accept a future S7-05 artifact only when the run is bound to the claimed RC and
all build, equality, clean-rebuild, verification, upload, and final-summary steps
are green. Record the run, jobs, artifact ID/name, creation and expiry, size, and
GitHub outer digest. Download the artifact without trusting the HTML index, then
verify the following independently:

1. The archive has exactly the five expected non-link regular entries and no
   enclosing diagnostics, temporary summaries, logs, raw data, or hidden files.
2. The downloaded outer bytes match the GitHub outer digest recorded by the
   successful job.
3. `manifest.json` is canonical UTF-8 JSON with LF termination, the fixed schema
   and ownership marker, the run head SHA, and the committed-ledger digest.
4. Its four records exactly match the other four entries by name, bytes, and
   SHA-256; the separately recomputed manifest digest matches the inner digest
   from the cross-Python Gate.
5. The index contains exactly the nine ordered slices and preserves the tested
   RC/document distinction and explicit unknown/null states.
6. The limitations and non-closure projection are present, and no prohibited
   path, URL, secret-like field/value, evidence-document path, historical
   artifact payload, or raw dataset is present.

If the artifact has expired, record that the historical citation is no longer
downloadable. Do not substitute a locally rebuilt archive for the missing
GitHub outer artifact or claim an outer digest for it.

## Stable failure contract

The public surface has exactly 16 fixed codes. The operator response is to
preserve the failing state, diagnose ownership/source separately, and rerun the
same command only after the cause is understood.

| Code | Meaning and response |
| --- | --- |
| `invalid_arguments` | Arguments are malformed or include an unsupported value; use only the fixed command. |
| `offline_required` | `--offline` was not explicitly supplied. |
| `source_unavailable` | Git root, HEAD, or committed ledger object cannot be read safely; restore a valid committed checkout. |
| `source_not_clean` | A tracked file is staged or modified; commit or intentionally restore it before building. |
| `source_mismatch` | `GITHUB_SHA` and HEAD differ; do not build evidence for a different revision. |
| `source_changed` | HEAD, tracked status, or committed ledger changed during construction; discard the attempt and use a stable checkout. |
| `ledger_read_failed` | The committed ledger cannot be read within its bounded file contract. |
| `ledger_contract_failed` | Ledger schema, canonical JSON, ordering, identity, run/job/artifact, unknown/null, or non-closure rules failed. |
| `privacy_contract_failed` | A prohibited key, URL/path, secret pattern, active HTML construct, or unsafe projection was found. |
| `workspace_unsafe` | Filesystem type, staging collision, link/reparse state, hardlink count, identity, or race check failed; preserve the path for inspection. |
| `workspace_not_owned` | An existing completed-output path lacks the exact accepted marker and closure; do not overwrite it. |
| `build_in_progress` | The owned lock is already held; wait for the active builder and investigate a stale lock rather than deleting blindly. |
| `bundle_write_failed` | Bounded create/write/link/fsync publication failed; the bundle is not accepted. |
| `bundle_contract_failed` | Rendered or read-back bytes do not satisfy exact-five, manifest, size, encoding, or expected-content rules. |
| `cleanup_failed` | Cleanup could not prove it removed only builder-owned temporary state; manual ownership review is required. |
| `command_failed` | An unexpected failure was reduced to the generic non-disclosing terminal code. |

## Privacy and non-closure

The bundle contains metadata and limitations, not source evidence payloads. It
omits evidence-document paths, absolute/local paths, URLs, secret-like fields or
values, raw datasets, databases, checkpoints, seals, wheels, and private IDs.
The exact-five allowlist prevents diagnostics from entering the upload. The HTML
page has no executable or remote content.

All nine included slice records remain narrow-slice `DONE` evidence only. The
ledger keeps P3e, P3e-5, P7, S4, Workflow E, and S7 `IN_PROGRESS`; Gate E, G2,
G4, and G5 remain open; P6 is `NOT_STARTED`; S2-04 remains `BLOCKED`. This
bundle does not close those stages or Gates, rerun historical acceptance,
guarantee artifact availability, prove signatures/attestation, provide complete
upstream datasets, or establish cloud/production readiness.

S7-05 itself remains **IN PROGRESS, awaiting a commit-bound RC, successful
remote workflow, and independent download audit**. Only that later evidence may
change this runbook's status.
