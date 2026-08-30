# Local fixed-window acceptance SLO evidence runbook

> Work package: S4-03 fixed three-window Local acceptance SLO evidence
> Status: `IN PROGRESS (implemented, awaiting remote RC)`
> Scope: Local-only acceptance evidence with no Docker, Cloud credentials, external
> network action, external alert delivery, or production reliability claim

## 1. Purpose and boundary

This runbook explains how to execute and independently verify the fixed S4-03 Local
acceptance window. It evaluates three fresh S4-01 observability runs as one bounded
acceptance sample. It does not convert a single demonstration into time-based
availability, a latency SLO, long-term statistical reliability, or a Cloud/production
SLO.

The implementation is complete locally, but the work package remains
`IN PROGRESS (implemented, awaiting remote RC)`. Do not mark S4-03 as `DONE`, assign an
RC SHA, or claim a verified artifact until both remote Python jobs and the Python 3.12
release artifact have been independently closed.

## 2. Prerequisites and the only command

Run from the repository root with the project dependencies installed under Python
3.12 or 3.13. No Docker daemon, GCP credential, `gcloud` login, model API, or external
network access is required.

```text
python tools/local-stack/run_slo_evidence_demo.py --approve-local-simulation
```

This is the only accepted command. The only accepted argument is the explicit Local
simulation approval shown above. Window counts, thresholds, workspaces, paths, URLs,
Cloud options, and arbitrary commands are not configurable inputs.

Each invocation runs three S4-01 windows sequentially. Every window must use a distinct
marker-owned run directory, execute the real success and deliberate-failure branches,
and attempt both workspace cleanups. The deliberate `REOPENED/FAILED` branch is an
expected business outcome, not a command failure.

In CI, Python 3.12 and Python 3.13 each invoke this wrapper once. Therefore each Python
job evaluates one fresh three-window group; windows from different jobs or invocations
must never be combined.

## 3. Frozen schema and scope

The public schema is `networkagent-local-slo-evidence/1.0`. The scope is frozen as:

```text
execution_mode=SEQUENTIAL
window_type=FIXED_THREE_ISOLATED_RUN_ACCEPTANCE_WINDOW
window_count=3
isolated_run_directories=true
latency_slo=false
statistical_reliability_claim=false
production_or_cloud_slo=false
```

Every normalized window contains only these low-cardinality fields:

- `sequence`, strictly 1, 2, then 3;
- `duration_ms`, a non-negative integer used only for diagnostics;
- `stage_command_successes`, from 0 through 22;
- `expected_branch_outcomes`, from 0 through 2;
- `exact_retry_integrities`, from 0 through 2;
- `workspace_cleanups`, from 0 through 2;
- `local_alerts_ok`, from 0 through 4;
- `observation_contract_valid`, a strict boolean.

Across three complete windows there are 66 expected stage events. Timing values never
enter an SLI calculation and must not be interpreted as a latency objective.

## 4. Five integer-ppm SLIs

All five SLIs use integer arithmetic only:

```text
observed_ppm = numerator * 1_000_000 // denominator
objective_ppm = 1_000_000
error_budget_ppm = 0
```

| SLI | Numerator | Denominator |
|---|---|---:|
| `LOCAL_STAGE_COMMAND_SUCCESS` | sum of `stage_command_successes` | 66 |
| `LOCAL_EXPECTED_BRANCH_OUTCOME` | sum of `expected_branch_outcomes` | 6 |
| `LOCAL_EXACT_RETRY_INTEGRITY` | sum of `exact_retry_integrities` | 6 |
| `LOCAL_WORKSPACE_CLEANUP` | sum of `workspace_cleanups` | 6 |
| `LOCAL_OBSERVATION_CONTRACT_VALID` | count of windows whose `observation_contract_valid` is exactly `true` | 3 |

Each SLI record must contain exactly `numerator`, `denominator`, `observed_ppm`,
`objective_ppm`, `error_budget_ppm`, and `state`. A state is `OK` only when
`observed_ppm >= objective_ppm`; otherwise it is `BREACH`. With the zero error budget,
any single miss remains visible in the completed three-window group and cannot be
repaired by a later window in that same group. Boolean values must never substitute for
integer counts, and integers `0/1` must never substitute for booleans.

