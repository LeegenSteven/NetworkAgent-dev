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

S2-01 is **DONE** based on its commit-bound remote Docker evidence. S2-02 is
**READY FOR REVIEW** until the updated `telco-container` workflow proves both
branches and restart recovery on a fresh Linux runner. This is still not Gate B
complete. A `--require-hashes` dependency lock, Trivy vulnerability policy,
generated container SBOM, and artifact signing/provenance are open
supply-chain work and must be closed before container promotion.
