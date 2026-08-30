# Isolated Local container candidate

This directory is the S2-01 container baseline for the local assurance profile.
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
  `smoke`; it is not a shell or arbitrary command pass-through.
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

## Promotion status

This slice is **READY FOR REVIEW**, not Gate B complete. The local workstation
used to author it has no Docker Engine, so real runtime evidence must come from
the `telco-container` GitHub workflow. A `--require-hashes` dependency lock,
Trivy vulnerability policy, generated SBOM, and artifact signing/provenance are
still open supply-chain work and must be closed before container promotion.