## Acceptance SLO breach

The report-internal evaluation rule is frozen as follows:

```text
name=LOCAL_DEMO_ACCEPTANCE_SLO_BREACH
threshold=any_sli_observed_ppm < objective_ppm
owner=networkagent-local-owner
runbook_anchor=local-slo-evidence#acceptance-slo-breach
external_delivery=false
```

`breached_slis` must be independently reconstructed from the five SLI states in the
table order. This rule is evaluated only inside the evidence report. It is not an
external page, notification, monitoring backend, or automated recovery action.

## 6. OK, BREACH, and ERROR are different states

| State | Meaning | Process boundary |
|---|---|---|
| `OK` | Three trustworthy, complete, source-consistent windows exist and all five SLIs meet the objective. | Exit 0; the evidence body plus stdout-only report metadata is written to stdout. |
| `BREACH` | Three trustworthy, complete windows exist, but at least one independently recomputed SLI misses the objective. | Exit 2; stderr uses code `slo_breach`. The safely persisted `local-slo-report.json` has `ok=false` and `evaluation.state=BREACH`. |
| `ERROR` | The execution, child envelope/body binding, JSON, path or identity, digest, source binding, cleanup claim, or aggregate report write is not trustworthy. | Exit 2 with a stable error code. Do not perform or claim SLO mathematics, and do not assume an aggregate report exists. |

A trustworthy child failure must bind its fixed error envelope to a matching persisted
S4-01 `FAIL` report. A nonzero envelope cannot relabel a successful child report. Source
records from all three windows must have one stable valid commit SHA and exact binding
semantics. A dirty but stable worktree can produce only worktree evidence; the release
candidate requires a clean commit-bound source matching `GITHUB_SHA`.

## 7. Report and source verification

The persisted aggregate body has no report pointer. Public success output adds only:

```text
filename=local-slo-report.json
bytes=<strict positive integer>
sha256=<64 lowercase hexadecimal characters>
```

Do not guess a random directory token and do not accept a path from untrusted output.
Compare the fixed defense-run directory inventory before and after the invocation, then
select only the newly created non-link regular `local-slo-report.json`. Verify its
filename, raw UTF-8 byte count, and SHA-256 against the public metadata. Removing the
stdout-only `report` field from a successful summary must reproduce the persisted body
exactly.

For either `OK` or a safely persisted `BREACH`, verify all of the following before using
the evidence:

1. The schema and frozen scope in section 3 match exactly.
2. All three source records are trustworthy and bind one unchanged commit SHA.
3. Window sequences are exactly 1 through 3 and their run-directory identities are
   distinct.
4. Recompute all five numerators, denominators, integer ppm values, and states from
   `windows`.
5. Recompute `breached_slis` and compare it with the frozen evaluation rule.
6. Recompute the persisted report bytes and SHA-256.

Treat any mismatch as `ERROR`, not as a measurable breach.

## 8. Breach handling and fresh evaluation recovery

Preserve the stable stderr envelope and the old aggregate report. Classify the miss by
the affected SLI:

- stage-command misses require review of the exact 22-event S4-01 graph;
- branch-outcome misses require both `RESOLVED/PASSED` and the deliberate
  `REOPENED/FAILED` result;
- retry misses require both branches to prove exact retry without amplification;
- cleanup misses require both marker-owned workspaces to be confirmed removed;
- contract misses require the complete S4-01 schema, report closure, event graph,
  privacy, low-cardinality metrics, four report-internal alerts, and source binding.

Do not change the objective or error budget, remove failing fields, choose only healthy
windows, combine windows from different invocations, overwrite the old report, or
recursively delete `.local`. After fixing the underlying cause, recovery evidence can
only come from a new command invocation containing three entirely fresh windows.

A new `OK` report proves only that the new fixed evaluation group is healthy. It does
not edit, erase, or retroactively recover the earlier `BREACH`. This is fresh evaluation
recovery, not automatic remediation and not backup/recovery.

