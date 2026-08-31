# Network Agent Demonstration

This repository demonstrates a set of network agents that manage the end to end lifecycle of a virtual telecoms network. The agents are built on [Google's Autonomous Network Operations Framework](https://cloud.google.com/blog/topics/telecommunications/the-autonomous-network-operations-framework-for-csps?e=48754805?utm_source%3Dlinkedin) and implemented using Google's [agent development kit](https://google.github.io/adk-docs/) and [agent to agent protocol](https://a2aprotocol.ai/). The agents are designed to help Network Architects and Operators easily deploy and manage complex Telco infrastructure and network function software.

## Unified assurance platform development

The `unified-platform` branch is progressively combining this lifecycle platform
with the deterministic LTE assurance demo. P2a now provides a cloud-independent
Local Profile for safe CSV/DuckDB ingestion, anomaly preview, explicit Incident
confirmation, and read-only rule-based RCA. It does not require GCP, ADK, a
model API, or network access.

The Local Profile now also has a single repository entry point for deployment
checks, initialization, status, a deterministic governance demo, an optional
foreground loopback service, and marker-scoped reset:

```text
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack doctor
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack init
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack demo --confirm-incident
```

For a defense-ready, one-command native demonstration that exercises both the
successful and failed-verification branches, run:

```text
python tools/local-stack/run_defense_demo.py --approve-local-simulation
```

The command creates two isolated marker-owned workspaces under
`.local/networkagent-defense`, verifies `RESOLVED/PASSED` and
`REOPENED/FAILED`, repeats the exact approval request to prove idempotency,
resets both workspaces, and writes an atomic JSON evidence report with its
SHA-256. It never starts Docker, reads Cloud credentials, or performs a real
network action. Evidence is commit-bound only when Git is available, the
tracked tree remains clean, and the same commit is observed before and after
the run.

For the S7-03 four-branch BubbleRAN fixture defense demonstration, run the
single fixed offline command:

```text
python tools/local-stack/run_bubbleran_defense_demo.py --offline --approve-local-simulation
```

This entry point generates four schema-valid records in code; it does not
download, bundle, or claim to reproduce the complete upstream BubbleRAN
benchmark. The records cross real loopback TCP into four independent Canonical
Incidents. A persistent checkpoint must report `4/4/4` selected/attempted/
delivered on its first use and `0/0/0` after reopening. The four governance
branches finish at `RESOLVED/PASSED`, `REOPENED/FAILED`, `REJECTED/NOT_RUN`, and
approval-expired `FAILED/NOT_RUN`. There are exactly two ActionRuns and two
VerificationRuns; the action is `LOCAL_SIMULATION` and `side_effects=false`.
Bypassing the settled checkpoint deliberately redelivers all four events while
Incident, Audit, SourceAssociation, and idempotency counts remain unchanged and
the four Incidents remain deeply equal.

S7-03 is `DONE` only for this narrow fixture-based defense entry point on RC
`46318cbf84b65c3060358dffb49b829479803308`. Its [Assurance run
33366606140](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140)
(jobs 99408450337/99408450434/99408450435/99408450555), [Local run
33366606118](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118)
(jobs 99408450116/99408450386), and [Container run
33366606112](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112)
(jobs 99408450317/99408503334) passed 8/8 jobs, with 122 successful steps and
11 expected conditional skips. Only Python 3.12 Assurance published the
[VERIFIED RC artifact
9748618894](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/artifacts/9748618894),
`telco-assurance-release-py3.12-attempt-1`: 248,105 bytes, archive digest
`sha256:975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`.
Independent download confirmed an exact 13-entry closure (12 payloads plus the
manifest), manifest `PASS` with `failures=[]`, and no CSV, DuckDB, JSONL, or
checkpoint. The summary is 2,374 bytes / SHA-256
`161354c5715b8a46730debcf7dd37658158d1ec338b469aa24f2bb2f3ddbc855`;
its reconstructed report is 2,225 bytes / SHA-256
`4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`.

See the [Local BubbleRAN four-branch defense
runbook](docs/runbooks/local-bubbleran-defense-demo.md) for the 6–8 minute
presentation, fields, failures, cleanup, independent artifact audit, privacy
rules, and ten exact non-claims. P3e-5 now has an independent fixture defense
entry point, but P3e-5 and P3e remain `IN PROGRESS`: RCAEval, the second data
path, cross-event aggregation, and complete upstream validation are unfinished.
S4, Workflow E, P7, and S7 remain `IN PROGRESS`; Gate E/G5/G2/G4 remain open,
S2-04 remains `BLOCKED`, and P6 unified UI remains `NOT STARTED`. This
documentation update is later than and not equal to the tested RC.

S7-04 adds a second, complementary Local Data Lab path: a five-case, pinned
upstream RCAEval `re2ob` answer-blind evaluation. Its single fetch-and-evaluate
command is:

```text
telco-lab --workspace .local/networkagent-rcaeval run rcaeval-re2ob-multisource-rca --accept-license MIT
```

The command verifies 16 MIT-licensed resources at revision
`afeacb11bcc94dadfd1c8f483ee4377b2b8b614e` (53,433,532 bytes), builds only
aggregate label-free features, ranks and seals all five cases before loading
answers, creates a batch commitment, then reuses the same seals for private
truth evaluation. The fixed slice reports five ranked cases, zero inconclusive,
Accuracy@1 through @5, average@5, and MRR all at 1,000,000 ppm. Its ownership
validity is 104,838 ppm (39 truth-owned references out of 372 ranked
references); that value is not an accuracy or annotation-quality metric.

S7-04 is `DONE` only for this narrow slice on RC
`b8a9e958a0a3354634f87e2fbc8f76aaf60913dd`. The [Data Lab push run
33385845017](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845017),
[Assurance push run
33385845041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845041),
and [Container push run
33385844990](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385844990)
all succeeded. The explicit [Data Lab dispatch
33385881296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296)
also passed its Python 3.12, minimum-Pydantic, and Python 3.13 jobs. Its only
[VERIFIED RC artifact
9755569487](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/artifacts/9755569487),
`telco-lab-release-py3.12-attempt-1`, is 148,959 bytes with archive SHA-256
`8afc11102a17310c78e1295a15a758396d904c9aea964985801c0e9e30fd88f4`.
Independent download confirmed an exact 10-file closure (9 manifest records
plus the manifest), a canonical 2,408-byte RCAEval summary with SHA-256
`999a35e25bfa53aaf3ef7f86f7eaf4b596c17b25366ba85cf7193724a41d0b38`,
two wheels / 47 members with no Parquet, Arrow, Feather, IPC, ORC, CSV,
DuckDB, or JSONL payload, an 8-component CycloneDX 1.4
SBOM including PyArrow 25.0.0, `pip-audit` 0, and wheel scan `PASS`.

See the [Local RCAEval answer-blind evaluation
runbook](docs/runbooks/local-rcaeval-evaluation.md) for licensing, cache/offline
verification, the exact pre-reveal ordering, interpretation, failures, cleanup,
artifact review, and all 17 exact non-claims. This is not the complete upstream
benchmark, production accuracy, generalization, causal identification, online
evaluation, live remediation, externally timestamped evidence, Cloud/OTel, or
a dashboard. RCAEval replay and cross-event aggregation remain unfinished;
P3e, S4, Workflow E, P7, and S7 remain `IN PROGRESS`, Gate E/G5/G2/G4 remain
open, S2-04 remains `BLOCKED`, and P6 remains `NOT STARTED`. This documentation
update is later than and not equal to the tested RC.

For a bounded, privacy-minimized observation of the same defense path, run:

```text
python tools/local-stack/run_observability_demo.py --approve-local-simulation
```

This wrapper records 22 fixed local stage events, a diagnostic one-sample
timing snapshot, low-cardinality in-report metric aggregates, and four
in-report alert evaluations. It does not export OpenTelemetry, propagate a
distributed trace, expose Prometheus metrics, deliver external alerts, or
define an SLO; `propagated_trace=false`. The 579 safe Trace rows are Local
dataset inputs, not OpenTelemetry spans. See the
[Local observability evidence runbook](docs/runbooks/local-observability-demo.md)
for the exact contract and limitations.

The narrow S4-01 slice is complete for RC
`cb4a4e7191f67aa71ef980668352d55001e23142`:
[Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665)
passed on Python 3.12/3.13, and Python 3.12 published
[VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310).
Independent download confirmed 11 files (10 manifest records plus the
manifest) with no extra, missing, or drifted entry. Only Local was selected by
the RC's path filters; no same-SHA Data Lab or Assurance run is claimed. S4,
Workflow E, and S7 remain in progress; Gate E and G5 remain open.

For a read-only, revision-grouped projection of the durable Canonical lifecycle,
run:

```text
python tools/local-stack/run_lifecycle_evidence_demo.py --approve-local-simulation
```

The S4-02 wrapper reuses the fixed native defense flow and emits two projections:
`RESOLVED/PASSED` and `REOPENED/FAILED`. Each branch has exactly eight contiguous
revision groups and 14 allowlisted events, with `read_only=true`, exact durable
bindings, one execution attempt, `side_effects=false`, and
`distributed_trace=false`. The projection contains no domain/workspace
identifiers or hashes, absolute paths, pseudonymous correlation, raw records,
resource/KPI values, root cause, or evidence URI; source and report integrity
metadata remain outside the projection. See the
[Local Canonical lifecycle projection runbook](docs/runbooks/local-lifecycle-projection.md)
for the exact field and event graph.

S4-02 is complete for RC
`69643e8a6f79b1264d60e5517eeb9a24035c8e7d`: [Local run
33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831)
passed on Python 3.12/3.13, and Python 3.12 published [VERIFIED RC artifact
9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391).
The same SHA's Assurance, Container, and Cloud workflows also passed; Data Lab
was not triggered. This Cloud result is CI/Emulator evidence, not Cloud Staging
or production evidence. S4, Workflow E, P7, and S7 remain in progress; Gate E,
G5, G2, and G4 remain open, and S2-04 remains blocked.

For a fixed three-window Local acceptance SLI/SLO evaluation, run:

```text
python tools/local-stack/run_slo_evidence_demo.py --approve-local-simulation
```

The S4-03 wrapper executes three fresh, isolated S4-01 windows sequentially and
evaluates five strict integer-ppm SLIs over 66 stage events. A complete healthy
group requires `66/66`, `6/6`, `6/6`, `6/6`, and `3/3`; every objective is
1,000,000 ppm with a zero error budget. The deliberate `REOPENED/FAILED` branch
is an expected business outcome. `OK`, measurable `BREACH`, and untrustworthy
`ERROR` remain separate states, and recovery evidence can only come from a new
three-window invocation. See the [Local fixed-window acceptance SLO
runbook](docs/runbooks/local-slo-evidence.md) for the frozen contract.

The S4-03 narrow slice is `DONE` for RC
`faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`: [Local run
33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133)
completed in 11m40s, with Python 3.12 [job
99333812338](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812338)
and Python 3.13 [job
99333812397](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812397)
successful. Python 3.12 published [VERIFIED RC artifact
9740377450](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/artifacts/9740377450),
117,046 bytes with archive digest
`sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`.
Independent download confirmed 13 files (12 manifest records plus the manifest)
with exact closure and a manifest PASS. The five SLIs were all `OK`; evaluation
was `OK` with no breaches and privacy was `PASS`. Only the Local workflow was
triggered for this RC. This fixed acceptance sample is not a time-based or
latency SLO, long-term statistical reliability, OpenTelemetry/Prometheus,
external alert delivery, backup/recovery, or Cloud/production evidence. S4,
Workflow E, P7, and S7 remain in progress; Gate E/G5/G2/G4 remain open, and
S2-04 remains blocked.

For one fixed Local cold-backup and recovery drill, run:

```text
python tools/local-stack/run_backup_restore_demo.py --approve-local-simulation
```

The S4-04 wrapper stops at a deliberately narrow offline boundary: one stopped
Local writer, one DuckDB database, one two-file cold backup, a corrupt-copy
rejection against an unchanged fresh database, one atomic restore, an exact
idempotent retry, lifecycle equivalence, and identity-bound cleanup. The backup
schema is `networkagent-local-cold-backup/1.0`; the evidence schema is
`networkagent-local-backup-recovery/1.0`. See the [Local cold backup and recovery
runbook](docs/runbooks/local-backup-restore.md) before using the lower-level
`backup` or `restore` commands.

This S4-04 narrow slice is `DONE` for RC
`54551feb43be60c3b9bdd5eab076cdb7c0aba61a`. [Local run
33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792)
completed in 13m04s; its Python 3.12/3.13 jobs both passed Domain + Local
`576 passed`, local-stack `224 passed, 3 skipped`, Local E2E `2 passed`, and the
fixed recovery drill. [Container run
33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784)
also passed both jobs. Python 3.12 published the 14-day [VERIFIED RC artifact
9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851),
118,251 bytes with archive digest
`sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`.
Independent download confirmed 14 regular files (13 manifest records plus the
manifest), exact bytes/SHA closure, manifest `PASS`, `failures=[]`, privacy
`PASS`, and the fifth supplemental recovery evidence. This is not online,
off-host, encrypted/signed, multi-replica, cross-version, Cloud/Spanner, RPO/RTO,
power-loss, HA, or production recovery evidence. S4, Workflow E, P7, and S7
remain in progress; Gate E/G5/G2/G4 remain open, and S2-04 remains blocked.

For one fixed single-process runtime correlation through the real Local replay
and Assurance path, run:

```text
python tools/local-stack/run_runtime_trace_demo.py --approve-local-simulation
```

The S4-05 wrapper sends one fixed BubbleRAN event over real loopback TCP, checks
durable DuckDB readback, and issues a real A2A Analyze request. The derived
`X-NetworkAgent-Trace-Id` binds exactly six ordered events across `sender`,
`repository`, `receiver`, and `a2a`, with six durable/request/result bindings.
Analyze leaves the Canonical domain and nine tables unchanged but writes the
expected A2A transport state in `assurance_a2a_tasks`; the whole database is
therefore not claimed read-only. Actions, approvals, executions, and
verifications remain `0 -> 0`. Raw JSONL stays only in the local run directory
and is never release evidence. See the [Local single-process runtime trace
runbook](docs/runbooks/local-runtime-trace.md) for the frozen event, privacy,
failure, artifact-audit, and non-claim contract.

S4-05 is `DONE` only for this narrow slice on corrective RC
`2e59d7ca88cc550e315d63e80339909ef619cd2c`. Its [Assurance run
33362806092](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092)
(jobs 99397345468/99397345590/99397345635/99397345601), [Local run
33362806180](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180)
(jobs 99397346249/99397346041), and [Container run
33362806104](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104)
(jobs 99397345678/99397392344) all passed. Python 3.12 Assurance published the
14-day [VERIFIED RC artifact
9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240),
`telco-assurance-release-py3.12-attempt-1`, 246,678 bytes with archive digest
`sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`.
Independent download confirmed 12 files (11 manifest records plus the
manifest), exact closure, no raw JSONL, and a reconstructed 1,651-byte report
with SHA-256
`5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`.

The first feature commit `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc`
is historical only: its Local workflow failed while collecting the misplaced
Assurance-owned trace tests. The corrective RC moved those tests to the
Assurance profile and removed duplicate Local collection. This result is not
OpenTelemetry/Prometheus, distributed or cross-process/multi-event correlation,
MCP propagation, external alert delivery, Cloud/production observability, or a
full-database read-only claim. S4, Workflow E, P7, and S7 remain in progress;
Gate E/G5/G2/G4 remain open, and S2-04 remains blocked. This documentation
update is later than and not equal to the tested RC.

The demo stops at `AWAITING_APPROVAL`. A second command may enable
`--action-mode simulate` and approve only the exact `action_hash` and Incident
revision returned by that preview. The sole action is `LOCAL_SIMULATION`;
successful verification reaches `RESOLVED`, while failed verification reaches
`REOPENED`. This path never calls GCP, Engineer, MCP, GitOps, or a Network
Operator and cannot perform a real network change.

The foreground Assurance process also exposes a strict loopback-only
Governance HTTP surface under `/local/v1/incidents/{incident_id}` for reading,
preparing, deciding, and executing the same local simulation lifecycle. POST
operations require an explicit `governance-v1` operation header and strict JSON;
the existing A2A JSON-RPC and Agent Card routes remain unchanged. A separate
`POST /local/v1/faults/replay` route accepts only the public, versioned
`ReplayWirePayload` contract from a loopback peer. It acknowledges with HTTP
202 only after bounded readback verifies the current Incident's immutable
facts, its initial revision-0 Audit, and its source-event association. Neither
local route can invoke a real network action.

The same direct-loopback surface exposes `GET /local/v1/healthz`, `readyz`,
and `version`. Liveness does not read a repository; readiness performs one
bounded Canonical repository read and returns a fixed 503 for dependency
failure, timeout, or a still-running timed-out worker; version returns only
allowlisted package and contract versions. All standard non-GET methods use the
same bounded JSON 405 contract; HEAD keeps the normal empty-body semantics.
These unsigned local probes do not attest Cloud readiness or deployment
identity.

The direct h11 ingress admits at most 32 live transports and applies a
one-second header deadline. Governance, Fault, and A2A share one request-body
slot with zero queue and a two-second body deadline; Governance/Fault also
share one isolated business worker with zero queue and a five-second operation
deadline. Busy, timeout, unknown-path, and wrong-method responses are fixed,
non-reflecting JSON and close the connection. Header/body timers remain
cooperative on the ASGI event loop: legacy synchronous A2A DuckDB work can
delay them, and SDK-managed background/streaming tasks can outlive the
synchronous admission lease. Only Governance/Fault business work is claimed
to have the dedicated worker isolation described above.

P3e adds an independent Local Data Lab for reproducible tests against vetted
public telecom datasets. Dataset downloads are always explicit, license-gated,
version/checksum pinned, cached outside Git, and converted through privacy-safe
adapters before evaluation.

```text
telco-lab --workspace .local/telco-lab run \
  bubbleran-persistent-interference \
  --accept-license CC-BY-SA-4.0 \
  --overlap-threshold 0.1

telco-lab --workspace .local/telco-lab evaluate \
  bubbleran-persistent-interference \
  --overlap-threshold 0.1
```

The first command explicitly fetches the pinned artifacts; the second is fully
offline and fails closed unless every cached artifact still matches its lock.
Third-party data is never bundled in Git or project wheels and retains its
upstream license. BubbleRAN observations can also be converted into a bounded,
label-free replay plan. `telco-lab` provides the shared strict wire model, an
immediate delivery helper, and a monotonic paced runner with bounded deadline,
cancellation evidence, and opt-in finite retries for transient network/timeout
failures. The Assurance receiver maps each validated source event to its own
5G SA Canonical Incident. For the exact pinned BubbleRAN scenario, only a
server-owned rule for `ran.mac.ul_bler > 0.15` adds RCA evidence and provenance;
the threshold is a controlled local-test signature, not a production rule.

A real loopback TCP E2E covers `RESOLVED`, verification-failed `REOPENED`,
approval `REJECTED`, and approval-expiry `FAILED`; replaying settled events
performs no additional durable writes. `telco-lab` now also provides an opt-in,
caller-owned local checkpoint store and persistent paced wrapper. The wrapper
atomically saves each plan-bound checkpoint after a valid 202/204 receipt and
before advancing; the updated real TCP E2E asserts that reopening a completed
store selects, attempts, and delivers zero events; its final local rerun passed.
The store uses strict bounded JSON, a non-blocking single-writer lock, and an
explicit local workspace/checkpoint
directory. It is still not a signed receiver acknowledgement: response-loss
recovery depends on receiver idempotency, and POSIX mount topology plus
malicious same-user filesystem swaps remain host trust boundaries. The receiver
intentionally performs no cross-event aggregation. This path does not connect
to Cloud Fault ingress, Spanner, Pub/Sub, Engineer, MCP write tools, GitOps, or
a Network Operator.

Sprint 1 closure evidence was `222 passed, 1 skipped` for Data Lab + Lab E2E
under both Pydantic 2.5.3 and 2.13.4, Assurance full `76 passed`, local-stack
`22 passed`, and Local E2E `3 passed`, including the real TCP
persistent-checkpoint restart case. A2A contracts are `33 passed` and A2A E2E
is `4 passed`.

Sprint 1 is closed on remote RC
`7cbff490ccb71befb42c7cd30204f7f88e3b2f38`: all 4
[Assurance jobs](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938),
all 3 [Data Lab jobs](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073),
and both [Local jobs](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955)
completed successfully with an exact matching `headSha`. Their Python 3.12
release jobs uploaded 14-day `VERIFIED RC` artifacts with archive/wheel
SHA-256 values, SBOMs, exact runtime inventories, content scans, and dependency
audits showing zero known vulnerabilities. The prior
[Cloud/Emulator run](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595)
belongs to the previous RC: it remains historical Emulator evidence but does
not attest this RC or Cloud Staging.

Sprint 2 remains in progress, while its S2-01 secure container baseline is
complete for remote RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946`. The
`assurance`, `init`, and `reset` services use `network_mode: none`; `probe` and
`smoke` use `network_mode: service:assurance` so they can verify the server over
the same namespace's loopback without a published port or bridge network. The
digest-pinned image runs as UID/GID `10001:10001` with a read-only root,
`cap_drop: ALL`, `no-new-privileges`, bounded resources, a hardened `/tmp`
tmpfs, one writable named workspace volume, and manifest-verified read-only
input mounts. Local policy and artifact tests passed `75 passed, 1 skipped`
(the skip is the Windows symlink condition); Black, flake8, YAML/JSON parsing,
and diff checks also passed. The remote Linux policy suite passed
`76 passed, 0 skipped`.

The matching [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755)
passed both [compose-policy job 99258612862](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258612862)
and [build-inspect-smoke job 99258640065](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258640065).
It resolved the Compose policy, built and inspected local image ID
`sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`
(an ephemeral runner build ID, not a registry digest), scanned 5
application layers / 2,570 members and 9,148 merged-rootfs members, initialized
13,440 performance rows and 579 trace rows with 0 incidents and
`external_access=false`, and passed health, live isolation, shared-loopback
smoke and probe steps; the probe step emitted no stdout. Reset removed state,
artifacts, and marker with `workspace_removed=true`, followed by successful
cleanup.

S2-02 is also **DONE** for remote RC
`d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4`. Its commit-bound
[telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750)
passed [compose-policy job 99266075811](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266075811)
and [build-inspect-smoke job 99266104885](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266104885),
including `128 passed` on Linux. The real container demonstration created two
isolated projects: the success branch ended `RESOLVED`, the intentional failed
verification branch ended `REOPENED`, and both reported
`restart_observed=true`, `exact_replay=true`, and
`real_network_side_effects=false`. The top-level `projects_removed=true`
confirms both Compose projects were removed. The run uploaded 0 artifacts.

The matching [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757)
also passed both [job 99266075954](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075954)
and [job 99266075805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075805).
Each Python variant passed Domain + Local `518 passed`, local-stack
`29 passed, 2 skipped`, and Local E2E `2 passed`. The Python 3.12 job published
[VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877)
with archive digest
`sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`;
this is Local release evidence, not a container registry artifact.

S2-03 is also **DONE** for remote RC
`68b16ea528a85b743aa8c05044948bac195ee8ec`. Its commit-bound
[telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296)
passed [compose-policy job 99281949020](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281949020)
and [build-inspect-smoke job 99281979960](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281979960).
The run published 14-day
[artifact 9734817516](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/artifacts/9734817516)
named `telco-container-release-attempt-1`, with GitHub archive digest
`sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`
and classification `VERIFIED RUNNER-LOCAL EVIDENCE`. It binds runner-local
image/config digest
`sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`,
Trivy 0.74.0 fixable Critical/High gate `0/0`, a full diagnostic with `5`
Critical + `29` High findings that are all unfixed, and a CycloneDX 1.7
container SBOM with `145` components.

S2-01, S2-02, and S2-03 are **DONE**, but Workflow B/Sprint 2/P7 remain
`IN PROGRESS` and Gate B is not passed. This workstation still has no Docker or
actionlint, so no local result is claimed for those tools. No registry image or
digest was published, and there is still no signing, attestation, provenance,
or Trivy DB OCI digest/signature capture. The uploaded runner-local artifact is
not an offline-independent re-verification bundle because the image, scanner
binary, and database are not uploaded. G2/Gate A/S3/G4, cross-event aggregation,
RCAEval, and Cloud Staging authorization remain open.

S2-04 is **BLOCKED** after four candidates were scanned with the same Trivy
0.74.0/database snapshot: none simultaneously preserves the current CPython
3.12/glibc/provenance contract and reaches complete Critical/High `0/0`.
Ignoring unfixed findings, allowlisting vulnerabilities, or discarding package
provenance is not accepted as closure. See the
[S2-04 base-image evaluation](docs/security/s2-04-base-image-evaluation.md).

* [Implementation Development Plan 2.0](docs/implementation-development-plan-2.0.md)
* [Living implementation plan](docs/unified-platform-implementation-plan.md)
* [Local Profile setup and CLI](packages/telco-local/README.md)
* [Local deployment and governance entry point](tools/local-stack/README.md)
* [Local governance security Gate](docs/security/local-governance-gate.md)
* [Local Data Lab design and operating boundary](docs/local-data-lab.md)
* [P3e Local Data Lab security Gate](docs/security/p3e-data-lab-gate.md)
* [P2a security Gate audit](docs/security/p2a-gate-audit.md)

## Network Agents

There are two categories of network agents provided:

* __Network Engineering Agents__: Agents that help to design and build complex network services.
* __Network Optimisation Agents__: Agents that listen to the network and suggest optimisations that can resolve an issue or improve network performance.

<p align=center>
<img src="drawings/agents.png"  width="500">
</p>

All agents support the Google A2A protocol, which declares each agent's capabilities and allows agents to be dynamically loaded. Adding new functionality to network lifecycle tooling as needed. 

The following agents can be found in this repository.

| Agent              | Capabilities              |
|--------------------|--------------------------|
|Supervisor Agent| Routes requests from users to the right agent to handle it|
|Engineering Agent| Decomposes network intended changes into a planned design and set of implementaion tasks |
|Operations Agent| Query the current state of the network and available network services |
|Test Agent| Run tests across the network |
|Logs Agent| Query automation and network function logs |
|Resolver Agent| Investigates incidents and attempts to auto-resolve by interacting with other agents |

These agents can interact with each other over A2A or directly with end users using natural language as described in the following sections. 

### Chat based interaction

End users interact with the supervisor agent in natural language. The supervisor agent is responsible for routing tasks to agents it knows about that can handle those tasks and report progress back to the user throughout the lifecycle of that task. 

<p align=center>
<img src="drawings/supervisoragent.png"  width="400">
</p>

Supervisor agent communicates to the specialist network agents over the A2A protocol. As seen in the figure above, some agents are implemented using ADK and some using Langgraph. This demonstrates agents can interact with each other dynamically irrespective of the agent framework used.

Each agent has access to a set of network automation and data tools through an MCP server. Allowing each agent to find information about the network to do their job and also request changes of the network. 

### Background network agents

The previous interaction pattern was human driven. In this pattern an agent is listening to the network and when it identifies a potential issue, it triggers a task to try to auto resolve the issue. 

<p align=center>
<img src="drawings/backgroundagents.png"  width="400">
</p>

When a resolution to the issue is identifed, the resolution agent interacts with other agents through the A2A protocol to make the appropriate changes to the network. In this case the engineering agent receives a request to make changes from the resolution agent. The engineering agent needs approval to make any changes, so it triggers a notification to the supervisor agent asking for approval. 

## Network Agent Architecture

The tools available to the network agents provide access to GCP network automation and topology services. Allowing agents to update the network, discover existing topology and what network services and capabilities can be deployed in the future.  

<p align=center>
<img src="drawings/architecture.png"  width="500">
</p>

The GCP services used are: 

* __Network Orchestration__: GitOps style Kubernetes orchestration of cloud infrastructure and virtual network function resources.
* __Active Topology__: Spanner Graph model of the network topology is maintained automatically by listening to all changes made in the orchestration tools. All logs, performance metrics are captured in the same topology database. Along with embeddings of all logs and configuration of the network to allow semantic search. 
* __Virtual Mobile Network__: A set of virtual radio simulators, open source 5G core and transport network functions are deployed on GCP, provide a lab environment that can demonstrate real use case scenarios. 

## Network Agent Environment

More details on the network agents, services and the environment can be found below. 

* [Network Services](docs/networkservices.md)
* [GCP Environment](docs/gcp.md)
* [CICD](docs/cicd.md)
* [Network Agents](docs/agent.md)

## Run the demo

* [Setup GCP environment](INSTALL.md)
* [Build a 5G Network demo scenario](docs/5gbuilddemo.md)
* [Closed Loop demo scenario](docs/closedloopdemo.md)

## LICENSES

The source code of this project is provided under the [Apache 2.0 license](LICENSE).
Project-owned media and data explicitly marked as such are provided under the
[CC-BY 4.0 license](http://creativecommons.org/licenses/by/4.0/). Third-party
datasets are not relicensed by this repository: each dataset remains governed
by the exact upstream license and attribution recorded in its audited catalog
entry and workspace lock. Raw third-party datasets are downloaded to a local
cache and are not included in Git or release wheels.
