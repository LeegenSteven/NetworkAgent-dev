# Local single-process runtime trace runbook

> Work package: S4-05 bounded Local runtime correlation evidence
> Status: `DONE (S4-05 narrow slice)`
> Scope: one fixed BubbleRAN event over real loopback TCP in one process

## 1. Purpose and boundary

This runbook reproduces and independently verifies the S4-05 path:

```text
Replay sender
  -> loopback HTTP receiver
  -> durable DuckDB readback
  -> sender acknowledgement
  -> A2A Analyze request
  -> deterministic RCA result
```

The event schema is `networkagent-local-runtime-trace-event/1.0`; the bounded
evidence schema is `networkagent-local-runtime-trace-evidence/1.0`, classified
as `LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE`. The tested release candidate
is `2e59d7ca88cc550e315d63e80339909ef619cd2c`.

This is a single-process Local correlation proof. It is not OpenTelemetry,
Prometheus, a distributed or cross-process trace, concurrent/multi-event
correlation, MCP propagation, external alert delivery, Cloud observability, or
production monitoring. It does not close S4, Workflow E, Gate E, or G5.

## 2. The only command

Run from the repository root with the Local Profile and Assurance dependencies
installed under Python 3.12 or 3.13:

```text
python tools/local-stack/run_runtime_trace_demo.py --approve-local-simulation
```

The explicit Local simulation confirmation is the only accepted argument. The
wrapper fixes the input to one safe BubbleRAN UL-BLER event, binds a real TCP
listener to an ephemeral `127.0.0.1` port, disables action execution, and uses
the real Replay, receiver, DuckDB repository, and A2A Analyze implementations.
It accepts no caller path, URL, event, threshold, trace identifier, action mode,
Cloud setting, or arbitrary command.

Success writes one bounded JSON summary to stdout and nothing to stderr.
Failure writes no success summary and returns one stable JSON error on stderr.
Do not merge stderr into a prospective release summary.

## 3. Header derivation and admission

The sender derives the correlation value only after validating a source event
identifier. The frozen derivation is:

```text
trace_id = "local-replay-trace-" +
           sha256(b"telco-assurance:local-replay-trace:v1\0" +
                  source_event_id.encode("ascii")).hexdigest()
```

It sends the value in `X-NetworkAgent-Trace-Id`. The receiver accepts at most
one printable-ASCII value of at most 256 bytes and independently derives the
expected value from the validated replay payload. A duplicate, malformed, or
mismatched value returns HTTP 409 with `LOCAL_FAULT_TRACE_CONFLICT` before the
business repository write; rejected values are not echoed into trace output.

The header remains optional for older callers. An absent header preserves the
existing replay behavior but emits no receiver/repository propagated events, so
such a request cannot satisfy the six-event S4-05 contract.

## 4. Exact event contract and order

Every JSONL line has exactly these seven fields:

```text
schema
emitted_at
trace_id
component
operation
outcome
error_code
```

`emitted_at` is UTC with microseconds and `Z`. The fixed successful run requires
six `OK` events, four distinct components, one correlation value, null
`error_code`, and this exact order:

| # | Component | Operation |
|---:|---|---|
| 1 | `sender` | `REPLAY_REQUEST_VALIDATED` |
| 2 | `repository` | `INCIDENT_DURABLE_READBACK` |
| 3 | `receiver` | `REPLAY_RESPONSE_ACCEPTED` |
| 4 | `sender` | `REPLAY_DELIVERY_ACKNOWLEDGED` |
| 5 | `a2a` | `ANALYZE_REQUEST_VALIDATED` |
| 6 | `a2a` | `ANALYZE_COMPLETED` |

Runtime emission is best effort and must never change business success. The
successful evidence wrapper is stricter: a missing, extra, reordered, invalid,
or failed event makes the evidence untrustworthy and returns
`trace_contract_failed`.

## 5. Six bindings and write semantics

The proof requires all six bindings:

1. The loopback receiver observes the exact derived header.
2. Bounded readback finds the durable current Incident with the immutable replay
   facts.
3. The initial revision-0 audit record is durable and exact.
4. The source-event association is durable and exact.
5. The A2A Analyze request carries the same correlation value.
6. The RCA result and report bind back to that request and durable Incident.

The database snapshot is taken after replay durability and before A2A Analyze,
then repeated after Analyze. Exactly `assurance_a2a_tasks` changes because the
A2A transport handler persists task state. These nine tables remain unchanged:

