# NetworkAgent local stack

This directory provides one cross-platform, JSON-only entry point for the
credential-free Local Profile. It does not start Docker, read GCP credentials,
or make an implicit network request. Python 3.12 or 3.13 is required.

Run every command from the repository root with an explicit workspace. The
examples below use the project's isolated `.local` area:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack doctor
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack init
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack status
```

On Linux or macOS, replace `.venv/Scripts/python.exe` with the Python 3.12/3.13
interpreter for the project environment. `doctor` reports the core, governance,
data, optional server, and loopback-port checks without creating the workspace.

## One-command defense demonstration

The following explicit command runs the complete native Local Profile evidence
path without Docker, GCP credentials, a server, or an external network call:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_defense_demo.py --approve-local-simulation
```

It performs one bounded doctor check and then creates two isolated workspaces
under `.local/networkagent-defense`. Both branches initialize the fixed 13,440
KPI rows and 579 safe Trace rows, confirm the same deterministic Incident,
review the allowlisted `LOCAL_SIMULATION` preview, copy its exact action hash
and revision, and verify the complete eight-event governance history. The
success branch must finish at `RESOLVED/PASSED`; the intentional failed
verification branch must finish at `REOPENED/FAILED`. Each branch repeats the
original approval command and proves that terminal state and record counts do
not change.

Standard output is one bounded JSON document. The retained atomic report is
named `defense-demo-report.json`; stdout gives its repository-relative path and
SHA-256. Both marker-owned workspaces are reset even when an operation fails,
while the report directory remains for review. `LOCAL_NATIVE_SIMULATION_EVIDENCE`
means Git was available, the same commit was observed before and after the run,
and the tracked tree stayed clean. Otherwise the honest classification is
`LOCAL_WORKTREE_SIMULATION_EVIDENCE`.

The only accepted argument is the explicit `--approve-local-simulation`
confirmation. No workspace, URL, header, command, actor, Cloud, Docker, or real
action input is accepted. The command does not cover rejection/expiry branches,
container execution, real remediation, Cloud rehearsal, full G2/G4 closure, or
final G5 acceptance.

For the third-party prerequisites, 6–8 minute presentation sequence, report
field checklist, SHA-256 verification, safe failure cleanup, remote RC evidence,
and the exact limitation statement, follow the
[Local native defense demonstration runbook](../../docs/runbooks/local-defense-demo.md).
S7-01 is complete at RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706`:
the Python 3.12/3.13 Local jobs both ran this command and verified the source
binding, two terminal states, and two cleanups. S7-02 is complete at RC
`79feeee6771749bbdd1ce7ce44b77193a1db544f`: Local run 33327786238 passed
on Python 3.12/3.13 with `518 passed`, local-stack `49 passed, 2 skipped`, and
Local E2E `2 passed` per job. Its Python 3.12 VERIFIED RC artifact 9736785325
contains `release-evidence/defense-demo-summary.json` as manifest-verified
supplemental evidence; Python 3.13 also executes the demonstration but does not
upload a second release artifact. The independently downloaded summary is 3,379
bytes with SHA-256
`ae0b412a42d9430a35117dd9e8987662c7359cc95ea72a076fa2f869bcaa51ef`.
The S7-01 historical artifact 9736486858 predates that supplemental file and
must not be presented as the S7-02 evidence package. S7 overall remains in
progress because its broader security, release, operability, Cloud, and real
action boundaries are still open. S2-04 also remains blocked; this local
evidence does not close the complete container Critical/High gate.

## One-command BubbleRAN four-branch defense evidence

Run the S7-03 wrapper from the repository root with both frozen confirmations:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_bubbleran_defense_demo.py --offline --approve-local-simulation
```

No other argument is accepted. The input is four records generated in code
against the reviewed BubbleRAN schema (`CODE_GENERATED_SCHEMA_FIXTURE`), not a
copy or complete execution of the upstream dataset. The wrapper starts the real
loopback Replay/Assurance TCP path, creates four independent Canonical cases,
and verifies a persistent checkpoint at `4/4/4` selected/attempted/delivered
followed by `0/0/0` after reopening. The four fixed branches are approved-pass
`RESOLVED/PASSED`, approved-fail `REOPENED/FAILED`, `REJECTED/NOT_RUN`, and
approval-expired `FAILED/NOT_RUN`. Exactly two ActionRuns and two
VerificationRuns are allowed; every action is `LOCAL_SIMULATION` with
`side_effects=false`.

