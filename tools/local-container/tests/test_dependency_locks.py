from __future__ import annotations

import re
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BUILD_LOCK = (
    REPOSITORY_ROOT / "deploy" / "local" / "build-requirements-py312-linux-amd64.lock"
)
RUNTIME_LOCK = (
    REPOSITORY_ROOT / "deploy" / "local" / "runtime-requirements-py312-linux-amd64.lock"
)
DOCKERFILE = REPOSITORY_ROOT / "deploy" / "local" / "Dockerfile"


EXPECTED_BUILD_WHEELS = {
    "setuptools": (
        "81.0.0",
        "setuptools-81.0.0-py3-none-any.whl",
        "fdd925d5c5d9f62e4b74b30d6dd7828ce236fd6ed998a08d81de62ce5a6310d6",
    ),
    "wheel": (
        "0.45.1",
        "wheel-0.45.1-py3-none-any.whl",
        "708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248",
    ),
}


EXPECTED_RUNTIME_WHEELS = {
    "a2a-sdk": (
        "0.3.11",
        "a2a_sdk-0.3.11-py3-none-any.whl",
        "f57673d5f38b3e0eb7c5b57e7dc126404d02c54c90692395ab4fd06aaa80cc8f",
    ),
    "annotated-doc": (
        "0.0.5",
        "annotated_doc-0.0.5-py3-none-any.whl",
        "117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101",
    ),
    "annotated-types": (
        "0.8.0",
        "annotated_types-0.8.0-py3-none-any.whl",
        "f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0",
    ),
    "anyio": (
        "4.14.2",
        "anyio-4.14.2-py3-none-any.whl",
        "9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494",
    ),
    "certifi": (
        "2026.7.22",
        "certifi-2026.7.22-py3-none-any.whl",
        "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775",
    ),
    "cffi": (
        "2.1.1",
        "cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf",
    ),
    "charset-normalizer": (
        "3.5.1",
        "charset_normalizer-3.5.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "b9af956078716df40d985fb0dfeb2c2120c5ca92ba4ff4b388acfd01cdc14d08",
    ),
    "click": (
        "8.5.0",
        "click-8.5.0-py3-none-any.whl",
        "255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360",
    ),
    "cryptography": (
        "50.0.1",
        "cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl",
        "51afcfceb15597cf2635068e4ac9a56b2abde622edde17f37d85fd7b5306497a",
    ),
    "duckdb": (
        "1.5.5",
        "duckdb-1.5.5-cp312-cp312-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl",
        "7a6d2d11859d82a936ebdcb30ce3d8a1cbb3e990bff05c12abb9b54c44fa7bd1",
    ),
    "fastapi": (
        "0.141.1",
        "fastapi-0.141.1-py3-none-any.whl",
        "bfb91aa2d334c61cb35ba9a116fc123b3d3df31640b801cf57a7a78ec3f603b3",
    ),
    "google-api-core": (
        "2.34.0",
        "google_api_core-2.34.0-py3-none-any.whl",
        "cdf9c67e7ca2402d86ccbfde5f2503fc83e3cc3f58cc78456ae96cad24a6d2de",
    ),
    "google-auth": (
        "2.57.0",
        "google_auth-2.57.0-py3-none-any.whl",
        "180dafe015cfb62193bea26b677500fab5b9fd51a1e825ebf3ad9b182047ae59",
    ),
    "googleapis-common-protos": (
        "1.75.2",
        "googleapis_common_protos-1.75.2-py3-none-any.whl",
        "6b83302f554ea93a0f48409c7fc2050f954bcbcddb7e3a9c76d4a823cb22920e",
    ),
    "h11": (
        "0.16.0",
        "h11-0.16.0-py3-none-any.whl",
        "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
    ),
    "httpcore": (
        "1.0.9",
        "httpcore-1.0.9-py3-none-any.whl",
        "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
    ),
    "httpx": (
        "0.28.1",
        "httpx-0.28.1-py3-none-any.whl",
        "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
    ),
    "httpx-sse": (
        "0.4.3",
        "httpx_sse-0.4.3-py3-none-any.whl",
        "0ac1c9fe3c0afad2e0ebb25a934a59f4c7823b60792691f779fad2c5568830fc",
    ),
    "idna": (
        "3.19",
        "idna-3.19-py3-none-any.whl",
        "815e7be7a7806d54abb586dc943addc79e8b2ee16915059658cbeff4b1b43bf4",
    ),
    "proto-plus": (
        "1.28.4",
        "proto_plus-1.28.4-py3-none-any.whl",
        "4b01341272f8a348db3f003b6143109f83ab43091019d5181b3fcdf500ab32aa",
    ),
    "protobuf": (
        "7.36.0",
        "protobuf-7.36.0-cp310-abi3-manylinux2014_x86_64.whl",
        "70f5ec8eb0da81a44360c0dc0beac99a0d78071d21956a7076bae8bd2051841b",
    ),
    "pyasn1": (
        "0.6.4",
        "pyasn1-0.6.4-py3-none-any.whl",
        "deda9277cfd454080ec40b207fb6df82206a3a2688735233cdcd8d3d565f088b",
    ),
    "pyasn1-modules": (
        "0.4.2",
        "pyasn1_modules-0.4.2-py3-none-any.whl",
        "29253a9207ce32b64c3ac6600edc75368f98473906e8fd1043bd6b5b1de2c14a",
    ),
    "pycparser": (
        "3.0",
        "pycparser-3.0-py3-none-any.whl",
        "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
    ),
    "pydantic": (
        "2.13.4",
        "pydantic-2.13.4-py3-none-any.whl",
        "45a282cde31d808236fd7ea9d919b128653c8b38b393d1c4ab335c62924d9aba",
    ),
    "pydantic-core": (
        "2.46.4",
        "pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "926c9541b14b12b1681dca8a0b75feb510b06c6341b70a8e500c2fdcff837cce",
    ),
    "pytz": (
        "2026.3.post1",
        "pytz-2026.3.post1-py2.py3-none-any.whl",
        "dd95840dd199baea12d9cc096a1d452caa6596a1c1e4b5f3dbd1541855d5e815",
    ),
    "requests": (
        "2.34.2",
        "requests-2.34.2-py3-none-any.whl",
        "2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0",
    ),
    "sse-starlette": (
        "3.4.8",
        "sse_starlette-3.4.8-py3-none-any.whl",
        "6e82314c786709a3cd9520f2285cf9fff90e181e598e8a357b0cf80f66afba0d",
    ),
    "starlette": (
        "1.6.0",
        "starlette-1.6.0-py3-none-any.whl",
        "a86dd39d14bb45f85a3d18525215a9ef0cfd1f192ac793220e72598c90335f0c",
    ),
    "typing-extensions": (
        "4.16.0",
        "typing_extensions-4.16.0-py3-none-any.whl",
        "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
    ),
    "typing-inspection": (
        "0.4.4",
        "typing_inspection-0.4.4-py3-none-any.whl",
        "65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147",
    ),
    "urllib3": (
        "2.7.0",
        "urllib3-2.7.0-py3-none-any.whl",
        "9fb4c81ebbb1ce9531cce37674bbc6f1360472bc18ca9a553ede278ef7276897",
    ),
    "uvicorn": (
        "0.35.0",
        "uvicorn-0.35.0-py3-none-any.whl",
        "197535216b25ff9b785e29a0b79199f55222193d47f820816e7da751e9bc8d4a",
    ),
}


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REQUIREMENT = re.compile(r"^([a-z0-9-]+)==([^\s]+) \\$")
_HASH = re.compile(r"^    --hash=sha256:([0-9a-f]{64})$")
_DIRECT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _read_lock(path: Path) -> dict[str, tuple[str, str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    result: dict[str, tuple[str, str, str]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or (line.startswith("#") and not line.startswith("# wheel: ")):
            index += 1
            continue
        assert line.startswith("# wheel: ")
        filename = line.removeprefix("# wheel: ")
        assert (
            filename.endswith(".whl") and "/" not in filename and "\\" not in filename
        )
        assert index + 2 < len(lines)
        requirement = _REQUIREMENT.fullmatch(lines[index + 1])
        digest = _HASH.fullmatch(lines[index + 2])
        assert requirement is not None and digest is not None
        name = requirement.group(1)
        assert _NAME.fullmatch(name) is not None
        assert name not in result
        result[name] = (requirement.group(2), filename, digest.group(1))
        index += 3
    return result


def _is_target_wheel(filename: str) -> bool:
    if filename.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl")):
        return True
    return bool(
        re.search(r"-cp312-cp312-.*manylinux.*_x86_64\.whl$", filename)
        or re.search(r"-cp3(?:10|11)-abi3-.*manylinux.*_x86_64\.whl$", filename)
    )


def test_build_lock_is_exact_single_hash_official_wheel_inventory() -> None:
    entries = _read_lock(BUILD_LOCK)
    assert entries == EXPECTED_BUILD_WHEELS
    assert all(
        filename.endswith("-py3-none-any.whl") for _, filename, _ in entries.values()
    )


def test_runtime_lock_is_exact_single_hash_target_wheel_inventory() -> None:
    entries = _read_lock(RUNTIME_LOCK)
    assert entries == EXPECTED_RUNTIME_WHEELS
    assert all(_is_target_wheel(filename) for _, filename, _ in entries.values())
    assert len({digest for _, _, digest in entries.values()}) == len(entries)


def test_runtime_lock_covers_all_and_only_first_party_runtime_dependencies() -> None:
    pyprojects = (
        REPOSITORY_ROOT / "packages" / "telco-domain" / "pyproject.toml",
        REPOSITORY_ROOT / "packages" / "telco-local" / "pyproject.toml",
        REPOSITORY_ROOT / "packages" / "telco-lab" / "pyproject.toml",
        REPOSITORY_ROOT / "networkagents" / "assurance" / "pyproject.toml",
    )
    first_party = {"telco-domain", "telco-local", "telco-lab", "telco-assurance-agent"}
    direct: set[str] = set()
    build_backends: set[str] = set()
    for path in pyprojects:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        for requirement in document["project"]["dependencies"]:
            match = _DIRECT_NAME.match(requirement)
            assert match is not None
            name = _canonical_name(match.group(1))
            if name not in first_party:
                direct.add(name)
        for requirement in document["build-system"]["requires"]:
            match = _DIRECT_NAME.match(requirement)
            assert match is not None
            build_backends.add(_canonical_name(match.group(1)))
    assert direct == {"a2a-sdk", "duckdb", "pydantic", "pytz", "starlette", "uvicorn"}
    assert direct < set(EXPECTED_RUNTIME_WHEELS)
    assert build_backends == {"setuptools"}
    assert build_backends < set(EXPECTED_BUILD_WHEELS)


def test_dockerfile_uses_hash_locks_and_separates_first_party_wheels() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    normalized = " ".join(text.replace("\\\n", " ").split())
    assert "runtime-constraints.txt" not in text
    assert (
        "source=deploy/local/build-requirements-py312-linux-amd64.lock,"
        "target=/build-requirements.lock,ro" in normalized
    )
    assert (
        "source=deploy/local/runtime-requirements-py312-linux-amd64.lock,"
        "target=/runtime-requirements.lock,ro" in normalized
    )
    assert (
        "pip install --no-cache-dir --require-hashes --only-binary=:all: "
        "--no-deps --requirement /build-requirements.lock" in normalized
    )
    assert (
        "pip download --no-cache-dir --require-hashes --only-binary=:all: "
        "--no-deps --requirement /runtime-requirements.lock "
        "--dest /runtime-wheels" in normalized
    )
    assert (
        "pip install --no-cache-dir --no-compile --no-index "
        "--find-links=/runtime-wheels --require-hashes --only-binary=:all: "
        "--no-deps --requirement /runtime-requirements.lock" in normalized
    )
    assert (
        "pip install --no-cache-dir --no-compile --no-index --no-deps "
        "/wheels/*.whl" in normalized
    )
    assert "python -m pip check" in normalized
    assert "RUN --network=none" in normalized
    assert (
        'python -c "import telco_assurance_agent, telco_domain, telco_lab, telco_local"'
        in normalized
    )
    assert "--constraint" not in text
