# Local deployment and governance security Gate

> Review date: 2026-08-31
> Local simulation scope: **PASS**
> BubbleRAN replay-to-governance slice: **PASS**
> Remote GitHub Actions: **PASS for RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38`**
> Health/checkpoint/release-evidence changes: **REMOTE TESTED; VERIFIED RC ARTIFACTS**
> Latest HTTP admission/deadline hardening: **LOCAL AND REMOTE PASS**
> Sprint 1 Governance HTTP + Loopback Replay: **DONE**
> S2-01 secure container baseline: **DONE for RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946`**
> S2-01 `telco-container`: **REMOTE DOCKER PASS**
> S2-02 container governance recovery: **DONE for RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4`**
> S2-02 container + Local CI: **REMOTE PASS**
> S2-03 container release evidence: **DONE for RC `68b16ea528a85b743aa8c05044948bac195ee8ec`**
> S2-04 compatible base-image closure: **BLOCKED**
> S4-01 local observability evidence: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S4-02 Canonical lifecycle projection evidence: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S4-03 fixed-window acceptance SLO evidence: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S4-04 Local DuckDB cold backup recovery evidence: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S4-05 Local single-process runtime trace evidence: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S7-03 Local BubbleRAN four-branch fixture defense: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> S7-04 Local RCAEval five-case answer-blind evaluation: **DONE FOR ITS NARROW RC SLICE; ASSESSED SEPARATELY**
> Workflow E / Gate E / G5: **NOT PASSED BY THIS GATE**
> Gate B overall: **NOT PASSED**
> Cloud/production authorization: **NOT APPLICABLE**

## Decision

The repository now provides a complete, auditable governance loop for the
isolated Local Profile. Bundled LTE observations can be detected, explicitly
confirmed as a Canonical Incident, analyzed, proposed for approval, approved in
a separate hash/revision-bound operation, executed only as `LOCAL_SIMULATION`,
and verified. A passed check reaches `RESOLVED`; a failed check reaches
`REOPENED`.

The second local slice now carries the exact pinned BubbleRAN replay projection
over real loopback TCP into a durable Canonical Fault receiver. Each source
event owns one deterministic 5G SA Incident. A server-owned fixed rule attaches
exact rule-version/content provenance only for the controlled
`ran.mac.ul_bler > 0.15 ratio` signature. The same two-stage governance engine
then covers success, verification failure, rejection, and approval expiry.
This slice has passed independent local review and the replacement RC-bound
remote compatibility and security matrix. S1-01 through S1-07 and the Sprint 1
Governance HTTP/Loopback Replay scope are closed. This result does not complete
P3e, the wider supply-chain release Gate, or any Cloud Gate.

The independent Gate C/D review has now passed locally. Its final HTTP
hardening adds a strict 32-connection transport cap, a one-second header
deadline, one admitted request body with zero queue and a two-second body
deadline, and one isolated business worker with zero queue and a five-second
operation deadline. Timeouts and caller cancellation do not cancel a possibly
committed repository operation; the service remains busy until it settles and
then relies on the existing idempotency contract for exact recovery. These
changes passed the same replacement RC used to close Sprint 1.

The loopback service also has bounded `healthz`, `readyz`, and `version`
endpoints, while Data Lab has an opt-in caller-owned persistent checkpoint
wrapper. The real TCP scenario asserts that a completed checkpoint restarts
with zero selected, attempted, or delivered events. These additions and the
final HTTP hardening passed one shared remote RC matrix; its Python 3.12 jobs
published `VERIFIED RC` artifacts retained for 14 days.

This Gate authorizes only the side-effect-free local demonstration. It does not
authorize or attest GCP credentials, Spanner, Pub/Sub, Cloud MCP, Engineer A2A,
GitOps, GKE, Network Operator, real network actions, Cloud Staging IAM/OIDC,
DLQ behavior, or Workload Identity.

The separate S4-01 wrapper can observe this fixed native demonstration with
bounded in-process stage events, diagnostic timing, and in-report metric/alert
evaluation. That evidence does not expand this Governance Gate: it has no
OpenTelemetry export, propagated trace, Prometheus metrics, external alert
delivery, or SLO, and does not pass Workflow E, Gate E, or G5. The 579 safe
Trace rows referenced below are Local input records, not OpenTelemetry spans.

The separate S4-02 wrapper reads the completed durable Canonical records and
projects them into eight revision groups / 14 allowlisted events per terminal
branch. It proves exact bindings, one execution attempt, exact retry, cleanup,
`read_only=true`, and `side_effects=false` while omitting domain/workspace
identifiers and hashes, paths, correlation values, and raw records. Its frozen
`distributed_trace=false` contract means this evidence is not runtime
structured logging, OpenTelemetry, Prometheus, a distributed trace, an SLO, or
external alert delivery. It does not expand this Governance Gate or authorize
Cloud/production execution.

The separate S4-03 wrapper evaluates three fresh S4-01 windows as one fixed
Local acceptance sample. Its five integer-ppm SLIs and report-internal breach
rule distinguish trustworthy `OK`/`BREACH` from untrustworthy `ERROR`, but do
not turn this Governance Gate into runtime monitoring or a reliability Gate.
The sample is not time-based availability, a latency SLO, long-term statistical
reliability, external alert delivery, automatic recovery, backup/recovery, or a
Cloud/production SLO. It does not expand this Gate or close Workflow E, Gate E,
or G5.

The separate S4-04 wrapper exercises one stopped-writer, single-process Local
DuckDB cold-backup recovery drill. It proves an exact two-file checkpointed
backup, manifest and logical-content verification, rejection of a corrupted
copy without changing a fresh database, one atomic restore, an idempotent retry,
durable lifecycle equivalence, and identity-bound cleanup. It is not an online,
encrypted/signed, off-host, cross-version, multi-replica, RPO/RTO, power-loss,
Cloud/Spanner, HA, or production recovery result. Unknown-identity or raced
residue is deliberately preserved rather than auto-deleted. This evidence is
assessed separately and does not expand the Governance Gate or close Workflow E,
Gate E, or G5.

The separate S4-05 wrapper correlates one fixed BubbleRAN replay across the real
loopback sender/receiver, durable DuckDB readback, and A2A Analyze path. It
requires six ordered events, four components, one derived header correlation,
six durable/request/result bindings, and governance actions/approvals/executions/
verifications remaining `0 -> 0`. Analyze changes only the
`assurance_a2a_tasks` transport table; the Canonical domain and nine other tables
remain unchanged, so the whole database is explicitly not claimed read-only.
Raw JSONL remains Local-only and is not uploaded. This is not OTel/Prometheus,
distributed/cross-process/multi-event correlation, MCP propagation, external
alerting, Cloud/production observability, or Gate E/G5 closure. It is assessed
separately and does not expand this Governance Gate.

The separate S7-03 wrapper exercises the four governance terminal branches from
four code-generated BubbleRAN-schema records over the real loopback path. The
fixture is explicitly not the complete upstream benchmark. It proves four
independent cases, persistent-checkpoint `4/4/4 -> 0/0/0`, two ActionRuns and
two VerificationRuns, `LOCAL_SIMULATION` with `side_effects=false`, and zero
business-record delta after four checkpoint-bypassing settled redeliveries. It
does not provide cross-event aggregation, RCAEval, production accuracy, real
remediation, Cloud deployment, a unified dashboard, or Gate E/G5 closure. It is
assessed separately and does not expand this Governance Gate.

The separate S7-04 path evaluates five pinned upstream RCAEval cases entirely
inside the Local Data Lab. It validates all 16 resources, builds only
aggregate label-free features, ranks and seals every case before answer reveal,
then validates the same batch commitment and reuses the same seals for private
truth evaluation. It does not call the governance engine, replay an RCAEval
event, perform remediation, or expand this Gate. Its perfect five-case metrics
are not a complete benchmark, production-accuracy, generalization, or causality
claim; its commitment is explicitly not externally timestamped.

## S2-01 container review

The S2-01 baseline has passed both an independent static security review and
remote Docker behavior checks on exact RC
`d0a020fb7a5d8a33cd136cd18917d21b7e067946`; S2-01 is therefore `DONE`. Gate B
overall is not passed. The local workstation has neither Docker nor actionlint,
so the local evidence remains the Python-level policy/artifact suite:
`75 passed, 1 skipped`, where the single skip is the Windows symlink condition.
Black, flake8, YAML/JSON parsing, and diff checks passed. The remote Linux
policy suite passed `76 passed, 0 skipped`.

The matching [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755)
completed [compose-policy job 99258612862](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258612862)
and [build-inspect-smoke job 99258640065](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258640065)
successfully. It resolved the Compose policy, built and inspected runner-local
image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`
(not a registry digest), scanned 5 application layers / 2,570 members and 9,148
merged-rootfs members, and initialized 13,440 performance rows and 579 trace
rows with 0 incidents and `external_access=false`. Health, live isolation,
shared-loopback smoke, and the probe step succeeded; the probe emitted no
stdout. Reset removed state, artifacts, and marker with
`workspace_removed=true`, followed by successful cleanup.

