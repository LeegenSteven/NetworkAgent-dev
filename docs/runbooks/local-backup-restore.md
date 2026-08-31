# Local DuckDB cold backup and recovery runbook

> Work package: S4-04 stopped-writer Local DuckDB cold backup recovery
> Status: `DONE (S4-04 narrow slice)`
> Scope: one local, single-process DuckDB writer; offline backup and
> reset/fresh-initialization recovery only

## 1. Purpose and boundary

This runbook explains how to create, verify, and restore the Local Profile's
bounded DuckDB cold backup, and how to reproduce the fixed S4-04 recovery drill.
The portable backup is schema `networkagent-local-cold-backup/1.0`; the fixed
drill emits `networkagent-local-backup-recovery/1.0` evidence.

The independently verified release candidate is
`54551feb43be60c3b9bdd5eab076cdb7c0aba61a`. It proves one stopped-writer,
single-process Local recovery path. It does not turn the Local Profile into a
production backup service and does not close S4, Workflow E, Gate E, or G5.

The public scope is frozen as:

```text
backup_mode=COLD_OFFLINE
database_engine=DUCKDB
execution_mode=LOCAL_SINGLE_PROCESS
restore_target=RESET_FRESH_INITIALIZATION
writer_stopped=true
```

## 2. Prerequisites and stopped-writer rule

Run from the repository root with the project dependencies installed under
Python 3.12 or 3.13. The workspace must already be marker-owned and initialized.
No Docker daemon, GCP credential, model API, or external network is required.

Before either `backup` or `restore`:

1. Stop `local_stack.py serve` and every other process that can open the
   workspace database.
2. Confirm there is exactly one operator and no second maintenance invocation.
3. Keep the writer stopped until the command exits and its JSON result is
   verified.
4. Use a local fixed drive/filesystem. Do not use a symlink, junction, reparse,
   UNC/device path, filesystem root, home, repository root, or a path equal to,
   inside, or above the workspace.

The `.backup-restore.lock` is a non-blocking cooperative maintenance lock. It
detects another conforming invocation, but it is not a distributed lock and
cannot make an online writer safe. `writer_stopped=true` is therefore an
operator prerequisite, not an inference from lock acquisition.

## 3. Fixed evidence drill: the only command

Run:

```text
python tools/local-stack/run_backup_restore_demo.py --approve-local-simulation
```

This wrapper accepts no workspace, path, URL, threshold, Cloud, or arbitrary
command argument. The explicit Local simulation confirmation is its only input.
It creates a new marker-owned run directory beneath the fixed defense-evidence
root and performs this sequence:

```text
doctor
  -> successful native governance lifecycle
  -> read-only lifecycle projection
  -> cold backup
  -> reset
  -> fresh init
  -> corrupt-copy restore rejection, fresh database unchanged
  -> valid restore (changed=true)
  -> exact restore retry (changed=false)
  -> lifecycle equivalence checks
  -> workspace + valid backup + corrupt backup cleanup
  -> atomic local-backup-recovery-report.json
```

Success stdout is one bounded JSON document. Failure stdout is empty and stderr
is one stable JSON error document. Do not redirect stderr into the success
summary.

## 4. Operator backup and restore commands

Choose a new destination whose parent already exists. The destination itself
must not exist:

```text
python tools/local-stack/local_stack.py --workspace W backup --destination NEW_DIR
```

On success, save the lowercase 64-character value at
`result.manifest.sha256`. The output also contains database/catalog summaries and
a `local_ownership_sha256`; that ownership value is process-local cleanup
metadata, not a portable backup identifier and not a substitute for the
manifest hash.

With every writer still stopped, review the exact two-file source and restore
using the saved manifest hash:

```text
python tools/local-stack/local_stack.py --workspace W restore --source DIR --expected-manifest-sha256 64-lowercase-hex --yes
```

The explicit `--yes` is mandatory. A successful first replacement reports
`result.changed=true`. Repeating the exact restore after the target already has
the same database reports `result.changed=false`; this is the required
idempotency result. A changed source or expected hash is not an exact retry.

