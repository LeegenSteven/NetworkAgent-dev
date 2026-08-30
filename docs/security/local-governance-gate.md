# Local deployment and governance security Gate

> Review date: 2026-08-30
> Local simulation scope: **PASS**
> BubbleRAN replay-to-governance slice: **READY FOR REVIEW**
> Remote GitHub Actions: **PASS for RC `6ba631929c312bbff27ef0ad4a9136d2cb390ae1`**
> Health/checkpoint/release-evidence changes: **REMOTE TESTED; VERIFIED RC ARTIFACTS**
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
This slice remains ready for independent review. Its RC-bound remote
compatibility and security matrix has passed. S1-06 and the evidence-specific
S1-07 work are closed, but that result does not itself complete the independent
review, the Sprint, P3e, or the wider supply-chain release Gate.

After that RC, the loopback service gained bounded `healthz`, `readyz`, and
`version` endpoints, while Data Lab gained an opt-in caller-owned persistent
checkpoint wrapper. The real TCP scenario now asserts that a completed
checkpoint restarts with zero selected, attempted, or delivered events. The
supporting additions passed the new remote RC matrix and its Python 3.12 jobs
published independently rechecked `VERIFIED RC` artifacts.

This Gate authorizes only the side-effect-free local demonstration. It does not
authorize or attest GCP credentials, Spanner, Pub/Sub, Cloud MCP, Engineer A2A,
GitOps, GKE, Network Operator, real network actions, Cloud Staging IAM/OIDC,
DLQ behavior, or Workload Identity.

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

The current focused local evidence was run with bytecode/cache writes disabled.
Completed counts are recorded exactly:

| Suite | Confirmed result |
|---|---:|
| Data Lab + Lab E2E, Pydantic 2.5.3 | 222 passed, 1 skipped |
| Data Lab + Lab E2E, Pydantic 2.13.4 | 222 passed, 1 skipped |
| Assurance full suite | 54 passed |
| Domain + Local + shared contracts | 520 passed |
| Assurance status focused suite | 4 passed |
| Local E2E | 3 passed |
| A2A contracts | 33 passed |
| A2A E2E | 4 passed |
| Real loopback TCP BubbleRAN → Governance E2E | 1 passed |

The canonical remote `telco-lab 0.1.0` wheel is 74,425 bytes with SHA-256
`4c646e7ad618884284bf5f0b484b579c19dbcaccc8ef01571eccfc4ea197d900`.
The downloaded remote artifacts were rehashed locally: every wheel and evidence
file matched its manifest. CycloneDX 1.4 structure, runtime inventory, wheel
content scans, and `pip-audit==2.10.1` all passed with zero known
vulnerabilities. Artifacts are retained for 14 days and expire on 2026-09-13.

The real entry point was also exercised against a fresh disposable workspace:
`doctor` reported ready; `init` loaded schema 1.1 with 13,440 performance rows
and 579 safe trace rows; `status` reported ready; the first demo found 15
candidates and stopped at `AWAITING_APPROVAL`; the second command copied the
returned hash/revision and reached `RESOLVED`; confirmed reset removed only
`state`, `artifacts`, and the marker, then removed the empty workspace.

### Remote RC evidence

The tested release candidate is
`6ba631929c312bbff27ef0ad4a9136d2cb390ae1`. Every run below completed with
`success`, and each run's `headSha` is exactly that value:

* [Assurance CI run 33301104511](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104511): both Python jobs passed Domain 323, Lab 219 with 2 skipped, Local 195, Lab E2E 1 with 1 skipped, Local E2E 3, Assurance 54, A2A contracts 33, and A2A E2E 4; Supervisor 57 and the legacy wire fixture also passed.
* [Data Lab CI run 33301104518](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104518): Python 3.12/3.13 current-boundary jobs and the Python 3.12 Pydantic 2.5.3 minimum job each reported `220 passed, 3 skipped`.
* [Local CI run 33301104520](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104520): both Python jobs reported Domain+Local `518 passed`, local-stack `19 passed, 2 skipped`, and Local-only E2E `2 passed`; the real CLI, outside-source-tree wheel smoke, and `pip check` passed.
* [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595): Cloud profile and real Spanner Emulator contracts passed on Python 3.12/3.13. It does not authorize or attest Cloud Staging.

The Python 3.12 jobs published these 14-day artifacts:

* [Data Lab artifact 9728965310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104518/artifacts/9728965310), manifest SHA-256 `9c2f4e2c5a9d35cb900a94b64f3ef6b2f604ceb34910a4638f926791f0b9d63d`.
* [Local artifact 9728983176](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104520/artifacts/9728983176), manifest SHA-256 `337578af2a1a37d6bed33f4d8314439b13ceb80a58d3d515cfa8fbb67dec45cb`.
* [Assurance artifact 9729018617](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104511/artifacts/9729018617), manifest SHA-256 `84d7978fb585202a5641850b0af0604b132540fe3e9c38f491fe194f91816d73`.

This evidence update is a later documentation-only commit. It is not the tested
RC and must not replace the `headSha` recorded by those runs. Artifact hashes
prove integrity, not publisher identity; signing/attestation, hash-locked
offline installation, SPDX, independent secret/SAST/license policy, and the
complete S3 Gate remain open.

## Residual limitations

* The Local Profile remains a single-process/single-writer DuckDB deployment.
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