The frozen network model keeps `assurance`, `init`, and `reset` on
`network_mode: none`. Only `probe` and `smoke` join
`network_mode: service:assurance`, reaching the server through the shared
network namespace's loopback. No service publishes or exposes a port, no
default/custom bridge is created, and no service-name URL or reverse proxy is
used to bypass the direct-loopback peer contract.

The candidate also pins the Python 3.12 Debian base by digest, runs as numeric
UID/GID `10001:10001`, uses a read-only root filesystem, drops all capabilities,
enables `no-new-privileges`, limits CPU, memory, PIDs, and file descriptors, and
mounts `/tmp` as a bounded `noexec,nosuid,nodev` tmpfs. A Docker named workspace
volume is the only persistent writable mount. Approved LTE inputs are read-only
binds and are checked against an image-owned exact-file/size/SHA-256 manifest;
they are not copied into image layers.

The remote run closes the S2-01 Docker-resolution, build/inspect, content-scan,
live-isolation, shared-loopback-step, reset, and cleanup checks. It uploaded 0
artifacts and did not publish a registry image, container SBOM, signature,
attestation, or provenance. At the S2-01 boundary, the complete success/failure
governance demonstration and restart recovery were still open; S2-02 closes
those two behavioral items below. S2-01 is `DONE`.

## S2-02 container governance recovery review

S2-02 has passed remote Docker and Local CI on exact RC
`d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` and is therefore `DONE`. The
commit-bound [telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750)
completed [compose-policy job 99266075811](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266075811)
and [build-inspect-smoke job 99266104885](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266104885)
successfully; the Linux policy suite reported `128 passed`.

The real orchestrator created separate bounded Compose projects for success and
intentional verification failure. Their machine-readable terminal states were
`RESOLVED` and `REOPENED`. Both branches reported
`restart_observed=true`, `exact_replay=true`, and
`real_network_side_effects=false`, proving an Assurance restart, exact replay
of the original prepare/decide/execute requests, and no real remediation side
effect. The top-level `projects_removed=true` proves final removal of both
project volumes. The
container run uploaded 0 artifacts.

