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