The final check bypasses the settled checkpoint and redelivers four events.
Incident, Audit, SourceAssociation, and idempotency deltas must all remain zero,
and the four Incident objects must remain deeply equal. A successful invocation
removes its identity-bound temporary work tree and retains only
`local-bubbleran-defense-report.json`. The report and release summary omit raw
records, domain/event identifiers, source/absolute locations, and other frozen
forbidden keys.

S7-03 is `DONE` only for this narrow entry point on RC
`46318cbf84b65c3060358dffb49b829479803308`. [Assurance run
33366606140](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140)
passed jobs 99408450337/99408450434/99408450435/99408450555; [Local run
33366606118](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118)
passed jobs 99408450116/99408450386; [Container run
33366606112](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112)
passed jobs 99408450317/99408503334. Across the three workflows, all 8 jobs and
122 steps succeeded; 11 conditional release/matrix steps were skipped as
designed.

The sole S7-03 evidence carrier is Python 3.12 Assurance [VERIFIED RC artifact
9748618894](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/artifacts/9748618894),
named `telco-assurance-release-py3.12-attempt-1`: 248,105 bytes, archive digest
`sha256:975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`.
Independent download found exactly 13 non-link regular entries (12 payloads plus
the manifest), manifest `PASS` with `failures=[]`, and no CSV, DuckDB, JSONL, or
checkpoint. `local-bubbleran-defense-summary.json` is 2,374 bytes / SHA-256
`161354c5715b8a46730debcf7dd37658158d1ec338b469aa24f2bb2f3ddbc855`;
removing its stdout-only `report` envelope reconstructs the 2,225-byte report /
SHA-256 `4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`.

Follow the [Local BubbleRAN four-branch defense
runbook](../../docs/runbooks/local-bubbleran-defense-demo.md) for the 6–8 minute
sequence, field allowlist, stable failures, identity-aware cleanup, independent
artifact reconstruction, and all ten `not_claimed` values. P3e-5 now has its
independent fixture defense entry, but P3e-5/P3e remain `IN PROGRESS` pending
RCAEval, the second path, cross-event aggregation, and complete upstream
validation. S4/Workflow E/P7/S7 remain `IN PROGRESS`; Gate E/G5/G2/G4 remain
open, S2-04 remains `BLOCKED`, and P6 unified UI remains `NOT STARTED`. This
documentation update is later than and not equal to the tested RC.

## One-command local observability evidence

The following fixed wrapper reruns the same native defense demonstration and
adds bounded, privacy-minimized stage evidence without changing the underlying
business flow:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_observability_demo.py --approve-local-simulation
```

Its successful `networkagent-local-observability/1.0` document contains 21
strictly ordered child-process stage events plus one finalization event. Each
event contains only `sequence/stage/branch/attempt/outcome/duration_ms/error_class`.
The timing snapshot is diagnostic and has one sample. The
metric series are in-report aggregates using only the fixed
`branch/error_class/outcome/stage` labels; they are not Prometheus metrics. The
four `LOCAL_*` alerts are evaluated only in the report and do not send an
external notification. `observation_id`, the source commit, and the defense
report SHA correlate this local evidence only; `propagated_trace=false`.

The wrapper does not record absolute paths, child stdout/stderr, the
environment, or raw arguments. The 579 safe Trace rows loaded by the Local
Profile are input data records, not OpenTelemetry spans. The report explicitly
does not claim OpenTelemetry export/Collector, a cross-HTTP/Replay/A2A/MCP
trace, Prometheus, external alert delivery, SLOs, Collector failure tolerance,
Gate E/G5 closure, or Cloud/production observability.

The narrow S4-01 slice is complete for RC
`cb4a4e7191f67aa71ef980668352d55001e23142`.
[Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665)
passed on Python 3.12/3.13: each job reported Domain + Local `518 passed`,
local-stack `66 passed, 2 skipped`, and Local E2E `2 passed`; Python 3.12 also
passed `18` release-boundary tests. Only Local was selected by the RC's path
filters, so no same-SHA Data Lab or Assurance run is claimed.

Python 3.12 published
[VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310),
named `telco-local-release-py3.12-attempt-1`: 106,309 bytes, archive digest
`sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`,
expiring `2026-09-13T19:33:25Z`. Independent download confirmed an exact
11-file closure: 10 manifest records plus the manifest. The defense summary is
3,379 bytes / SHA-256
`14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7`;
the observability summary is 9,178 bytes / SHA-256
`2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`.
Python 3.13 executes and verifies both demonstrations but does not upload a
duplicate release artifact.

Follow the
[Local observability evidence runbook](../../docs/runbooks/local-observability-demo.md)
for the exact event graph, report SHA verification, four alert procedures, and
limitations. S4, Workflow E, and S7 remain in progress; Gate E, G5, G2, and G4
remain open, and S2-04 remains blocked.

## One-command Canonical lifecycle projection evidence

The following fixed wrapper projects the durable records created by the same
native two-branch defense flow without changing them:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_lifecycle_evidence_demo.py --approve-local-simulation
```

