# telco-lab

`telco-lab` provides an audited, reproducible local workspace for public telecom
datasets. Importing the package, constructing `TelcoLab`, and listing the catalog
perform no network requests. A network request occurs only after an explicit
`fetch` command and exact license-token acceptance.

For local development from the repository:

```text
python -m pip install -e packages/telco-domain
python -m pip install -e packages/telco-lab
telco-lab --workspace .local/telco-lab catalog
```

The package supports Python 3.12 and 3.13. It has no GCP, model-provider, ADK,
MCP-server, or database dependency; catalog inspection and cached evaluation do
not require credentials.

```text
telco-lab --workspace .local/telco-lab catalog
telco-lab --workspace .local/telco-lab fetch \
  bubbleran.persistent-interference.clean.v1 \
  --accept-license CC-BY-SA-4.0
telco-lab --workspace .local/telco-lab verify
```

The first complete vertical slice evaluates BubbleRAN's pinned persistent-
interference dataset. `run` explicitly fetches the three locked artifacts and
then evaluates them; `evaluate` performs the same deterministic work using only
an already verified cache:

```text
telco-lab --workspace .local/telco-lab run \
  bubbleran-persistent-interference \
  --accept-license CC-BY-SA-4.0 \
  --overlap-threshold 0.1

telco-lab --workspace .local/telco-lab evaluate \
  bubbleran-persistent-interference \
  --overlap-threshold 0.1
```

The Python API exposes the same split through
`fetch_and_evaluate_bubbleran()` and `evaluate_cached_bubbleran()`. The result
keeps detector observations, ground truth, and predictions in distinct types;
upstream UE identifiers and label columns never enter detector observations or
the JSON summary.

## Bounded local replay planning

The public Python API also exports `ReplayPolicy`, `ReplayEvent`, `ReplayPlan`,
`ReplaySink`, `build_replay_plan()`, and `validate_replay_environment()`. Given
a fully verified, workspace-lock-bound `LabBundle`, it re-adapts the exact
locked artifact and compares the derived bundle before building an immutable,
deterministic sequence of KPI events. The plan includes UTC time shifting,
source-event identities and idempotency keys stable for the same replay-window
start, checksummed payloads, rate scheduling, and resumable sequence
checkpoints. Duplicate and out-of-order delivery orders can be selected
explicitly for fault-injection tests.

`LoopbackHttpReplaySink` is the opt-in transport implementation for a process
that already has a validated plan. It accepts the endpoint only through that
plan's `ReplayPolicy`, performs a fresh policy/environment/event and DNS/IP
loopback check for every emission, and makes one direct stdlib HTTP request in
an `asyncio.to_thread()` worker. It never consults proxy configuration and
never follows redirects. Requests are canonical JSON with fixed
`Content-Type`, `Idempotency-Key`, and
`X-NetworkAgent-Local-Operation: replay-v1` headers. Request bytes, response
headers/body, and the 1–30 second timeout are hard bounded; only HTTP 202 and
204 are accepted.

`deliver_replay_plan()` sends a finite selection immediately and strictly one
request at a time. With no selection it resumes after a plan-bound continuation
checkpoint in canonical plan order; the API does not accept a bare sequence
number. A nonzero checkpoint must exactly match the current `plan_id`, sequence,
and that sequence's `source_event_id` and `payload_sha256`; sequence zero uses
`None` for both event fields. An explicit bounded sequence may include duplicates
or out-of-order events for fault tests. Each selected occurrence is attempted at
most once. The first delivery failure returns its fixed error code and highest
contiguous checkpoint without retrying; a caller must explicitly persist and
resume it, either through the public checkpoint functions below or through a
separately reviewed store.

`ReplayPlan.plan_id` binds the complete policy (including its loopback endpoint),
replay window, and event sequence, so checkpoint validation detects plan,
endpoint, replay-window, and event drift before transport is called. A checkpoint
is still only a caller-owned continuation claim. It is not a signed or otherwise
authenticated proof that the receiver acknowledged an event.

This helper does **not** sleep until `scheduled_offset_seconds` and therefore
does not enforce `speed` or `max_rate_per_second` as wall-clock send throttles.
Use it only for immediate local delivery and deterministic fault-injection
tests.

`run_paced_replay()` is the opt-in wall-clock runner for the canonical suffix of
the same plan-bound checkpoint. It remains serial and aligns the next event to
its scheduled delta from the confirmed checkpoint, while enforcing a minimum
attempt interval of `1 / max_rate_per_second`; a delayed retry therefore cannot
cause a catch-up burst. A total deadline bounds both sleeps and in-flight emits.
The injectable `ReplayPacingClock` permits deterministic tests, while every
clock read must remain finite and monotonic and every sleep is still protected
by the real asyncio deadline.