The same RC's [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757)
also completed both [job 99266075954](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075954)
and [job 99266075805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075805)
successfully. Each Python variant passed Domain + Local `518 passed`,
local-stack `29 passed, 2 skipped`, and Local E2E `2 passed`. Python 3.12
published [VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877)
with archive digest
`sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`.
This Local release artifact is not a container registry artifact.

Gate B overall remains not passed. Workflow B/S2/P7 remain `IN PROGRESS`, and
Gate B, G2, Gate A, S3, and G4 remain open because there is still no registry
image/digest, signing/attestation/provenance, or Trivy DB OCI
digest/signature capture, and the uploaded runner-local artifact cannot be
used for offline-independent re-verification.

## S2-03 container release evidence review

S2-03 has passed remote Docker evidence generation on exact RC
`68b16ea528a85b743aa8c05044948bac195ee8ec` and is therefore `DONE`. The
commit-bound [telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296)
completed [compose-policy job 99281949020](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281949020)
and [build-inspect-smoke job 99281979960](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281979960)
successfully.

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

This evidence closes the runner-local Trivy/SBOM release-manifest slice, but it
does not publish a registry image/digest, does not provide signing,
attestation, provenance, or Trivy DB OCI digest/signature capture, and does
not supply an offline-independent re-verification bundle because the image,
scanner binary, and database are not uploaded.

## S2-04 and S4-01 through S4-05 boundary updates

S2-04 is `BLOCKED`: under the same Trivy 0.74.0/frozen database snapshot, none
of four candidates simultaneously preserves the current CPython 3.12, glibc,
public-availability and provenance contract while reaching complete
Critical/High `0/0`. Ignoring unfixed findings, allowlisting vulnerabilities,
or discarding package identity is not accepted. Gate B and G2 remain open.

S4-01 is `DONE` only for the narrow local observability evidence slice on RC
`cb4a4e7191f67aa71ef980668352d55001e23142`; its evidence and alert procedures
are recorded in the
[Local observability evidence runbook](../runbooks/local-observability-demo.md).
The RC's path filters selected only Local, so this statement does not claim a
same-SHA Data Lab or Assurance run. S4/Workflow E/S7 remain `IN PROGRESS`, and
Gate E/G5, G2/G4 remain open. This section is a cross-document boundary note,
not an additional PASS row in the Governance Gate below.