The only accepted argument is `--approve-local-simulation`. Standard output is
one `networkagent-local-lifecycle-evidence/1.0` JSON document. Its `success` and
`failure` branches are `networkagent-local-lifecycle-projection/1.0` documents
with exactly eight revision groups and 14 events each. The success branch ends
at `RESOLVED/PASSED`; the intentional failure branch ends at
`REOPENED/FAILED`. Both projections require contiguous revisions, exact durable
bindings, one ActionRun attempt, exact retry without record amplification,
marker-owned cleanup, `read_only=true`, `side_effects=false`, and
`distributed_trace=false`.

Each projected event contains only
`sequence/occurred_at/record_type/component/operation/outcome`; time is a record
attribute, not an ordering label. The projections omit domain and workspace
identifiers/hashes, absolute paths, correlation values, raw records, resource
or KPI values, root-cause text, evidence URIs, actors, reasons, environment,
stdout/stderr, and idempotency keys. The outer evidence envelope retains only
the bounded source/report integrity fields needed to bind and verify the run.

S4-02 is `DONE` for RC
`69643e8a6f79b1264d60e5517eeb9a24035c8e7d`. [Local run
33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831)
completed Python 3.12 [job
99323794962](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323794962)
and Python 3.13 [job
99323795037](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323795037)
successfully. Each job reported Domain + Local `576 passed`, local-stack
`89 passed, 2 skipped`, and Local E2E `2 passed`; Python 3.12 also passed `18`
release-boundary tests.

Python 3.12 published [VERIFIED RC artifact
9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391),
named `telco-local-release-py3.12-attempt-1`: 115,482 bytes, archive digest
`sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`,
expiring `2026-09-13T21:30:29Z`. Independent download confirmed 12 files: 11
manifest records plus the manifest, with exact closure. The lifecycle summary
is 8,431 bytes / SHA-256
`5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`;
removing its stdout-only `report` envelope reconstructs the persisted report at
8,290 bytes / SHA-256
`21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`.

Follow the [Local Canonical lifecycle projection
runbook](../../docs/runbooks/local-lifecycle-projection.md) for the exact
14-node graph, field allowlists, artifact verification, same-SHA workflow
evidence, and limitations. This slice does not provide runtime structured logs,
OpenTelemetry/Collector, Prometheus, a distributed trace, SLOs, external alert
delivery, or Cloud production evidence. S4, Workflow E, P7, and S7 remain in
progress; Gate E/G5/G2/G4 remain open, and S2-04 remains blocked.

## One-command fixed-window acceptance SLO evidence