Automatic retry defaults to `ReplayRetryPolicy.NONE`. The only opt-in choices
are the frozen `TRANSIENT_ONCE` and `TRANSIENT_TWICE` strategies, with fixed
bounded backoffs; they retry only network and timeout errors. Contract,
environment, event, payload, response, redirect, status and poison failures are
never retried. Deadline and cancellation evidence never advances the checkpoint
for an unconfirmed event. An in-flight deadline/cancellation reports its sequence
as uncertain, and explicit recovery reuses the event's stable idempotency key.
`run_paced_replay()` itself remains non-persistent and never interprets a
checkpoint as an authenticated receiver acknowledgement.

## Persistent caller-owned checkpoints

The opt-in local store exposes `load_replay_checkpoint()`,
`save_replay_checkpoint()`, and `clear_replay_checkpoint()`. Every call requires
both an explicit, already existing workspace and an explicit checkpoint
directory strictly below that workspace. The directory is created when safe,
but a path escape, `..`, symlink, Windows junction/reparse point, non-directory,
or non-regular checkpoint/lock file fails closed. One checkpoint directory owns
at most one active plan.

On Windows, UNC forms (`\\server\share` and `//server/share`) and device
namespaces (`\\?\` and `\\.\`) are rejected from the input text before any
filesystem path probe. After normalization, `GetDriveTypeW` must report
`DRIVE_FIXED`; an API failure, unknown drive type, or any non-fixed drive also
fails closed. These checks assume a trusted local filesystem. POSIX mount
topology is not classified, and a malicious same-user process that can rename
or swap an ancestor between validation and use remains outside this local-store
trust boundary; the store is not a hostile same-user filesystem sandbox.

The checkpoint file uses a strict, duplicate-key-free JSON schema with a 4 KiB
limit. It contains only the schema version and the exact
`ReplayDeliveryCheckpoint` fields. Loading revalidates all four continuation
fields against the supplied plan, so endpoint, replay-window, event sequence,
source-event, or payload drift is rejected. Saving is monotonic: an exact save
is idempotent and a lower sequence is rejected. Writes use a same-directory
temporary file, file flush, `fsync`, and atomic replace. A non-blocking
cross-process file lock makes overlapping load/save/clear/run operations fail
with `replay_checkpoint_busy` instead of waiting or becoming multi-writer.

`run_persistent_paced_replay()` loads that store and reuses the same bounded
pacing, retry, deadline, and cancellation core as `run_paced_replay()`. After a
valid 202/204 receipt, it atomically saves the new canonical checkpoint before
the in-memory runner advances or emits the next event. A deadline or cancellation
therefore leaves the last confirmed checkpoint restartable. If the receiver
committed but its response was lost, or the local checkpoint write failed, the
older checkpoint is intentionally retained; the next run sends the same event
with the same idempotency key and depends on receiver-side exact replay.

```python
import asyncio
from pathlib import Path

from telco_lab import (
    LoopbackHttpReplaySink,
    clear_replay_checkpoint,
    run_persistent_paced_replay,
)

workspace = Path(".local/telco-lab")
checkpoint_directory = workspace / "replay-checkpoints"
sink = LoopbackHttpReplaySink(
    plan.policy,
    environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": plan.policy.action_mode},
)
result = asyncio.run(
    run_persistent_paced_replay(
        plan,
        sink,
        workspace=workspace,
        checkpoint_directory=checkpoint_directory,
    )
)

# Clear is plan-bound and refuses to delete corrupt or cross-plan state.
clear_replay_checkpoint(
    plan,
    workspace=workspace,
    checkpoint_directory=checkpoint_directory,
)
```

Persistence does not change the trust model. The file remains a sender/caller-
owned continuation claim, not a signed or authenticated receiver ACK. It does
not prove exactly-once delivery by itself, does not permit continuation across
a changed plan/window, and does not add Cloud delivery or multi-writer support.

```python
import asyncio

from telco_lab import LoopbackHttpReplaySink, deliver_replay_plan

sink = LoopbackHttpReplaySink(
    plan.policy,
    environ={"RUNTIME_PROFILE": "local", "ACTION_MODE": plan.policy.action_mode},
)
result = asyncio.run(
    deliver_replay_plan(
        plan,
        sink,
        checkpoint=previous_checkpoint,
    )
)
```

The package remains a sender-side library: importing it, building a plan, and
listing the catalog perform no replay network I/O; only an explicit `emit()`,
`deliver_replay_plan()`, or `run_paced_replay()` call does. In this repository,
the Assurance process implements the matching durable receiver at
`POST /local/v1/faults/replay`. That route is loopback-only and accepts the
shared public wire contract below. It does not reuse the Cloud Fault ingress or
connect to Pub/Sub, Spanner, Engineer, MCP write tools, Resolver dispatch, or a
Network Operator.

The public `ReplayWirePayload` is the strict versioned sender/receiver boundary.
`replay_wire_payload_from_event()` produces it from a revalidated event, and
`validate_replay_wire_payload()` accepts one already JSON-decoded object and
recomputes the payload, source-event and idempotency identities plus the approved
metric/unit/flag and privacy guards. Its `request_fingerprint_sha256` property is
the digest of the complete canonical body and is not serialized as a wire field.
`ReplayEvent.sink_payload()` delegates to this model, so the canonical HTTP body
remains byte-compatible with the earlier safe projection.

## Local Canonical receiver and governance E2E

The repository receiver acknowledges HTTP 202 only after
`create_or_correlate()` has durably committed and bounded readback verifies the
current Incident's immutable facts, the initial revision-0 Audit, and the
immutable SourceAssociation. Missing current Incident or initial Audit returns
503 without adding a business write. Each source event maps to one
deterministic Canonical Incident; cross-event aggregation is intentionally not
implemented. An exact retry returns the original durable receipt even if the
Incident has since reached a settled governance state, and creates no new
Incident, audit, action, or verification record.

The current receiver admits only the exact pinned BubbleRAN dataset/version,
the `bubbleran-persistent-interference` scenario, `5G_SA`, and an approved GNB
resource identity. A server-owned fixed rule adds a KPI violation and exact
rule-content provenance only when `ran.mac.ul_bler > 0.15` with unit `ratio`.
This threshold recognizes a controlled local replay signature and must not be
presented as a production-network diagnosis.

One real loopback TCP acceptance test drives four source events through replay,
durable reception, RCA, two-stage approval, and side-effect-free local
simulation. It verifies `RESOLVED`, verification-failed `REOPENED`, approval
`REJECTED`, approval-expiry `FAILED`, label non-disclosure, and zero durable
writes on an exact replay after settlement. The optional local checkpoint store
adds restartable sender-side continuation, but remains unauthenticated and
single-writer. There is still no cross-event Incident aggregation, real
remediation, Cloud delivery, or RCAEval vertical slice.

Replay construction fails closed unless all of the following remain true:

* the workspace verifies and the bundle is bound to exactly one locked artifact;
* the adapter/version, scalar metric/unit projection, and quality flags are
  explicitly approved;
* ground-truth episodes and upstream prediction labels are absent from events;
* the endpoint is an explicit loopback URL and action mode is `disabled` or
  `simulate`;
* Cloud, Spanner, Pub/Sub, Kubernetes, Engineer, Operator, GitOps, Resolver,
  kubeconfig, or network-agent credential configuration is not present; and
* event, rate, duration, payload, aggregate payload, resource, concurrency, and
  speed budgets remain within hard limits.

Current hard ceilings are 10,000 events, 1,000 events/second, 24 hours,
256 KiB per event, 64 MiB total payload, 1,000 resources, concurrency 16, and
speed 1,000×. Callers should normally choose lower scenario-specific budgets.

Exit status `0` means the command succeeded (and `verify` is valid), `1` means
verification completed but found an invalid or missing artifact, and `2` means
the request was safely rejected. Standard output and standard error are JSON.
They never include a local path or a source URL/query.

Every downloaded resource is limited by a global fixed byte ceiling and its
catalog-pinned exact size. It is streamed through SHA-256 into a temporary file,
then atomically committed. Cache hits are re-read and verified. The workspace
lock records a stable lock identity, dataset/catalog versions, artifact and
catalog fingerprints, source-URL fingerprints, allowed hosts, adapters,
license evidence digests, attribution, and the license review date.

The package and wheels contain no third-party dataset bytes. BubbleRAN's data
is attributed to the
[BubbleRAN Open Telco Datasets project](https://github.com/bubbleran/open-telco-datasets)
and remains under its upstream
[CC BY-SA 4.0 license](https://creativecommons.org/licenses/by-sa/4.0/).
The catalog pins commit `fa4e3333855d64474e710bc5bebf11a9ec075e0b`, exact
artifact sizes, and SHA-256 digests. Company distribution or commercial use
must still receive the appropriate internal license/compliance review.
The immutable evidence details are recorded in
[`THIRD_PARTY_DATA.md`](THIRD_PARTY_DATA.md).

On the pinned full artifacts, the reference evaluation at temporal IoU `0.1`
loads 1,597 anomalous observations with 5 ground-truth episodes, 3,601 clean
observations with no episode, and 55 upstream predicted episodes. It produces
TP=5, FP=50, FN=0. These figures are a reproducibility baseline, not a claim
that the upstream detector has production-grade precision.