S4-02 is `DONE` only for the read-only Canonical lifecycle projection slice on
RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d`. The commit-bound [Local run
33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831)
completed Python 3.12 job 99323794962 and Python 3.13 job 99323795037
successfully; each passed Domain + Local `576 passed`, local-stack `89 passed, 2
skipped`, and Local E2E `2 passed`, with `18` release-boundary tests on 3.12.
Its [VERIFIED RC artifact
9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391)
is documented and independently reproducible through the [Local lifecycle
projection runbook](../runbooks/local-lifecycle-projection.md). Assurance run
33336341877, Container run 33336341805, and Cloud run 33336341859 also passed on
the same SHA; Data Lab was not triggered. The Cloud workflow result is CI and
Emulator evidence, not Cloud Staging or production authorization.

S4-03 is `DONE` only for the fixed three-window Local acceptance SLI/SLO slice
on RC `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`. The commit-bound [Local run
33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133)
completed Python 3.12 job 99333812338 and Python 3.13 job 99333812397
successfully; only the Local workflow was triggered. Each job evaluated its own
three fresh windows and all five SLIs were `OK`, with evaluation `OK`, no
breaches, and privacy `PASS`. The 14-day [VERIFIED RC artifact
9740377450](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/artifacts/9740377450)
was 117,046 bytes with archive digest
`sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`.
Independent download confirmed exact 13-file closure; the SLO summary was 3,271
bytes / SHA-256
`ae181eaffe6da11c5dd0cdea07dcfcba3a400daaf6ed44352b1e573faa5f489b`,
and the reconstructed report was 3,136 bytes / SHA-256
`2538629be3133920e76f2de9e0fa0ff9575853095538c266efc6e544d02c5c64`.
The full independent procedure is recorded in the [Local fixed-window
acceptance SLO runbook](../runbooks/local-slo-evidence.md).

S4-04 is `DONE` only for the stopped-writer Local DuckDB cold backup/recovery
slice on RC `54551feb43be60c3b9bdd5eab076cdb7c0aba61a`. The commit-bound [Local run
33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792)
completed in 13m04s. Python 3.12 [job
99372557281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557281)
and Python 3.13 [job
99372557192](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557192)
completed successfully in 13m00s / 10m28s; their fixed drill steps took 37s /
31s. Each reported Domain + Local `576 passed`, local-stack `224 passed, 3
skipped`, and Local E2E `2 passed`. The same SHA's [Container run
33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784)
completed jobs 99372557334 and 99372587413 successfully in 2m07s. Only Local and
Container workflows were triggered for this RC.

The 14-day [VERIFIED RC artifact
9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851)
is 118,251 bytes with archive digest
`sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`.
Independent download confirmed 14 non-link regular files: 13 manifest records
plus the manifest, with exact bytes/SHA closure, manifest `PASS`, and
`failures=[]`. The fifth supplemental recovery summary is 1,951 bytes /
SHA-256 `f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`;
removing its stdout-only report envelope reconstructs the 1,804-byte persisted
report with SHA-256
`f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`.
The exact independent procedure and non-claims are recorded in the [Local cold
backup and recovery runbook](../runbooks/local-backup-restore.md).

S4-05 is `DONE` only for the single-process Local runtime trace slice on
corrective RC `2e59d7ca88cc550e315d63e80339909ef619cd2c`. The original feature
candidate `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc` is historical only: [Local
run 33362166565](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362166565)
failed while collecting trace tests that belonged to the Assurance profile.
The corrective RC migrated those tests and removed their duplicate Local
collection.

On that corrective SHA, [Assurance run
33362806092](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092)
passed jobs 99397345468/99397345590/99397345635/99397345601; [Local run
33362806180](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180)
passed jobs 99397346249/99397346041; and [Container run
33362806104](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104)
passed jobs 99397345678/99397392344. The Python 3.12 Assurance job published the
14-day [VERIFIED RC artifact
9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240),
named `telco-assurance-release-py3.12-attempt-1`, 246,678 bytes with archive
digest
`sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`.
Independent download confirmed exactly 12 non-link regular entries (11 manifest
records plus the manifest), no uploaded raw JSONL, and a reconstructed report
of 1,651 bytes / SHA-256
`5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`.
The exact event/header/write/privacy/error/non-claim contract and independent
procedure are frozen in the [Local single-process runtime trace
runbook](../runbooks/local-runtime-trace.md).

S4/Workflow E/P7/S7 therefore remain `IN PROGRESS`; Gate E/G5 and G2/G4 remain
open, and S2-04 remains `BLOCKED`. Like S4-01 through S4-04, this S4-05 note is
assessed separately and does not add a PASS row to the Governance Gate below.
This documentation update is later than and not equal to the tested RC.

## S7-03 BubbleRAN fixture defense evidence

S7-03 is `DONE` only for the fixed four-branch local defense entry point on RC
`46318cbf84b65c3060358dffb49b829479803308`. The only command is
`python tools/local-stack/run_bubbleran_defense_demo.py --offline --approve-local-simulation`.
Its four inputs are marked `CODE_GENERATED_SCHEMA_FIXTURE`; no upstream
BubbleRAN bytes are downloaded or bundled, and no complete-upstream claim is
made.

The fixed proof requires four independent Canonical cases and four source
associations. The first persistent checkpoint run reports selected/attempted/
delivered `4/4/4`; reopening the completed store reports `0/0/0`. The terminal
branches are, in order, approved-pass `RESOLVED/PASSED`, approved-fail
`REOPENED/FAILED`, `REJECTED/NOT_RUN`, and approval-expired `FAILED/NOT_RUN`.
There are exactly two ActionRuns and two VerificationRuns. Every action has type
`LOCAL_SIMULATION` and `side_effects=false`. A final checkpoint-bypassing replay
delivers all four settled events again while Incident, Audit, SourceAssociation,
and Idempotency deltas remain zero and all four Incident objects remain deeply
equal.

The exact RC's [Assurance run
33366606140](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140)
passed jobs 99408450337/99408450434/99408450435/99408450555; [Local run
33366606118](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118)
passed jobs 99408450116/99408450386; and [Container run
33366606112](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112)
passed jobs 99408450317/99408503334. The three workflows therefore passed all
8 jobs and 122 successful steps; 11 conditional matrix/release steps were
skipped as designed.

The sole S7-03 carrier is the Python 3.12 Assurance [VERIFIED RC artifact
9748618894](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/artifacts/9748618894),
named `telco-assurance-release-py3.12-attempt-1`: 248,105 bytes with archive
SHA-256
`975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`.
Independent download confirmed an exact 13-entry closure (12 payloads plus the
manifest), manifest `PASS` with `failures=[]`, and no CSV, DuckDB, JSONL, or
checkpoint. The BubbleRAN summary is 2,374 bytes / SHA-256
`161354c5715b8a46730debcf7dd37658158d1ec338b469aa24f2bb2f3ddbc855`;
removing its stdout-only report envelope reconstructs the 2,225-byte report /
SHA-256 `4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`.
Both summary and report omit the frozen forbidden identifiers, paths, source
locations, and raw records.

The [Local BubbleRAN four-branch defense
runbook](../runbooks/local-bubbleran-defense-demo.md) records the fixed 6–8
minute presentation, field checks, safe failure/cleanup behavior, independent
artifact audit, and ten non-claims. P3e-5 has gained this independent fixture
defense entry but P3e-5/P3e remain `IN PROGRESS`: RCAEval, the second path,
cross-event aggregation, and complete upstream validation remain unfinished.
S4/Workflow E/P7/S7 remain `IN PROGRESS`; Gate E/G5/G2/G4 remain open, S2-04
remains `BLOCKED`, and P6 unified UI remains `NOT STARTED`. This documentation
update is later than and not equal to the tested RC, and this section adds no
new PASS row to the Governance Gate below.

## S7-04 RCAEval answer-blind evaluation evidence

S7-04 is `DONE` only for the five-case offline evaluation slice on RC
`b8a9e958a0a3354634f87e2fbc8f76aaf60913dd`. Its only fetch-and-evaluate
command is `telco-lab --workspace .local/networkagent-rcaeval run
rcaeval-re2ob-multisource-rca --accept-license MIT`. The catalog binds dataset
revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`, 16 resources / 53,433,532
bytes, closure
`c99ced28f1cb56464820a9570ead783de753c31ad36f5d7d29de594115101fb1`, and MIT
evidence
`c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f`.