## 9. Cleanup safety

On execution and error paths the wrapper can handle, S4-01 uses its protected `finally`
path to attempt both marker-owned workspace resets. S4-03 does not impose its own outer
process kill that would deliberately skip that path. This does not claim cleanup is
guaranteed after an operating-system or CI job force-termination outside the wrapper's
control.

Two outcomes must remain separate. If a trustworthy child report explicitly records
fewer than two completed cleanups, the complete three-window group can produce a
`LOCAL_WORKSPACE_CLEANUP` `BREACH`. If a child instead claims both cleanups succeeded
while either workspace directory still exists, the evidence contradicts the filesystem
and must be `window_contract_failed` `ERROR`; no SLO mathematics may be produced or
claimed for that group.

If a workspace still remains, do not remove the run directory or `.local` broadly.
Follow [Local observability demo](local-observability-demo.md) and use its protected
reset only for the exact marker-owned workspace after verifying its identity. Until
cleanup is confirmed, the evidence must not be presented as `OK`. Preserve whether the
original result was a measurable `BREACH` or an untrustworthy `ERROR`; cleanup must not
be used to rewrite that historical state.

## 10. Privacy contract

The aggregate report records none of the following:

- raw child stdout or stderr;
- raw events or raw arguments;
- environment values;
- absolute paths or report paths;
- workspace, domain, or observation identifiers;
- high-cardinality metric labels.

The fixed privacy record must state each corresponding flag as `false` and
`status=PASS`. Source commit and aggregate report SHA values are integrity evidence;
they are not domain identifiers, propagated trace context, or a distributed trace.

```text
status=PASS
absolute_paths_recorded=false
child_stderr_recorded=false
child_stdout_recorded=false
domain_identifiers_recorded=false
environment_recorded=false
high_cardinality_metric_labels=false
observation_identifiers_recorded=false
raw_arguments_recorded=false
raw_events_recorded=false
report_paths_recorded=false
workspace_identifiers_recorded=false
```

## 11. Coverage limits

The report explicitly does not claim:

- `TIME_BASED_AVAILABILITY_SLO`
- `LATENCY_SLO`
- `LONG_TERM_STATISTICAL_RELIABILITY`
- `RUNTIME_STRUCTURED_LOGGING`
- `OPEN_TELEMETRY_EXPORT`
- `COLLECTOR_OR_DISTRIBUTED_TRACE`
- `PROMETHEUS_RECORDING_RULES`
- `EXTERNAL_ALERT_DELIVERY`
- `MULTI_REPLICA_LOAD_OR_CAPACITY`
- `BACKUP_OR_RECOVERY`
- `GATE_E_OR_G5_CLOSURE`
- `CLOUD_OR_PRODUCTION_SLO`

The delivered list is exactly:

- `FIXED_THREE_RUN_LOCAL_ACCEPTANCE_WINDOW`
- `INTEGER_PPM_ACCEPTANCE_SLIS`
- `ZERO_ERROR_BUDGET_ACCEPTANCE_OBJECTIVE`
- `IN_REPORT_BREACH_EVALUATION`

## 12. CI, release closure, and status transition

The Local workflow must execute one real three-window group under Python 3.12 and one
under Python 3.13. Only the Python 3.12
`release-evidence/local-slo-summary.json` is the fourth supplemental evidence file added
to release-manifest construction, manifest verification, and the uploaded artifact.
Python 3.13 validates the same public contract but does not upload a duplicate artifact.

Before both remote jobs finish successfully and the Python 3.12 artifact is independently
verified for exact membership, manifest status, summary bytes/SHA, persisted-body
reconstruction, source SHA, and SLI recomputation, the status remains exactly
`IN PROGRESS (implemented, awaiting remote RC)`.

After that evidence exists, documentation may mark only `DONE (S4-03 narrow slice)`.
S4, Workflow E, Gate E, G5, G2, G4, Cloud/production SLO, external alert delivery, and
backup/recovery remain open. A fresh healthy evaluation must never be described as
automatic recovery, production reliability, or a backup/recovery result.
