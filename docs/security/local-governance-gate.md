# Local deployment and governance security Gate

> Review date: 2026-08-30
> Local simulation scope: **PASS**
> BubbleRAN replay-to-governance slice: **READY FOR REVIEW**
> Remote GitHub Actions: **PASS for RC `427fc6832bf6b115d035e5d2cb492a25ffd82395`**
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
compatibility and security matrix has passed; that result closes S1-06 but does
not itself complete the review, the Sprint, or the release-evidence work.

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
| Real TCP business E2E | PASS | One real loopback TCP test reaches `RESOLVED`, `REOPENED`, `REJECTED`, and approval-expiry `FAILED`; exact replay after all four settle adds zero Incident/Audit/Action/Verification writes. |

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
production RCA conclusion. The caller-owned continuation checkpoint is still
not a signed receiver acknowledgement and is not persisted by `telco-lab`.

## Reproduction evidence

The current focused local evidence was run with bytecode/cache writes disabled.
Only completed runs are recorded here:

| Suite | Confirmed result |
|---|---:|
| `telco-lab` full suite, Pydantic 2.5.3 | 197 passed |
| `telco-lab` full suite, Pydantic 2.13.4 | 197 passed |
| Assurance replay receiver focused suite | 22 passed |
| Assurance full suite | 50 passed |
| Combined release regression | 133 passed |
| Real loopback TCP BubbleRAN → Governance E2E | 1 passed |

The latest local `telco-lab 0.1.0` wheel is 67,653 bytes with SHA-256
`96B5D696CB769E29256C5319FF391DA5CC30F2B25D108F5730FF9F8BD467C40B`.
It remains local build evidence only. The remote RC jobs rebuilt and checked
wheel contents, installation outside the source tree, and dependency
consistency, but did not print remote wheel byte sizes/SHA-256 values or upload
downloadable artifacts. The local digest therefore is not an RC artifact
digest.

The real entry point was also exercised against a fresh disposable workspace:
`doctor` reported ready; `init` loaded schema 1.1 with 13,440 performance rows
and 579 safe trace rows; `status` reported ready; the first demo found 15
candidates and stopped at `AWAITING_APPROVAL`; the second command copied the
returned hash/revision and reached `RESOLVED`; confirmed reset removed only
`state`, `artifacts`, and the marker, then removed the empty workspace.

### Remote RC evidence

The tested release candidate is
`427fc6832bf6b115d035e5d2cb492a25ffd82395`. Every run below completed with
`success`, and each run's `headSha` is exactly that value:

* [Assurance CI run 33296728012](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728012): on both Python 3.12 and 3.13, Domain 323, Lab 197, Local 195, Lab E2E 1 with 1 skipped, Local E2E 3, Assurance 50, A2A contracts 33, and A2A E2E 4 passed; all four wheels, outside-source-tree smoke, and `pip check` passed.
* [Data Lab CI run 33296728022](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728022): Python 3.12 and 3.13 with Pydantic 2.13.4 each reported `198 passed, 1 skipped`; Python 3.12 with the declared Pydantic 2.5.3 minimum also reported `198 passed, 1 skipped`; the wheel content allowlist, outside-source-tree smoke, and `pip check` passed.
* [Local CI run 33296728032](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728032): both Python jobs reported Domain+Local `518 passed`, local-stack `19 passed, 2 skipped`, and Local-only E2E `2 passed`; the real CLI reached `RESOLVED`, performed the guarded reset, and both wheels plus `pip check` passed.
* [Cloud CI run 33296727982](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296727982): the additional Cloud/Emulator regression passed. It does not authorize or attest Cloud Staging.

This evidence update is a later documentation-only commit. It is not the tested
RC and must not replace the `headSha` recorded by those runs.

## Residual limitations

* The Local Profile remains a single-process/single-writer DuckDB deployment.
* The deterministic verification outcome is test input, not an observation from
  a real Tester/Operations service.
* `serve` exposes the existing Assurance detect/confirm/analyze interface; it
  does not expose the new governance approval/execution loop over A2A.
* The BubbleRAN receiver intentionally creates one Incident per source event;
  it does not aggregate a multi-event episode or correlate multiple resources.
* Replay continuation checkpoints are caller-owned in-memory values. The
  repository does not yet provide checkpoint persistence across process loss.
* The fixed BubbleRAN 5G SA rule proves controlled test provenance only. It is
  not a production detector threshold, multi-source RCA, or RCAEval result.
* Replay identity stability is scoped to an identical plan/replay-window start;
  changing the replay window intentionally creates a new replay identity.
* The workspace guard rejects supplied and resolved Windows network/reparse
  paths, but still treats the host filesystem and same-user ancestor directory
  integrity as an operating-system trust boundary.
* No real remediation, production rollback, Cloud identity/delivery, or
  infrastructure isolation claim follows from this Gate. The successful RC
  matrix attests only the listed repository tests, builds, smoke checks, and
  dependency checks.

Within those explicit boundaries, the local deployment and simulated
operations-governance loop is accepted.
