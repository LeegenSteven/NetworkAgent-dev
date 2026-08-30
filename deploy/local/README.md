# Isolated Local container candidate

This directory contains the S2-01 container baseline and the S2-02 simulated
governance acceptance flow for the local assurance profile.
It is intentionally isolated: the server binds only to `127.0.0.1` inside a
Docker `network_mode: none` namespace, and no service publishes or exposes a
host port. The `probe` and `smoke` services join the assurance container's
network namespace, so verification can reach loopback without creating a
bridge network.

## Run the candidate

Docker Compose v2 and a Docker Engine are required. From the repository root:

```text
docker compose -f deploy/local/compose.yaml build assurance
docker compose -f deploy/local/compose.yaml run --rm init
docker compose -f deploy/local/compose.yaml up -d assurance
docker compose -f deploy/local/compose.yaml run --rm smoke
docker compose -f deploy/local/compose.yaml run --rm probe
```

Stop the server before the marker-owned reset:

```text
docker compose -f deploy/local/compose.yaml stop assurance
docker compose -f deploy/local/compose.yaml run --rm reset
docker compose -f deploy/local/compose.yaml down --remove-orphans
```

`down` preserves the named workspace volume. Use `--volumes` only when deletion
of that Docker-managed volume is explicitly intended.

## Enforced boundary

- The Python 3.12 Debian base is pinned by digest and used for both build stages.
- The runtime is UID/GID `10001:10001`, has a read-only root filesystem, drops
  every capability, enables `no-new-privileges`, and has bounded CPU, memory,
  process, file-descriptor, and temporary-filesystem limits.
- The image entry point accepts only `init`, `serve`, `reset`, `probe`, or
  `smoke`, the two fixed offline demo operations, or the three fixed governance
  operations. It is not a shell or arbitrary command pass-through. Governance
  always targets `127.0.0.1:8085`, disables proxies and redirects, and exposes
  no caller-controlled URL, actor, reason, header, or idempotency key.
- Raw LTE inputs never enter an image layer. Compose mounts the four approved
  inputs with long bind syntax, `read_only: true`, and
  `create_host_path: false`. Before init, serve, or smoke, an image-owned
  manifest checks exact file sets, byte counts, and SHA-256 digests.
- The Docker named volume at `/var/lib/networkagent` is the only persistent
  writable mount. `/tmp` is a size-bounded, `noexec,nosuid,nodev` tmpfs.
- There are no host ports, bridge networks, reverse proxies, Docker socket
  mounts, devices, environment files, GCP credentials, or secret/config mounts.

`tools/local-container/compose_guard.py` validates the fully resolved Compose
JSON, not only the YAML text. GitHub CI also builds the real image, inspects its
runtime settings and contents, initializes the volume, waits for health, runs
the shared-loopback smoke/probe, and exercises reset.

`tools/local-container/run_governance_demo.py` is the closed S2-02 CI
orchestrator. It creates two random, bounded Compose projects with independent
named volumes. One reaches `RESOLVED/PASSED`; the other reaches
`REOPENED/FAILED`. Both use only `LOCAL_SIMULATION`. It then restarts Assurance,
replays the exact prepare/decide/execute requests with the original action hash
and revision, stops the service, verifies the database offline, resets the
marker-owned workspace, and removes each project volume in a `finally` path.
The second branch is an intentionally failed verification, not a failed Docker
command and not a real remediation action.

## Promotion status

S2-01 and S2-02 are **DONE** based on commit-bound remote Docker evidence. For
S2-02, RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` passed
[telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750),
including [compose-policy job 99266075811](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266075811)
and [build-inspect-smoke job 99266104885](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266104885).
The Linux policy suite reported `128 passed`. The real success and intentional
verification-failure branches ended `RESOLVED` and `REOPENED`; both reported
`restart_observed=true`, `exact_replay=true`, and
`real_network_side_effects=false`. The top-level `projects_removed=true`
confirms both Compose projects were removed. The container run uploaded 0
artifacts.

The same RC passed both jobs in
[Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757):
each Python variant reported Domain + Local `518 passed`, local-stack
`29 passed, 2 skipped`, and Local E2E `2 passed`. Python 3.12 published
[VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877)
with archive digest
`sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`.
That is Local release evidence, not a registry image or container artifact.

This is still not Gate B complete. A `--require-hashes` dependency lock, Trivy
Critical/High vulnerability policy, registry image/digest, generated container
SBOM, and signing/attestation/provenance remain open supply-chain work. Sprint
2, Workflow B, and P7 therefore remain `IN PROGRESS`; Gate B, Gate A, S3, and G4
remain open.