The fixed ordering validates and opens the entire closure, reads only
label-free timing, builds five aggregate-only feature sets, ranks and seals all
five while private slots remain outside the ranker, and creates a batch
commitment before loading answers. Only then does it load answers, validate the
same commitment, reuse the same seals, construct the private truth mapping, and
evaluate. The protocol reports answer-blind ranking, pre-reveal commitment,
post-reveal validation, reused ranking, five seals, and
`externally_timestamped=false`. Public output omits private sample, candidate,
reference-identifier, artifact-location, and raw-row details.

The fixed result is 5 ranked / 0 inconclusive; Accuracy@1 through @5,
Average@5, and MRR are each 1,000,000 ppm. Candidate-ownership validity is
104,838 ppm: 39 truth-owned references among 372 ranked references. The latter
is not accuracy, recall, or annotation quality, and none of the perfect metrics
extends beyond this five-case slice.

The exact RC's [Data Lab push run
33385845017](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845017),
[Assurance push run
33385845041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845041),
and [Container push run
33385844990](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385844990)
all succeeded. Explicit [Data Lab dispatch
33385881296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296)
also passed jobs 99468272496/99468272632/99468272707.

The sole carrier is the Python 3.12 Data Lab [VERIFIED RC artifact
9755569487](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/artifacts/9755569487),
named `telco-lab-release-py3.12-attempt-1`: 148,959 bytes, archive SHA-256
`8afc11102a17310c78e1295a15a758396d904c9aea964985801c0e9e30fd88f4`, created
`2026-08-31T11:13:40Z` and retained for 14 days through
`2026-09-14T11:13:40Z`. Independent download confirmed exactly ten regular
files (nine manifest records plus the manifest), 9/9 byte/digest matches, and
manifest `PASS`. The canonical 2,408-byte summary has SHA-256
`999a35e25bfa53aaf3ef7f86f7eaf4b596c17b25366ba85cf7193724a41d0b38`.
Two wheels contain 47 members and no Parquet, Arrow, Feather, IPC, ORC, CSV,
DuckDB, or JSONL. Runtime inventory is two first-party plus six runtime
packages; CycloneDX 1.4 has eight components including PyArrow 25.0.0,
`pip-audit` reports zero findings, and wheel scan is `PASS`.

The [Local RCAEval evaluation
runbook](../runbooks/local-rcaeval-evaluation.md) freezes license/download,
the single command, cache/offline revalidation, interpretation, stable failure,
safe cleanup, artifact audit, the exact answer-blind ordering, and all 17 exact
non-claims. This section is later than and not equal to the tested RC. It closes
only S7-04: P3e/S7/P7/S4/Workflow E remain `IN PROGRESS`, Gate E/G5/G2/G4
remain open, S2-04 remains `BLOCKED`, and P6 remains `NOT STARTED`.

## Reviewed flow

```text
bundled safe LTE CSV
  → DuckDB Local Profile
  → deterministic Detector
  → explicit Incident confirmation
  → TRIAGED → INVESTIGATING → RCA_COMPLETE
  → one fixed LOCAL_SIMULATION proposal
  → AWAITING_APPROVAL
  → review action_hash + expected_revision
  → separate explicit decision with actor + reason
  → REMEDIATING → side-effect-free ActionRun → VERIFYING
  ├─ PASSED → RESOLVED
  └─ FAILED → REOPENED
```

The implementation is in
[`governance.py`](../../packages/telco-local/src/telco_local/governance.py),
the deployment entry point is
[`local_stack.py`](../../tools/local-stack/local_stack.py), and the full bundled
data acceptance scenario is
[`test_local_governance.py`](../../tests/e2e/local/test_local_governance.py).

The new replay path is:

```text
pinned BubbleRAN artifact
  → label-free ReplayPlan → public ReplayWirePayload
  → monotonic paced loopback HTTP
  → durable Incident + source association → HTTP 202
  → atomically persist caller-owned checkpoint → restart selects zero events
  → fixed 5G SA RCA provenance → separate prepare/decide/execute
  ├─ PASSED → RESOLVED
  ├─ verification FAILED → REOPENED
  ├─ approval rejected → REJECTED
  └─ approved but expired → FAILED, zero ActionRun
```

## Gate results