## 5. Exact two-file closure and capacity

A valid cold-backup directory contains exactly two non-link, single-link regular
files and no other member:

```text
networkagent.duckdb
backup-manifest.json
```

The database limit is 128 MiB and the manifest limit is 16 KiB. Catalog limits
are four schemas, 64 tables, 16 views, 100,000 total rows, and 1,024 records per
fingerprinted catalog query. The destination is first built in a unique
mode-0700 staging directory, then published with an atomic no-replace rename.
An existing destination is never overwritten.

The database copy is produced only after `CHECKPOINT`, using DuckDB
`COPY FROM DATABASE`, followed by a checkpoint of the copied catalog. External
access is disabled after the required local attach; extension autoload/install
and temp spilling are disabled. A `.wal`, `.tmp`, link-like object, hardlink,
extra file, missing file, or changed member fails closed.

## 6. Manifest, physical hash, and logical verification

`backup-manifest.json` is strict canonical UTF-8 JSON with a trailing newline.
Duplicate keys, unknown/missing keys, non-canonical serialization, invalid UTC
time, invalid UUID, or an unsupported schema are rejected. It binds:

- `backup_id` and `created_at`;
- the source workspace-marker SHA-256;
- Local and optional Assurance schema versions;
- DuckDB library and storage-compatibility versions;
- the checkpointed database filename, byte count, and SHA-256;
- schema/table/view counts, sorted table identities, per-table row counts, and
  total row count;
- a logical SHA-256 covering catalog metadata and all ordered table rows.

Backup completion reopens the copied database read-only and compares its schema,
DuckDB metadata, catalog, tables, rows, and logical fingerprint with the source.
Restore verifies the exact directory membership, canonical manifest, manifest
hash, database bytes/hash, sidecar absence, schema/DuckDB metadata, catalog, row
counts, and logical fingerprint before replacement. It repeats source-directory
and manifest checks immediately before use. The caller-supplied manifest hash is
therefore an explicit operator binding in addition to the manifest's database
hash.

## 7. Corrupt-copy rejection and zero-change proof

The fixed drill makes a bounded regular-file copy of the valid two-file backup,
changes an actual database byte, and deliberately leaves the copied manifest's
database declaration unchanged. Restore must reject this source with the stable
core error `backup_invalid`.

The wrapper hashes the freshly initialized target database before and after the
rejected restore. The evidence is trustworthy only when
`fresh_database_unchanged_after_rejection=true`. A rejected command alone is not
enough: any target change, temporary-file residue, unexpected exit code, stdout,
or error shape changes the result to an untrustworthy drill failure.

This is a controlled integrity mismatch, not disk corruption, power-loss,
malware, ransomware, or disaster-recovery evidence.

## 8. Restore idempotency and lifecycle equivalence

Restore copies the verified source into a fixed, exclusively created file in the
workspace state directory, reopens it read-only, repeats physical and logical
verification, then atomically replaces `networkagent.duckdb`. The state
directory, current database, source directory, and temporary file identities are
rechecked immediately before replacement. The directory is synchronized where
the platform supports it.

The fixed report's ten proof values must all be exact:

```text
backup_changed=true
backup_file_count=2
catalog_equivalent=true
corrupt_backup_rejected=true
fresh_database_unchanged_after_rejection=true
lifecycle_projection_equivalent=true
restore_changed=true
restore_retry_changed=false
restore_retry_equivalent=true
row_count_equivalent=true
```

The wrapper compares the complete read-only Canonical lifecycle projection from
before reset with the projection after the first restore and after the retry.
It also requires the two restore summaries to be identical except for
`changed`. Catalog or row-count equality by itself is not lifecycle-equivalence
evidence.

## 9. Path identity, races, and cleanup

Directory ownership is captured as device/inode. Regular-file ownership is
captured as device/inode/size/modified-time/change-time/link-count. The internal
ownership digest uses the fixed
`networkagent-local-backup-ownership/2` domain and the complete captured tree.
Raw paths and raw filesystem identities are never placed in the persisted drill
report or release evidence.

