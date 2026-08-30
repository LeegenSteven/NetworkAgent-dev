FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS wheel-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN --mount=type=bind,source=deploy/local/build-requirements-py312-linux-amd64.lock,target=/build-requirements.lock,ro \
    python -m pip install --no-cache-dir \
        --require-hashes \
        --only-binary=:all: \
        --no-deps \
        --requirement /build-requirements.lock

RUN --mount=type=bind,source=deploy/local/runtime-requirements-py312-linux-amd64.lock,target=/runtime-requirements.lock,ro \
    python -m pip download --no-cache-dir \
        --require-hashes \
        --only-binary=:all: \
        --no-deps \
        --requirement /runtime-requirements.lock \
        --dest /runtime-wheels

COPY packages/telco-domain/pyproject.toml packages/telco-domain/README.md /build/domain/
COPY packages/telco-domain/src /build/domain/src
COPY packages/telco-local/pyproject.toml packages/telco-local/README.md /build/local/
COPY packages/telco-local/src /build/local/src
COPY packages/telco-lab/pyproject.toml packages/telco-lab/README.md packages/telco-lab/THIRD_PARTY_DATA.md /build/lab/
COPY packages/telco-lab/src /build/lab/src
COPY networkagents/assurance/pyproject.toml networkagents/assurance/README.md /build/assurance/
COPY networkagents/assurance/src /build/assurance/src

RUN python -m pip wheel --no-cache-dir --no-build-isolation --no-deps \
        --wheel-dir /wheels \
        /build/domain /build/local /build/lab /build/assurance


FROM python:3.12-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579 AS runtime

LABEL org.opencontainers.image.title="NetworkAgent isolated Local profile" \
      org.opencontainers.image.description="Loopback-only local assurance runtime" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/LeegenSteven/NetworkAgent-dev"

ENV HOME=/nonexistent \
    LANG=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN --network=none \
    --mount=type=bind,from=wheel-builder,source=/runtime-wheels,target=/runtime-wheels,ro \
    --mount=type=bind,from=wheel-builder,source=/wheels,target=/wheels,ro \
    --mount=type=bind,source=deploy/local/runtime-requirements-py312-linux-amd64.lock,target=/runtime-requirements.lock,ro \
    python -m pip install --no-cache-dir --no-compile \
        --no-index \
        --find-links=/runtime-wheels \
        --require-hashes \
        --only-binary=:all: \
        --no-deps \
        --requirement /runtime-requirements.lock \
    && python -m pip install --no-cache-dir --no-compile --no-index --no-deps \
        /wheels/*.whl \
    && python -m pip check \
    && python -c "import telco_assurance_agent, telco_domain, telco_lab, telco_local" \
    && groupadd --gid 10001 networkagent \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin networkagent \
    && install -d -o 10001 -g 10001 -m 0750 /var/lib/networkagent \
    && install -d -o 10001 -g 10001 -m 0755 \
        /opt/networkagent/bin \
        /opt/networkagent/share \
        /opt/networkagent/tools/local-stack \
        /opt/networkagent/data/samples/lte-demo \
        /opt/networkagent/data/rca-rules/lte \
        /opt/networkagent/data/docs/lte \
    && touch /var/lib/networkagent/.volume-owner \
    && chown 10001:10001 /var/lib/networkagent/.volume-owner

COPY --chown=10001:10001 tools/local-container/container_entrypoint.py /opt/networkagent/bin/container_entrypoint.py
COPY --chown=10001:10001 tools/local-stack/local_stack.py /opt/networkagent/tools/local-stack/local_stack.py
COPY --chown=10001:10001 deploy/local/input-manifest.json /opt/networkagent/share/input-manifest.json

WORKDIR /opt/networkagent
USER 10001:10001

ENTRYPOINT ["python", "/opt/networkagent/bin/container_entrypoint.py"]
CMD ["serve"]