| Control | Result | Evidence |
|---|---|---|
| Explicit local deployment boundary | PASS | `doctor`, `init`, `status`, `demo`, foreground `serve`, and `reset` share one JSON-only entry point and an explicit workspace. |
| Safe workspace ownership | PASS | Repository-contained workspaces are accepted only below `.local`; filesystem root, home, repository root/parents, symlinks, Windows junctions/reparse points, UNC/device paths, non-fixed Windows drives, and non-empty unowned directories fail closed before state is opened. A marker identifies owned state. |
| Failure-safe initialization and reset | PASS | A failed first `init` removes only entries created for that uncommitted initialization. `reset` requires `--yes`, removes marker-owned `state`/`artifacts`, and preserves extra entries at the workspace root. |
| Network exposure | PASS | The optional service is foreground-only, fixed to `127.0.0.1`, and rejects any action mode other than `disabled`. The governance action/verification gateways perform no external I/O. |
| Layered ingress and execution budgets | PASS | The direct h11 server admits at most 32 live transports and gives each initial/keep-alive request header an absolute one-second budget. Governance, Fault, and A2A share one pre-body admission slot with zero queue and a two-second body budget; Governance/Fault additionally share one isolated business worker with zero queue and a five-second operation budget. Busy, timeout, uncertain outcome, unknown local path, and wrong method use fixed non-reflecting JSON with connection-close semantics. |
| Local status surface | PASS | Direct-loopback `healthz` is dependency-free; `readyz` performs one 1-second-bounded Repository read and returns fixed 503 on failure, timeout, or an existing stuck worker; `version` returns only unsigned allowlisted package/contract versions. Every standard non-GET method uses the fixed bounded JSON 405 contract; HEAD has the standard empty body. None attests Cloud readiness. |
| Proposal policy | PASS | Read-only RCA cannot propose actions directly. Only a conclusive, evidence-backed report with affected resources can receive exactly one fixed, low-risk, reversible `LOCAL_SIMULATION` proposal. |
| Two-stage explicit approval | PASS | Preparing an Incident persists a `PENDING` record and returns the reviewed action hash/revision. Decision requires a separate call with exact hash, revision, actor, non-empty reason, and idempotency key. First confirmation and action approval cannot be combined. |
| Approval freshness and scope | PASS | The decision binds Incident, report/version, action/hash, resource scope, revision, and expiry. Default TTL is 15 minutes and maximum TTL is 24 hours. Execution re-resolves the latest durable decision using a trusted clock. |
| Fail-closed authorization | PASS | Rejection, expiry, stale revision, wrong hash, changed replay payload, changed scope/report, or invalid `ApprovalReference` creates no network action. If an already committed approval expires before execution, one bound transition moves `REMEDIATING` to `FAILED` with zero action/verification records. Disabled mode cannot approve. |
| Action confinement | PASS | `SimulatedActionGateway` accepts only the exact `LOCAL_SIMULATION` type, fixed scenario/version parameters, reversible flag, and fixed no-op rollback statement. It produces one local `ActionRun` with `side_effects=false`. |
| Verification semantics | PASS | Verification creates local `TEST_RESULT` evidence. Only `PASSED` closes the Incident; `FAILED` records the failure and moves it to `REOPENED`. |
| Idempotency and recovery | PASS | Stable per-step keys bind immutable requests, including the normalized actor and policy inputs. Exact retries are read-only, changed retries conflict, and prepare/decision/execute resume after a durable commit whose response was lost. Boolean decisions and verification outcomes require an actual boolean value. |
| Privacy-safe output | PASS | Action previews expose only action hash/type, risk, and allowlisted resource identity fields. Canonical sensitive-data validation runs before demo artifacts and output; stable CLI errors do not reflect paths or rejected values. |
| Shared replay contract | PASS | Public frozen `ReplayWirePayload` is used by sender and receiver; it recomputes payload/source/idempotency identities, enforces approved metric/unit/flag projections, and excludes labels and client-supplied rule claims. |
| Durable replay reception | PASS | `POST /local/v1/faults/replay` requires loopback Host and peer, strict `replay-v1`, matching idempotency header, and bounded strict JSON. Before 202 it performs bounded readback of current Incident immutable facts, the initial revision-0 Audit, and SourceAssociation. Missing Incident/Audit returns 503 with zero added write; changed retries conflict and exact response-loss recovery is read-only. |
| Controlled 5G provenance | PASS | Only the exact pinned BubbleRAN dataset/version/scenario and 5G SA GNB identity are admitted. `ran.mac.ul_bler > 0.15 ratio` uses the server-owned version 1.0.0 rule and content hash; the threshold is explicitly local-test-only. |
| Paced replay resilience | PASS | The serial runner uses a finite monotonic clock, scheduled offsets/rate floor, total deadline, cancellation/uncertain-sequence evidence, and opt-in finite retries only for network/timeout failures. Checkpoints advance only on durable ACK. |
| Persistent replay checkpoint | PASS | The opt-in caller-owned store exposes load/save/clear plus a persistent paced wrapper. It uses strict 4 KiB JSON, exact plan/window/event/payload binding, atomic replace, monotonic no-regression, and a non-blocking single-writer lock; corruption, cross-plan/old-window, links/path escape, UNC/device, non-fixed Windows drive, and API failure fail closed. |
| Real TCP business E2E | PASS | The one-case flow reaches `RESOLVED`, `REOPENED`, `REJECTED`, and approval-expiry `FAILED`; completed-checkpoint restart delivers zero events before the separate settled exact-replay zero-write check. |
| Containerized success/failure governance | PASS | RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` reaches `RESOLVED` and `REOPENED` in separate real Compose projects using only `LOCAL_SIMULATION`; both report `real_network_side_effects=false`. |
| Container restart, exact replay, and cleanup | PASS | Both S2-02 branches report `restart_observed=true` and `exact_replay=true`; the top-level `projects_removed=true` confirms both projects were removed after offline database verification and marker-owned reset. |

## Open-data replay boundary

[`telco_lab.replay`](../../packages/telco-lab/src/telco_lab/replay.py) is reviewed
as a supporting fault-test boundary, not as part of the LTE governance E2E. It
builds deterministic, immutable events only from a fully verified and
lock-bound `LabBundle`. It re-adapts the exact locked artifact and compares the
derived bundle with the caller-provided bundle before constructing a plan. The
adapter/version, metric/unit projection, and quality flags must be allowlisted;
ground-truth episodes and upstream predictions are not fields in a
`ReplayEvent` or its sink payload.

The replay policy requires an explicit loopback URL and matching
`disabled|simulate` action mode. It rejects Cloud, GCP, Spanner, Pub/Sub,
Kubernetes, Engineer, Operator, GitOps, Resolver, kubeconfig, and network-agent
credential configuration. Hard ceilings are 10,000
events, 1,000 events/second, 24 hours, 256 KiB per payload, 64 MiB total,
1,000 resources, concurrency 16, and speed 1,000×. Within the same plan and
replay-window start, stable source-event and idempotency identities, sequence
checkpoints, and explicit duplicate/out-of-order selections make receiver
resilience testable.

The public `ReplayWirePayload` is now the one strict sender/receiver boundary.
The package provides both immediate serial fault-injection delivery and the
opt-in `run_paced_replay()` runner. The latter aligns sends to a monotonic
schedule and rate floor, bounds sleep and in-flight work with a total deadline,
and records the last confirmed checkpoint plus any uncertain in-flight
sequence when cancelled or expired. Retry defaults to none. The only opt-in
policies are one or two fixed retries, and only network/timeout failures are
eligible; contract, privacy, payload, environment, redirect, HTTP status, and
poison failures are final.

The Assurance process supplies the durable business receiver at
`POST /local/v1/faults/replay`. It revalidates loopback Host and peer, operation
and idempotency headers, the bounded strict JSON body, all wire identities, and
the exact BubbleRAN dataset/version/scenario/5G GNB scope. One validated source
event maps to one deterministic Incident. HTTP 202 follows only after the
Repository commit and bounded readback of the current immutable Incident facts,
the initial revision-0 Audit record, and the SourceAssociation. A changed retry
conflicts, while exact replay after response loss or after governance settlement
returns the first durable receipt with zero new write; any missing readback
component fails closed with 503 and adds no write.

For the controlled success path, only a server-owned versioned rule can attach
the `ran.mac.ul_bler > 0.15 ratio` KPI violation and rule-content hash. The
receiver never trusts client rule fields and does no cross-event aggregation.
The threshold is a lab signature, not a general 5G detector threshold or a
production RCA conclusion.

The optional persistent runner saves a new canonical checkpoint after each
valid 202/204 receipt and before advancing or emitting the next event. A lost
response or local save failure retains the older checkpoint, so restart can
send the same stable idempotency key again and depends on receiver exact-replay
semantics. The checkpoint remains a caller-owned continuation claim, not a
signed receiver acknowledgement. Store operations are single-writer and fail
busy rather than wait; they are not a shared checkpoint service.

## Reproduction evidence

The Sprint 1 focused local evidence was run with bytecode/cache writes disabled.
Completed counts are recorded exactly and retained as historical evidence:

| Suite | Confirmed result |
|---|---:|
| Data Lab + Lab E2E, Pydantic 2.5.3 | 222 passed, 1 skipped |
| Data Lab + Lab E2E, Pydantic 2.13.4 | 222 passed, 1 skipped |
| Assurance full suite, Sprint 1 RC | 76 passed |
| Domain + Local + shared contracts | 520 passed |
| local-stack safety suite | 22 passed |
| Local E2E | 3 passed |
| A2A contracts | 33 passed |
| A2A E2E | 4 passed |
| Real loopback TCP BubbleRAN → Governance E2E | 1 passed |

The canonical remote wheels are `telco-lab 0.1.0` at 74,425 bytes with SHA-256
`4c646e7ad618884284bf5f0b484b579c19dbcaccc8ef01571eccfc4ea197d900`
and `telco-assurance-agent 0.1.0` at 56,893 bytes with SHA-256
`9f7d47ea0c45d2a01a60a5a726055a7368f3d2cf86d4d8a8ac1445bde08ce96d`.
The Domain, Lab, and Local wheel summaries are unchanged from the prior RC.
CycloneDX structure, runtime inventory, wheel content scans, and dependency
audits passed with zero known vulnerabilities. The verified RC artifacts are
retained for 14 days.

The real entry point was also exercised against a fresh disposable workspace:
`doctor` reported ready; `init` loaded schema 1.1 with 13,440 performance rows
and 579 safe trace rows; `status` reported ready; the first demo found 15
candidates and stopped at `AWAITING_APPROVAL`; the second command copied the
returned hash/revision and reached `RESOLVED`; confirmed reset removed only
`state`, `artifacts`, and the marker, then removed the empty workspace.

### Sprint 1 remote RC evidence

The tested release candidate is
`7cbff490ccb71befb42c7cd30204f7f88e3b2f38`. Every run below completed with
`success`, and each run's `headSha` is exactly that value:

* [Assurance CI run 33308634938](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938): all 4 jobs passed.
* [Data Lab CI run 33308635073](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073): all 3 jobs passed.
* [Local CI run 33308634955](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955): both jobs passed.

The Python 3.12 jobs published these `VERIFIED RC`, 14-day artifacts:

* [Assurance artifact 9731341117](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938/artifacts/9731341117), archive digest `sha256:30cee4d4ca7c8e7d09cdde27449a8165a5e1da3e16efa8dc0fc30c4af44d454e`; runtime inventory 34, dependency audit 0, SBOM components 38.
* [Data Lab artifact 9731281738](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073/artifacts/9731281738), archive digest `sha256:2e314321e990f38ef82696a6df78fe9f11538f6c582996004d4b66d2d11a2231`; runtime inventory 5, dependency audit 0, SBOM components 7.
* [Local artifact 9731294281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955/artifacts/9731294281), archive digest `sha256:adee5fba5887a4d61a4f59fba9a946c8d211038144095918e3045a6f56b0bee0`; runtime inventory 7, dependency audit 0, SBOM components 9.

The prior RC's [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595)
remains historical Cloud/Spanner Emulator evidence. Its `headSha` is not the
new RC above, and it does not authorize or attest Cloud Staging.

This evidence update is a later documentation-only commit. It is not the tested
RC and must not replace the `headSha` recorded by those runs. Artifact hashes
prove integrity, not publisher identity; signing/attestation, hash-locked
offline installation, SPDX, independent secret/SAST/license policy, and the
complete S3 Gate remain open.

The 76-test Assurance, 22-test local-stack, 3-test Local E2E, 33-test A2A
contract, and 4-test A2A E2E results are the independent local evidence for the
same HTTP admission/deadline implementation now covered by the remote RC.

### S2-02/S2-03 remote RC evidence

The tested S2-02 release candidate is
`d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4`. Both runs completed with
`success`, and each run's `headSha` is exactly that value:

* [Container CI run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750): jobs [99266075811](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266075811) and [99266104885](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266104885) passed; Linux policy `128 passed`; both real governance branches passed restart, exact replay, no-real-side-effect, offline verification, reset, and cleanup assertions. The run uploaded 0 artifacts.
* [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757): jobs [99266075954](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075954) and [99266075805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075805) passed. Both Python variants recorded Domain + Local `518 passed`, local-stack `29 passed, 2 skipped`, and Local E2E `2 passed`.
* [Local VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877): Python 3.12 archive digest `sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`.
* [Container CI run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296): jobs [99281949020](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281949020) and [99281979960](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281979960) passed. The run published [artifact 9734817516](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/artifacts/9734817516), classified `VERIFIED RUNNER-LOCAL EVIDENCE`, with archive digest `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`; it binds runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`, Trivy 0.74.0 fixable Critical/High gate `0/0`, full diagnostic `5` Critical + `29` High (all unfixed), and CycloneDX 1.7 SBOM `145` components.