All known children are validated before cleanup deletes any one of them. A
same-name replacement, same inode with changed ctime, link/hardlink, extra child,
directory swap, or any identity drift fails closed. Objects whose identity is
unknown or raced are preserved for manual review. This is the explicit
`IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP` non-claim.

On a successful fixed drill, the workspace, valid backup, and corrupt backup are
removed; only the report remains in the run directory. A manual `backup` command
intentionally leaves its published two-file directory for the operator. There is
no broad backup-delete command. If a failure leaves residue, do not recursively
delete `.local`, the defense root, a run directory, or a guessed path. First
preserve the stable error, inspect the exact non-link path and identities, and
remove only a confirmed operator-owned object using normal administrative
procedures.

## 10. Stable error classification

Core commands return one safe JSON error on stderr. The most relevant codes are:

| Code | Meaning and response |
|---|---|
| `workspace_not_initialized` | Initialize the marker-owned workspace before backup; do not create a database by hand. |
| `restore_confirmation_required` | Re-run only after review with the explicit `--yes`. |
| `workspace_busy` | A writer/maintenance lock or unsafe WAL state exists; stop writers and investigate. |
| `backup_exists` | Select a different new destination; never overwrite the existing directory. |
| `backup_too_large` | Database, manifest, catalog, rows, or free-space budget is outside the frozen Local limit. |
| `manifest_mismatch` | The supplied manifest SHA does not bind this source; do not substitute the source value without independent review. |
| `backup_invalid` | Membership, link/sidecar, JSON, hash, schema, catalog, logical, source identity, or race validation failed; preserve the source. |
| `backup_failed` | Backup failed before trustworthy publication or verification; inspect only identity-confirmed residue. |
| `restore_failed` | Restore could not copy, verify, publish, or safely clean its owned temporary file; do not claim recovery. |
| `unsafe_workspace` | A workspace/state/path/identity safety boundary failed; do not bypass it. |

The fixed wrapper additionally uses `confirmation_required`, `command_failed`,
`recovery_contract_failed`, `cleanup_failed`, and `report_write_failed`. A
cleanup failure overrides an otherwise successful drill because the public
success contract requires all three cleanup results. Stable errors do not expose
rejected paths or raw values.

## 11. Report, source binding, and artifact reconstruction

The persisted `local-backup-recovery-report.json` contains the evidence body but
not a report pointer. Public success stdout adds only:

```text
report.filename=local-backup-recovery-report.json
report.bytes=<strict positive integer>
report.sha256=<64 lowercase hexadecimal characters>
```

Do not trust a caller-supplied report path. Inventory the fixed defense root
before and after the command, select only the newly created non-link regular
report under the newly created non-link run directory, then compare raw UTF-8
bytes and SHA-256 with the stdout envelope.

To reconstruct the persisted body from a release summary, parse strict JSON,
remove the top-level `report` member, serialize with sorted keys, UTF-8,
`ensure_ascii=false`, separators `,` and `:`, and append one LF. For the verified
RC this produces exactly 1,804 bytes and SHA-256
`f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`.

`source.commit_bound=true` requires Git to be available, the same valid commit
before and after execution, and a clean tracked tree throughout. That case uses
classification `LOCAL_COLD_BACKUP_RECOVERY_EVIDENCE`. A stable dirty worktree is
honestly classified `LOCAL_WORKTREE_COLD_BACKUP_RECOVERY_EVIDENCE` and cannot be
promoted to a verified RC. In CI, source commit must equal `GITHUB_SHA`.

## 12. Privacy and release-content boundary

The fixed privacy record must be exactly `status=PASS` with every following
flag `false`:

```text
absolute_paths_recorded
backup_identifiers_recorded
child_stderr_recorded
child_stdout_recorded
database_bytes_recorded
database_digests_recorded
database_filenames_recorded
domain_identifiers_recorded
environment_recorded
manifest_content_recorded
manifest_digests_recorded
raw_arguments_recorded
workspace_identifiers_recorded
```

