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