This evidence update is later than and not equal to the tested S2-02/S2-03 RCs.
The Local artifact and runner-local container artifact do not supply a
published container registry image/digest. No run here proves signing,
attestation, provenance, or Trivy DB OCI digest/signature capture, and the
S2-03 artifact is not an offline-independent re-verification bundle because the
image, scanner binary, and database are not uploaded.

## Residual limitations

* The Local Profile remains a single-process/single-writer DuckDB deployment.
* S4-04 covers only a stopped-writer two-file Local DuckDB cold backup and
  reset/fresh-init restore. It does not provide online, encrypted/signed,
  off-host, cross-version, multi-replica, RPO/RTO, power-loss, Cloud/Spanner,
  HA, or production recovery. Identity-unknown or raced residue is preserved
  for manual review and is not automatically deleted.
* S4-05 covers one fixed event in one Local process. Its raw JSONL is not release
  evidence; it supplies no OTel/Prometheus export, distributed/cross-process or
  concurrent/multi-event correlation, MCP propagation, external alert delivery,
  sink guarantee, or Cloud/production monitoring. A2A Analyze persists
  `assurance_a2a_tasks`, so the full database is not read-only.
* S7-03 uses four code-generated schema fixture records, not the complete
  BubbleRAN upstream benchmark. It does not supply RCAEval, a second data path,
  cross-event/episode aggregation, production accuracy, real remediation,
  Cloud/GCP deployment, a unified dashboard, or P3e/S7/Gate E/G5 closure.