The uploaded release artifact contains evidence summaries, wheels, inventories,
SBOM/scan records, and the release manifest; it is not a cold-backup archive.
The independent audit found no member whose name ends in `.duckdb`, no member
named `backup-manifest.json`, and no member with a `.local` path segment. This is
an archive-member/path assertion; it must not be broadened into a claim that the
raw ZIP byte string contains no occurrence of `.local`. The audited evidence
JSON also contained no cold-backup raw path and no raw ownership, device, or
inode field.

## 13. Delivered coverage and explicit non-claims

The seven delivered flags are exactly:

```text
checkpointed_full_database_backup=true
corrupt_backup_rejection=true
lifecycle_equivalence=true
manifest_hash_binding=true
reset_fresh_init_restore=true
restore_idempotency=true
successful_run_workspace_backup_cleanup=true
```

The 12 `not_claimed` values are exactly:

```text
ONLINE_BACKUP
PRODUCTION_HA
MULTI_REPLICA_FAILOVER
RPO_OR_RTO_SLO
POWER_LOSS_DURABILITY
ENCRYPTED_OR_SIGNED_BACKUP
OFF_HOST_OR_REMOTE_BACKUP
CROSS_VERSION_MIGRATION
CLOUD_OR_SPANNER_BACKUP
GATE_E_OR_G5_CLOSURE
CLOUD_OR_PRODUCTION_RECOVERY
IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP
```

Consequently this result must not be described as continuous backup, PITR,
replication, availability, disaster recovery, ransomware protection, retention,
key management, migration compatibility, Spanner recovery, or production
readiness.

## 14. Remote RC, independent artifact audit, and status transition

The tested RC is `54551feb43be60c3b9bdd5eab076cdb7c0aba61a`. [Local run
33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792)
completed successfully in 13m04s:

- Python 3.12 [job
  99372557281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557281)
  completed in 13m00s; the fixed drill step took 37s.
- Python 3.13 [job
  99372557192](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557192)
  completed in 10m28s; the fixed drill step took 31s.
- Each job recorded Domain + Local `576 passed`, local-stack `224 passed, 3
  skipped`, and Local E2E `2 passed`.

The same SHA's [Container run
33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784)
completed in 2m07s; jobs
[99372557334](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784/job/99372557334)
and
[99372587413](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784/job/99372587413)
were successful. Only Local and Container workflows were triggered for this RC;
no same-SHA Data Lab, Assurance, or Cloud result is claimed.

Python 3.12 published [artifact
9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851),
named `telco-local-release-py3.12-attempt-1`, classified `VERIFIED RC`, retained
14 days: 118,251 bytes, created `2026-08-31T03:42:18Z`, expiring
`2026-09-14T03:42:17Z`, with archive digest
`sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`.
Python 3.13 executed the same drill but intentionally uploaded no duplicate
artifact.

Independent download verified 14 non-link regular files: exactly 13 records
listed by `release-evidence/release-manifest.json`, plus the manifest itself,
with no extra, missing, traversal, link, or drifted member. The 6,847-byte release
manifest has SHA-256
`e13db5a8d326538e7c2aaea1d51f0ce8a71e557e4b0d366659d2873127d8d502`,
status `PASS`, and `failures=[]`. The fifth supplemental file,
`release-evidence/local-backup-recovery-summary.json`, is 1,951 bytes with
SHA-256
`f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`.
Its schema, commit/run/job/Python 3.12 source binding, scope, ten proof flags,
seven delivered flags, twelve non-claims, privacy, report reconstruction, and
release membership all matched the frozen contract.

This evidence permits only `DONE (S4-04 narrow slice)`. S4, Workflow E, P7, and
S7 remain `IN PROGRESS`; Gate E, G5, G2, and G4 remain open; S2-04 remains
`BLOCKED`. This documentation update is later than and not equal to the tested
RC, so it does not redefine the source that the remote evidence attests.