```text
assurance_pending_confirmations
assurance_schema_metadata
canonical_incident_audit
canonical_incident_idempotency
canonical_incident_source_events
canonical_incidents
cell_traces
local_schema_metadata
performance
```

Accordingly, `canonical_domain_unchanged=true`, but
`whole_database_read_only_claimed=false`. It is accurate to call the domain RCA
operation read-only; it is inaccurate to call the entire A2A request database
read-only. Governance actions, approvals, executions, and verifications are all
`0 -> 0`.

## 6. Evidence fields and acceptance checks

The persisted `local-runtime-trace-report.json` is the evidence body. Success
stdout adds only a `report` envelope containing its fixed filename, byte count,
and lowercase SHA-256. The body contains:

```text
schema=networkagent-local-runtime-trace-evidence/1.0
classification=LOCAL_SINGLE_PROCESS_LOOPBACK_TRACE_EVIDENCE
ok=true
scope.action_mode=DISABLED
scope.analyze_semantics=TRANSPORT_WRITE_DOMAIN_UNCHANGED
scope.execution=SINGLE_PROCESS
scope.network=REAL_LOOPBACK_TCP
scope.scenario=FIXED_BUBBLERAN_SINGLE_EVENT
proof.event_count=6
proof.component_count=4
proof.binding_checks=6
proof.single_correlation=true
proof.expected_order=true
proof.all_outcomes_ok=true
proof.successful_run_cleanup=true
proof.write_semantics.changed_table_count=1
proof.write_semantics.unchanged_table_count=9
proof.write_semantics.transport_state_changed=true
proof.write_semantics.canonical_domain_unchanged=true
proof.write_semantics.whole_database_read_only_claimed=false
```

`proof.governance_zero_delta` must contain zero for `actions`, `approvals`,
`executions`, and `verifications`. A release-eligible run additionally requires
`source.binding_stable=true`, `source.tracked_clean=true`,
`source.commit_bound=true`, and `release.source_state=COMMIT_BOUND`. A stable
dirty tracked tree is reported honestly as `WORKTREE_ONLY` with
`release.eligible=false`; it is not verified RC evidence.

## 7. Local raw events, privacy, and cleanup

The wrapper creates one identity-bound run directory beneath:

```text
.local/networkagent-runtime-trace/YYYYMMDDTHHMMSSZ-12hex/
```

A successful run retains exactly the report and the raw
`local-runtime-events.jsonl`; its temporary scenario workspace is removed. Raw
JSONL is local diagnostic material and contains the correlation value. It must
not be uploaded, copied into `release-evidence`, attached to a submission, or
treated as a durable delivery guarantee.

The release summary and persisted report omit raw events and payloads, absolute
paths, domain identifiers, source-event/Incident/resource/task/context/workflow
identifiers, idempotency keys, metrics, and the correlation value. The privacy
record must be exactly `status=PASS` with these flags false:

```text
absolute_paths_recorded
domain_identifiers_recorded
raw_events_in_release_summary
raw_payloads_recorded
```

The raw event model is limited to 1,024 bytes, each collector line to 4 KiB, the
whole JSONL stream to 64 KiB, and the report to 64 KiB. Files and directories
are checked by captured identity; links, hardlinks, replacements, and identity
drift fail closed. Unknown or raced residue is preserved for manual inspection
and is never automatically removed. Do not recursively delete `.local` or a
guessed run directory after failure.

## 8. Stable failure codes

The wrapper exposes only these stable command codes:

| Code | Meaning and response |
|---|---|
| `confirmation_required` | Re-run only with the exact explicit Local confirmation. |
| `invalid_arguments` | Remove every argument except the confirmation flag. |
| `trace_contract_failed` | Preserve the run directory; one or more event, binding, privacy, source, database, or identity checks failed. |
| `report_write_failed` | Preserve any identity-unknown residue; do not claim a report was published. |
| `cleanup_failed` | Evidence is not successful because the owned temporary workspace could not be removed safely. |
| `command_failed` | An unclassified bounded scenario failure occurred; preserve the stable error and inspect locally. |

The reusable trace event model rejects malformed input with
`local_runtime_trace_event_invalid` or
`local_runtime_trace_source_event_invalid`. If an implementation emits an
`ERROR` event, its allowlisted `error_code` is one of
`LOCAL_FAULT_TRACE_CONFLICT`, `REPLAY_DELIVERY_REJECTED`, or
`ASSURANCE_ANALYZE_FAILED`. The fixed accepted scenario requires no error event.