Run the fixed S4-03 wrapper from the repository root:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_slo_evidence_demo.py --approve-local-simulation
```

The only accepted argument is `--approve-local-simulation`. Each invocation
runs three fresh S4-01 observability windows sequentially in distinct
marker-owned run directories. The aggregate
`networkagent-local-slo-evidence/1.0` report evaluates five integer-ppm Local
acceptance SLIs over 66 stage events: stage-command success `66/66`, expected
branch outcome `6/6`, exact retry integrity `6/6`, workspace cleanup `6/6`, and
observation-contract validity `3/3`. Each objective is 1,000,000 ppm with a zero
error budget. The deliberate `REOPENED/FAILED` branch is expected, and timing is
diagnostic only; it never enters an SLI.

The persisted `local-slo-report.json` distinguishes a trustworthy `OK`, a
measurable `BREACH`, and an untrustworthy `ERROR`. A later healthy window cannot
hide a miss in the same group, and recovery requires a completely new
three-window invocation. Public success output exposes only the fixed filename,
byte count, and SHA-256 needed to reconstruct and verify the persisted body.

S4-03 is `DONE` only for this narrow slice on RC
`faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`. [Local run
33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133)
completed in 11m40s. Python 3.12 [job
99333812338](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812338)
succeeded in 10m51s and its fixed SLO step took 2m38s; Python 3.13 [job
99333812397](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812397)
succeeded in 11m36s and its fixed SLO step took 2m51s. The Python 3.13 release
steps were skipped by the matrix as designed.

Python 3.12 published the 14-day [VERIFIED RC artifact
9740377450](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/artifacts/9740377450),
named `telco-local-release-py3.12-attempt-1`: 117,046 bytes, archive digest
`sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`.
Independent download confirmed 13 files, exactly 12 manifest records plus the
manifest, with all bytes/SHA values matching, manifest `PASS`, and
`failures=[]`. `release-evidence/local-slo-summary.json` is 3,271 bytes / SHA-256
`ae181eaffe6da11c5dd0cdea07dcfcba3a400daaf6ed44352b1e573faa5f489b`;
removing its stdout-only `report` envelope reconstructs
`local-slo-report.json` at 3,136 bytes / SHA-256
`2538629be3133920e76f2de9e0fa0ff9575853095538c266efc6e544d02c5c64`.
The schema, `LOCAL_DEMO_ACCEPTANCE_SLO_EVIDENCE` classification, source binding,
three windows, five `OK` SLIs, no-breach evaluation, privacy `PASS`, and all 12
`not_claimed` entries matched the frozen contract. Only the Local workflow was
triggered.

Follow the [Local fixed-window acceptance SLO
runbook](../../docs/runbooks/local-slo-evidence.md) for independent report
reconstruction, breach handling, and cleanup rules. This slice is not a
time-based availability or latency SLO, long-term statistical reliability,
runtime structured logging, OpenTelemetry/Collector, Prometheus, distributed
trace, external alert delivery, backup/recovery, or Cloud/production evidence.
S4, Workflow E, P7, and S7 remain in progress; Gate E/G5/G2/G4 remain open, and
S2-04 remains blocked.

## One-command Local cold backup and recovery evidence

Run the fixed S4-04 wrapper from the repository root:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_backup_restore_demo.py --approve-local-simulation
```

The only accepted argument is the explicit Local simulation confirmation. The
wrapper creates one isolated success workspace, completes the native governance
lifecycle, checkpoints and copies the full DuckDB database into an exact two-file
backup, resets and freshly initializes the workspace, rejects a deliberately
corrupted copy without changing that fresh database, restores the valid backup,
and repeats the exact restore. The first valid restore must report `changed=true`;
the retry must report `changed=false`, and both the catalog/row counts and the
complete read-only lifecycle projection must remain equivalent. A successful run
removes the workspace and both temporary backup trees, leaving only the atomic
`local-backup-recovery-report.json` evidence body.

For an operator-controlled offline backup, stop every Local writer first, then
use a new destination directory:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack backup --destination .local/networkagent-backups/BACKUP_ID
```

Keep the returned lowercase manifest SHA-256. With all writers still stopped,
restore only after reviewing the source and supplying that exact hash plus the
explicit confirmation:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack restore --source .local/networkagent-backups/BACKUP_ID --expected-manifest-sha256 MANIFEST_SHA256 --yes
```

The portable backup closure is exactly `networkagent.duckdb` and
`backup-manifest.json`, under schema `networkagent-local-cold-backup/1.0`; limits
are 128 MiB for the database and 16 KiB for the manifest. The manifest binds the
checkpointed database bytes/SHA, DuckDB library/storage versions, Local/Assurance
schema versions, catalog/table/row counts, and a logical catalog+row fingerprint.
Links, hardlinks, sidecars, duplicate or non-canonical JSON, extra/missing members,
hash drift, catalog drift, path swaps, and concurrent writers fail closed. Cleanup
deletes only entries whose captured directory/file identities still match; an
unknown or raced replacement is preserved for inspection, never recursively
removed.