* S7-04 separately supplies a five-case pinned-upstream RCAEval answer-blind
  offline evaluation, but no RCAEval replay or governance integration. It is
  not the complete benchmark, full dataset coverage, upstream implementation
  parity, independent evidence annotation, production accuracy, cross-dataset
  or statistical generalization, causal identification, online evaluation,
  live remediation, an externally timestamped commitment, Cloud/OTel, a
  dashboard, or P3e/S7/Gate E/G5 closure.
* The deterministic verification outcome is test input, not an observation from
  a real Tester/Operations service.
* `serve` exposes the existing Assurance detect/confirm/analyze interface; it
  does not expose the new governance approval/execution loop over A2A.
* The BubbleRAN receiver intentionally creates one Incident per source event;
  it does not aggregate a multi-event episode or correlate multiple resources.
* Replay continuation checkpoints can be persisted locally, but remain
  caller-owned, unsigned claims. The store is non-blocking single-writer;
  response-loss recovery still depends on the receiver's idempotency contract.
* The fixed BubbleRAN 5G SA rule proves controlled test provenance only. It is
  not a production detector threshold, multi-source RCA, or RCAEval result.
* Replay identity stability is scoped to an identical plan/replay-window start;
  changing the replay window intentionally creates a new replay identity.
* Header and body deadlines are cooperative timers on the ASGI event loop.
  Governance/Fault repository work is isolated in its dedicated worker, but
  legacy A2A SDK/store calls can still block that loop and therefore are not
  claimed to have a hard wall-clock deadline.
* A2A non-blocking and streaming background work outlives the request boundary;
  the admission lease covers synchronous request dispatch, not the SDK-managed
  task lifetime.
* Windows checkpoint paths reject UNC/device namespaces before filesystem
  probes and require `DRIVE_FIXED`. POSIX mount topology and malicious
  same-user ancestor rename/swap TOCTOU remain host-filesystem trust boundaries.
* A timed-out readiness read runs in a worker that cannot be force-terminated;
  while it remains stuck, later `readyz` calls fail closed with 503 rather than
  create concurrent workers.
* No real remediation, production rollback, Cloud identity/delivery, or
  infrastructure isolation claim follows from this Gate. The successful RC
  matrix attests only the listed repository tests, builds, smoke checks, and
  dependency checks.

Within those explicit boundaries, the local deployment and simulated
operations-governance loop is accepted.