## 9. Delivered coverage and explicit non-claims

The five delivered values are exactly:

```text
FIXED_SINGLE_EVENT_REAL_LOOPBACK
SIX_STAGE_RUNTIME_CORRELATION
DURABLE_TO_A2A_RCA_BINDING
ANALYZE_WRITE_SEMANTICS
SUCCESSFUL_RUN_EPHEMERAL_STATE_CLEANUP
```

The ten `not_claimed` values are exactly:

```text
DISTRIBUTED_OR_CROSS_PROCESS_CORRELATION
OPEN_TELEMETRY_EXPORT
MCP_PROPAGATION
MULTI_EVENT_OR_CONCURRENT_CORRELATION
SINK_DELIVERY_GUARANTEE
FULL_DATABASE_READ_ONLY_ANALYZE
RAW_EVENT_ARTIFACT_UPLOAD
PRODUCTION_OR_CLOUD_OBSERVABILITY
GATE_E_OR_G5_CLOSURE
IDENTITY_UNKNOWN_OR_RACED_RESIDUE_AUTO_CLEANUP
```

These values also exclude Prometheus, external alert delivery, Cloud/production
operation, and any claim that the transport-level A2A task write does not occur.

## 10. Corrective RC history and remote evidence

The first feature candidate `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc`
is not a successful RC. Its [Local run
33362166565](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362166565)
failed while collecting the misplaced Assurance-owned trace tests. Corrective
commit `2e59d7ca88cc550e315d63e80339909ef619cd2c` moved those tests into the
Assurance profile and removed their duplicate Local collection. Only the
corrective commit is the tested S4-05 RC.

All of these corrective-RC jobs succeeded:

- [Assurance run
  33362806092](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092):
  [supervisor-adk-smoke 99397345468](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345468),
  [legacy-wire-fixture 99397345590](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345590),
  [Python 3.12 release gate 99397345635](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345635),
  and [Python 3.13 release gate 99397345601](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345601).
- [Local run
  33362806180](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180):
  [Python 3.12 job 99397346249](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180/job/99397346249)
  and [Python 3.13 job 99397346041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180/job/99397346041).
- [Container run
  33362806104](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104):
  [compose-policy 99397345678](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104/job/99397345678)
  and [build-inspect-smoke 99397392344](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104/job/99397392344).

The Python 3.12 Assurance job published the 14-day [VERIFIED RC artifact
9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240),
named `telco-assurance-release-py3.12-attempt-1`: 246,678 bytes, created
`2026-08-31T06:10:45Z`, expiring `2026-09-14T06:10:44Z`, with archive digest
`sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`.
The Local artifact is not the S4-05 supplemental-evidence carrier.

## 11. Independent artifact verification

Use a new audit directory and independently perform all of these checks:

1. Confirm the three runs' `headSha` equals the full corrective RC and every
   job listed above has conclusion `success`.
2. Query artifact 9747354240 metadata; match its name, byte count, run binding,
   retention window, and archive digest before extracting it.
3. Reject traversal, links, hardlinks, special files, duplicate names, or any
   member outside the bounded extraction directory.
4. Require exactly 12 non-link regular entries: 11 records listed by
   `release-evidence/release-manifest.json`, plus that manifest itself. Reject
   every extra, missing, size-drifted, or SHA-drifted entry.
5. Verify `release-evidence/local-runtime-trace-summary.json` against the
   schemas, source binding, scope, six-event/four-component/six-binding proof,
   write semantics, privacy, delivered values, and ten non-claims above.
6. Reconstruct the persisted report by removing the summary's top-level
   `report` member, serializing sorted UTF-8 JSON with separators `,` and `:`,
   and appending one LF. The result must be exactly 1,651 bytes with SHA-256
   `5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`.
7. Confirm the artifact contains no `.jsonl` raw event stream and no DuckDB
   database. The local raw stream is deliberately outside release evidence.

This evidence permits only `DONE (S4-05 narrow slice)`. S4, Workflow E, P7, and
S7 remain `IN PROGRESS`; Gate E, G5, G2, and G4 remain open; S2-04 remains
`BLOCKED`. This documentation update is later than and not equal to the tested
RC, so it does not redefine the source attested by the remote runs and artifact.