S4-04 is `DONE` only for this narrow slice on RC
`54551feb43be60c3b9bdd5eab076cdb7c0aba61a`. [Local run
33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792)
completed in 13m04s. Python 3.12 [job
99372557281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557281)
succeeded in 13m00s and its backup/recovery step took 37s; Python 3.13 [job
99372557192](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557192)
succeeded in 10m28s and its step took 31s. Each job reported Domain + Local
`576 passed`, local-stack `224 passed, 3 skipped`, and Local E2E `2 passed`.
[Container run
33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784)
also completed jobs 99372557334 and 99372587413 successfully.

Python 3.12 published the 14-day [VERIFIED RC artifact
9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851),
named `telco-local-release-py3.12-attempt-1`: 118,251 bytes, archive digest
`sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`.
Independent download confirmed 14 non-link regular files, exactly 13 manifest
records plus the manifest, with manifest `PASS` and `failures=[]`. The fifth
supplemental evidence `local-backup-recovery-summary.json` is 1,951 bytes /
SHA-256 `f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`;
removing its stdout-only `report` envelope reconstructs the persisted report at
1,804 bytes / SHA-256
`f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`.
Python 3.13 verifies the drill but does not upload a duplicate artifact.

Follow the [Local cold backup and recovery
runbook](../../docs/runbooks/local-backup-restore.md) for stopped-writer
prerequisites, manifest/logical verification, stable error handling, artifact
reconstruction, privacy, cleanup, and every explicit non-claim. This slice does
not establish online backup, production HA, multi-replica failover, RPO/RTO,
power-loss durability, encryption/signing, off-host retention, cross-version
migration, Cloud/Spanner recovery, or production recovery. S4, Workflow E, P7,
and S7 remain `IN PROGRESS`; Gate E/G5/G2/G4 remain open, and S2-04 remains
`BLOCKED`.

## One-command Local runtime trace evidence

Run the fixed S4-05 wrapper from the repository root:

```powershell
.venv/Scripts/python.exe tools/local-stack/run_runtime_trace_demo.py --approve-local-simulation
```

The explicit Local simulation confirmation is the only accepted argument. One
fixed BubbleRAN event crosses the real loopback Replay sender and HTTP receiver,
passes bounded durable DuckDB readback, and then enters the real A2A Analyze
handler. `X-NetworkAgent-Trace-Id` is deterministically derived from the
validated source event and binds six ordered `OK` events across four components:

```text
sender:     REPLAY_REQUEST_VALIDATED
repository: INCIDENT_DURABLE_READBACK
receiver:   REPLAY_RESPONSE_ACCEPTED
sender:     REPLAY_DELIVERY_ACKNOWLEDGED
a2a:        ANALYZE_REQUEST_VALIDATED
a2a:        ANALYZE_COMPLETED
```

All six header/durable/audit/association/request/result bindings must pass. The
A2A request changes only transport table `assurance_a2a_tasks`; the other nine
tracked tables and the Canonical domain remain unchanged. Actions, approvals,
executions, and verifications remain `0 -> 0`. This is not a whole-database
read-only claim.

A successful run retains `local-runtime-trace-report.json` and the six-line raw
`local-runtime-events.jsonl` under a new identity-bound directory in
`.local/networkagent-runtime-trace/`, while removing its temporary scenario
workspace. The raw JSONL contains the correlation value, remains Local-only,
and must never enter `release-evidence` or an uploaded artifact. The persisted
report and release summary omit raw events/payloads, correlation and domain
identifiers, and absolute paths.

S4-05 is `DONE` only for corrective RC
`2e59d7ca88cc550e315d63e80339909ef619cd2c`. [Assurance run
33362806092](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092)
passed jobs 99397345468/99397345590/99397345635/99397345601; [Local run
33362806180](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180)
passed jobs 99397346249/99397346041; [Container run
33362806104](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104)
passed jobs 99397345678/99397392344. The Python 3.12 Assurance job published
[VERIFIED RC artifact
9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240),
named `telco-assurance-release-py3.12-attempt-1`: 246,678 bytes with archive
digest
`sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`.
Independent download verified exactly 12 entries (11 manifest records plus the
manifest), no JSONL, and a reconstructed 1,651-byte report / SHA-256
`5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`.

The initial feature candidate `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc`
is not a successful RC: Local run 33362166565 failed while collecting trace
tests that belonged to the Assurance profile. The corrective commit migrated
those tests and removed duplicate Local collection. Follow the [Local
single-process runtime trace
runbook](../../docs/runbooks/local-runtime-trace.md) for the exact seven-field
event schema, six bindings, failure codes, privacy rules, artifact
reconstruction, ten `not_claimed` values, and independent audit steps.

This slice is not OpenTelemetry/Collector, Prometheus, distributed or
cross-process/multi-event correlation, MCP propagation, sink delivery,
external alerting, Cloud/production observability, or Gate E/G5 closure. S4,
Workflow E, P7, and S7 remain `IN PROGRESS`; Gate E/G5/G2/G4 remain open,
S2-04 remains `BLOCKED`, and this documentation update is later than and not
equal to the tested RC.

## Safe governance demo

Actions are disabled by default. This command confirms the deterministic sample
Incident, runs local RCA, and stops at `AWAITING_APPROVAL`:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack demo --confirm-incident
```

For a complete simulation, first run that preview with
`--action-mode simulate`. Review the returned `action_hash`, target resources,
risk, and `expected_revision`. A separate command must copy the exact binding:

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py `
  --workspace .local/networkagent-stack `
  --action-mode simulate `
  demo `
  --approve-action `
  --reason "reviewed isolated local simulation" `
  --expected-action-hash HASH_FROM_PREVIEW `
  --expected-revision REVISION_FROM_PREVIEW
```

No command can perform a real network action. `simulate` writes only the local
Canonical Incident, action-run, verification, and audit records. Use
`--verification-outcome failed` on the approval command to exercise the
`REOPENED` path; it never reports that path as a closed loop. If a committed
approval expires before execution resumes, the Incident moves to `FAILED` with
zero action/verification records and the CLI reports `APPROVAL_NOT_EFFECTIVE`.

## Optional foreground service

When the Assurance dependencies are installed, the following command runs the
existing A2A service in the foreground. The bind address and published AgentCard
remain fixed to `127.0.0.1`; local-stack never creates a background process.

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack --port 8085 serve
```

From another local shell, the supported operational checks are:

```powershell
Invoke-RestMethod http://127.0.0.1:8085/local/v1/healthz
Invoke-RestMethod http://127.0.0.1:8085/local/v1/readyz
Invoke-RestMethod http://127.0.0.1:8085/local/v1/version
```

`healthz` means only that the foreground process responds. `readyz` adds one
bounded read of the local Canonical Incident repository; a fixed `503` means
the service must not receive replay or governance traffic yet. `version`
returns allowlisted package/API/schema versions and is not a signature. All
three reject a non-loopback Host or direct client, accept no query string, and
must not be placed behind a reverse proxy or port forward.

The foreground runner explicitly disables proxy-header trust and uses a
bounded local HTTP protocol: at most 32 live transports, a one-second request
header deadline, one admitted request body with no queue and a two-second body
deadline, plus one isolated Governance/Fault business operation with no queue
and a five-second deadline. A timeout never cancels an operation whose commit
state is unknown; retry only with the same idempotency-bound request after the
service is no longer busy. Fixed JSON 408/503 responses use
`Connection: close`. An over-cap socket may be reset before that best-effort
JSON reaches the caller, especially when the peer already queued unread bytes.

## Reset safety

`reset` without `--yes` only returns a JSON confirmation requirement. The
confirmed form removes the marker-owned `state` and `artifacts` directories and
the marker. Unknown files are preserved, and a source directory, repository
root, home directory, filesystem root, symlink/junction/reparse workspace,
UNC/device path, non-fixed Windows drive, or unmarked directory is rejected.

```powershell
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack reset
.venv/Scripts/python.exe tools/local-stack/local_stack.py --workspace .local/networkagent-stack reset --yes
```

Exit status `0` means success, `1` means not ready or confirmation required, and
`2` means a safe rejection. Standard output and standard error contain one JSON
document and never include the resolved workspace path.
