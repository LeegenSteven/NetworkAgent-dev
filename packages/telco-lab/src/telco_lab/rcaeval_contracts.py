"""Frozen contracts for the audited five-case RCAEval evaluation slice."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

import pyarrow as pa

from .parquet_reader import ParquetContract, parquet_schema_fingerprint


RCAEVAL_PIPELINE_ID: Final = "rcaeval-re2ob-multisource-rca"
RCAEVAL_DATASET_ID: Final = "rcaeval-re2ob-evaluation-slice"
RCAEVAL_DATASET_VERSION: Final = "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"
RCAEVAL_LICENSE_ID: Final = "MIT"
RCAEVAL_CATALOG_ID: Final = "networkagent-open-data"
RCAEVAL_CATALOG_VERSION: Final = "1.1.0"
RCAEVAL_INDEX_RESOURCE_ID: Final = "rcaeval.re2ob.index.v1"
RCAEVAL_TOTAL_BYTES: Final = 53_433_532
RCAEVAL_SAMPLE_COUNT: Final = 5
RCAEVAL_RESOURCE_COUNT: Final = 16
RCAEVAL_TELEMETRY_RESOURCE_COUNT: Final = 15
RCAEVAL_UPSTREAM_CLASSIFICATION: Final = "PINNED_UPSTREAM_RCAEVAL_RE2OB_SLICE"
RCAEVAL_FIXTURE_CLASSIFICATION: Final = "CODE_GENERATED_SCHEMA_FIXTURE"

_CREATED_BY = "parquet-cpp-arrow version 25.0.0"
_FORMAT_VERSION = "2.6"
_CODECS = ("ZSTD",)


@dataclass(frozen=True, slots=True)
class RcaEvalResourceContract:
    """Catalog bytes and the exact audited Parquet envelope."""

    resource_id: str
    size_bytes: int
    sha256: str
    adapter: str
    parquet: ParquetContract


_CORE_SERVICES = (
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "redis",
    "shippingservice",
)
_WORKLOAD_SERVICES = (
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "frontend-external",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "shippingservice",
)
_LATENCY_SERVICES = (
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "shippingservice",
)


def _metric_names(
    *,
    diskio: tuple[str, ...],
    errors: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        "time",
        *(f"{service}_cpu" for service in _CORE_SERVICES),
        *(f"{service}_mem" for service in _CORE_SERVICES),
        *(f"{service}_diskio" for service in diskio),
        *(f"{service}_socket" for service in _CORE_SERVICES),
        *(f"{service}_workload" for service in _WORKLOAD_SERVICES),
        *(f"{service}_error" for service in errors),
        *(f"{service}_latency-50" for service in _LATENCY_SERVICES),
        *(f"{service}_latency-90" for service in _LATENCY_SERVICES),
    )


_CHECKOUT_EMAIL_METRICS = _metric_names(
    diskio=("adservice", "emailservice", "recommendationservice", "redis"),
    errors=(
        "currencyservice",
        "frontend",
        "frontend-external",
        "productcatalogservice",
    ),
)
_CURRENCY_METRICS = _metric_names(
    diskio=(
        "adservice",
        "cartservice",
        "currencyservice",
        "emailservice",
        "frontend",
        "paymentservice",
        "productcatalogservice",
        "recommendationservice",
        "redis",
    ),
    errors=("frontend", "frontend-external"),
)
_PRODUCT_METRICS = _metric_names(
    diskio=(
        "adservice",
        "cartservice",
        "emailservice",
        "recommendationservice",
        "redis",
    ),
    errors=("frontend", "frontend-external"),
)
_RECOMMENDATION_METRICS = _metric_names(
    diskio=(
        "adservice",
        "cartservice",
        "emailservice",
        "recommendationservice",
        "redis",
    ),
    errors=("currencyservice", "frontend", "frontend-external"),
)


def _metric_schema(names: tuple[str, ...]) -> pa.Schema:
    return pa.schema(
        [
            pa.field(name, pa.int64() if name == "time" else pa.float64())
            for name in names
        ]
    )


_CASES_SCHEMA = pa.schema(
    [
        pa.field("case", pa.large_string()),
        pa.field("dataset", pa.large_string()),
        pa.field("suite", pa.large_string()),
        pa.field("system", pa.large_string()),
        pa.field("system_name", pa.large_string()),
        pa.field("root_cause_service", pa.large_string()),
        pa.field("fault", pa.large_string()),
        pa.field("fault_description", pa.large_string()),
        pa.field("repetition", pa.int64()),
        pa.field("inject_time", pa.int64()),
        pa.field("n_metrics", pa.int64()),
        pa.field("n_timesteps", pa.int64()),
        pa.field("time_start", pa.int64()),
        pa.field("time_end", pa.int64()),
        pa.field("duration_minutes", pa.float64()),
        pa.field("normal_timesteps", pa.int64()),
        pa.field("faulty_timesteps", pa.int64()),
        pa.field("has_logs", pa.bool_()),
        pa.field("n_logs", pa.int64()),
        pa.field("has_traces", pa.bool_()),
        pa.field("n_traces", pa.int64()),
        pa.field("has_root_cause_file", pa.bool_()),
    ]
)
_LOG_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.int64()),
        pa.field("container_name", pa.large_string()),
        pa.field("message", pa.large_string()),
    ]
)
_TRACE_SCHEMA = pa.schema(
    [
        pa.field("time", pa.large_string()),
        pa.field("traceID", pa.large_string()),
        pa.field("spanID", pa.large_string()),
        pa.field("serviceName", pa.large_string()),
        pa.field("methodName", pa.large_string()),
        pa.field("operationName", pa.large_string()),
        pa.field("parentSpanID", pa.large_string()),
        pa.field("startTimeMillis", pa.int64()),
        pa.field("startTime", pa.int64()),
        pa.field("duration", pa.int64()),
        pa.field("statusCode", pa.int64()),
    ]
)
_CHECKOUT_EMAIL_SCHEMA = _metric_schema(_CHECKOUT_EMAIL_METRICS)
_CURRENCY_SCHEMA = _metric_schema(_CURRENCY_METRICS)
_PRODUCT_SCHEMA = _metric_schema(_PRODUCT_METRICS)
_RECOMMENDATION_SCHEMA = _metric_schema(_RECOMMENDATION_METRICS)

_SCHEMA_FINGERPRINTS = {
    "cases": "edc5bd588eb576ca1cb1f2d3aa23af276cc73029bb9800fb68f2bf70d1be4bb9",
    "logs": "18384aef8503225da4d676a5159e64cc77cc02252107be4254b22546f67f1923",
    "traces": "3bce57ca022c40e7667bf1d094603da5df0a77ad94fcfe3f38644299eb3e9c14",
    "checkout-email": "841a78517490e9c21c083b4f7348b8c8a658fab502bc87f43ac4681d9c18fef3",
    "currency": "3b6b8bf838320956685820a93b0ab1a9c8a9f981439a56f4dadd798241322150",
    "product": "1423284cd2574cd2ab94b5ad863d4891b2aa6876011365ebd1626efaa3063eb2",
    "recommendation": "cf43fd5106521d1bbef0eaf478159af2ffb9e0a73fdf91bef032f6ef942aa93a",
}


def _parquet(
    schema: pa.Schema,
    fingerprint: str,
    projection: tuple[str, ...],
    *,
    rows: int,
    metadata_bytes: int,
    compressed_bytes: int,
    uncompressed_bytes: int,
) -> ParquetContract:
    return ParquetContract(
        expected_schema=schema,
        expected_schema_fingerprint=fingerprint,
        projected_columns=projection,
        expected_rows=rows,
        expected_row_groups=1,
        allowed_codecs=_CODECS,
        expected_created_by=_CREATED_BY,
        expected_format_version=_FORMAT_VERSION,
        expected_metadata_bytes=metadata_bytes,
        expected_compressed_bytes=compressed_bytes,
        expected_uncompressed_bytes=uncompressed_bytes,
    )


RCAEVAL_CASE_TIMING_CONTRACT: Final = _parquet(
    _CASES_SCHEMA,
    _SCHEMA_FINGERPRINTS["cases"],
    ("case", "inject_time", "time_start", "time_end"),
    rows=735,
    metadata_bytes=11_037,
    compressed_bytes=18_451,
    uncompressed_bytes=54_187,
)
RCAEVAL_CASE_ANSWER_CONTRACT: Final = _parquet(
    _CASES_SCHEMA,
    _SCHEMA_FINGERPRINTS["cases"],
    ("case", "root_cause_service"),
    rows=735,
    metadata_bytes=11_037,
    compressed_bytes=18_451,
    uncompressed_bytes=54_187,
)


def _resource(
    resource_id: str,
    *,
    size: int,
    digest: str,
    adapter: str,
    parquet: ParquetContract,
) -> RcaEvalResourceContract:
    return RcaEvalResourceContract(
        resource_id=resource_id,
        size_bytes=size,
        sha256=digest,
        adapter=adapter,
        parquet=parquet,
    )


_INDEX = _resource(
    RCAEVAL_INDEX_RESOURCE_ID,
    size=29_500,
    digest="c49a288920dbba2e8e724679a14636d5c7eb2b45426bba14007ef79a6c0ab1bb",
    adapter="rcaeval_case_index_v1",
    parquet=RCAEVAL_CASE_TIMING_CONTRACT,
)

_SLOT_DIGESTS = (
    "d635de19be3e19fafed570e51edfe2fe6b0df8038f1e090f31806f54ba0274d0",
    "9653806b419219e05bbf016b74d5f7c56059fe40420efe808d6f78e4567093e4",
    "023defe54da6bacd9a138f05ec1e2cf1800efba77aed2f1a9169afa7dea71e97",
    "c5226721ef585ca1ef4c4484b6d2f50a8ff538bd44652e5f8d6e155e720a8b70",
    "decaf7a6ad9a93eead5477e74988100287e7b8b52f98e3fa347892d8632b890a",
)
RCAEVAL_OPAQUE_SLOTS: Final = tuple(f"rcaslot-{digest}" for digest in _SLOT_DIGESTS)
_CASE_KEY_DIGESTS = (
    "0487e59f95b6a38206cce7a806b939076505f7107966634cb76e91eab89690d7",
    "8dc70b821f5a4558d6dfe51a1a521d394487114f4e33280099b67c4683090d2f",
    "8a3e46cd9fa8dbaf54ef7530286efdc6f0848771e3dc8b543628248410597cb3",
    "ac08fe4892987baed403008536e76687f90ff7f93a41d39e3cca4cd8531dfb30",
    "35ea4da0f1f0064ba7a129d2d40ef8bff679e380ff7b5c8de2f4c1917cf98e78",
)
RCAEVAL_CASE_KEY_SHA256_BY_SLOT: Final[Mapping[str, str]] = MappingProxyType(
    dict(zip(RCAEVAL_OPAQUE_SLOTS, _CASE_KEY_DIGESTS, strict=True))
)

_SLOT_FILE_FACTS = (
    (
        _CHECKOUT_EMAIL_SCHEMA,
        "checkout-email",
        (
            157_212,
            "f46b3d354b234e37c424f54a005329b61aac83d15dcdd46b5642e55f390ab25a",
            42_604,
            114_596,
            151_084,
        ),
        (
            336_876,
            "e1a25e50c8b0beae4b2886df54d86d2f28877d0b611fed98411e9c0b0c77dad3",
            2_177,
            334_687,
            3_749_956,
            171_322,
        ),
        (
            10_146_857,
            "56ca8f6dbeb76fab2d33faeca54bce8f40e9c5491ebd76404ebb2dfd83409367",
            6_111,
            10_140_734,
            24_283_393,
            391_997,
        ),
    ),
    (
        _CURRENCY_SCHEMA,
        "currency",
        (
            159_637,
            "ed8471d2f53c0c3f4095b25eff69d5c45762425afaa973d4dbdc04d7bc32c0f3",
            44_276,
            115_349,
            153_629,
        ),
        (
            342_888,
            "4fd881f7620902c141a7cfa2cd4c6c8115ab2d85c6c0a7fd1914a8dcf9a0c1ba",
            2_158,
            340_718,
            3_867_389,
            175_589,
        ),
        (
            10_214_448,
            "04d1512a44ece85f370caeaa09d24e441adc7f67bbebc0de689faea0d5563a63",
            6_111,
            10_208_325,
            24_955_490,
            401_542,
        ),
    ),
    (
        _CHECKOUT_EMAIL_SCHEMA,
        "checkout-email",
        (
            156_391,
            "52752452d9f2fdb69cec2b7575b617f84419131338fcb1c2e1829ef5c242e586",
            42_604,
            113_775,
            151_523,
        ),
        (
            337_106,
            "5b0eff42c36df03f4b618f79fea5e0e6f95d54e9afe0e857c86b68696bf72f05",
            2_177,
            334_917,
            3_676_342,
            171_212,
        ),
        (
            10_155_267,
            "c98764ff45836b7514480c515a4e0bfe685fb0ce15de8484eae012451b5aea6e",
            6_111,
            10_149_144,
            24_304_548,
            392_420,
        ),
    ),
    (
        _PRODUCT_SCHEMA,
        "product",
        (
            143_586,
            "40a32cdc027251907770a69cfff1a1f7f0b93d9188148b837a3afa1ed425f2de",
            41_956,
            101_618,
            140_483,
        ),
        (
            340_440,
            "3d9a771b2325aee6bce7b1536d7baae0fca7ba8cea8c043921fc64a123d34442",
            2_177,
            338_251,
            3_590_611,
            172_220,
        ),
        (
            10_244_076,
            "fcc49515cf5f50a84000a1dfddf0d75ab5ca06e9712a7f4775bfd2c364283135",
            6_111,
            10_237_953,
            24_460_895,
            393_266,
        ),
    ),
    (
        _RECOMMENDATION_SCHEMA,
        "recommendation",
        (
            154_244,
            "6695d4e58c7383a5a32f7a936f3700aa332501d4431c49003d111d3ef282b12f",
            42_536,
            111_696,
            151_251,
        ),
        (
            343_682,
            "b2622c7e637a9d4b11d618cf17fde180a76cd177f1234d495de6e325f415e021",
            2_157,
            341_513,
            3_685_733,
            171_429,
        ),
        (
            10_171_322,
            "59a3f6b063779d3088d8eea40bbfcf24c79cbde1fe6a6bb3af326ad0bff793b0",
            6_111,
            10_165_199,
            24_260_128,
            391_057,
        ),
    ),
)

_resources: list[RcaEvalResourceContract] = [_INDEX]
_groups: list[tuple[str, str, str, str]] = []
for index, (schema, schema_key, metric, logs, traces) in enumerate(
    _SLOT_FILE_FACTS,
    start=1,
):
    prefix = f"rcaeval.re2ob.slot-{index:02d}"
    metric_id = f"{prefix}.metrics.v1"
    log_id = f"{prefix}.logs.v1"
    trace_id = f"{prefix}.traces.v1"
    metric_contract = _parquet(
        schema,
        _SCHEMA_FINGERPRINTS[schema_key],
        tuple(schema.names),
        rows=1_441,
        metadata_bytes=metric[2],
        compressed_bytes=metric[3],
        uncompressed_bytes=metric[4],
    )
    log_contract = _parquet(
        _LOG_SCHEMA,
        _SCHEMA_FINGERPRINTS["logs"],
        ("timestamp", "container_name"),
        rows=logs[5],
        metadata_bytes=logs[2],
        compressed_bytes=logs[3],
        uncompressed_bytes=logs[4],
    )
    trace_contract = _parquet(
        _TRACE_SCHEMA,
        _SCHEMA_FINGERPRINTS["traces"],
        ("startTime", "startTimeMillis", "duration", "statusCode", "serviceName"),
        rows=traces[5],
        metadata_bytes=traces[2],
        compressed_bytes=traces[3],
        uncompressed_bytes=traces[4],
    )
    _resources.extend(
        (
            _resource(
                metric_id,
                size=metric[0],
                digest=metric[1],
                adapter="rcaeval_metrics_v1",
                parquet=metric_contract,
            ),
            _resource(
                log_id,
                size=logs[0],
                digest=logs[1],
                adapter="rcaeval_logs_v1",
                parquet=log_contract,
            ),
            _resource(
                trace_id,
                size=traces[0],
                digest=traces[1],
                adapter="rcaeval_traces_v1",
                parquet=trace_contract,
            ),
        )
    )
    _groups.append((RCAEVAL_OPAQUE_SLOTS[index - 1], metric_id, log_id, trace_id))

RCAEVAL_RESOURCE_CONTRACTS: Final[Mapping[str, RcaEvalResourceContract]] = (
    MappingProxyType({item.resource_id: item for item in _resources})
)
RCAEVAL_RESOURCE_IDS: Final = tuple(item.resource_id for item in _resources)
RCAEVAL_TELEMETRY_RESOURCE_IDS: Final = RCAEVAL_RESOURCE_IDS[1:]
RCAEVAL_TELEMETRY_GROUPS: Final = tuple(_groups)


def validate_frozen_rcaeval_contracts() -> None:
    """Fail if checked-in constants no longer describe the audited closure."""

    if (
        len(RCAEVAL_RESOURCE_IDS) != RCAEVAL_RESOURCE_COUNT
        or len(RCAEVAL_TELEMETRY_RESOURCE_IDS) != RCAEVAL_TELEMETRY_RESOURCE_COUNT
        or len(set(RCAEVAL_RESOURCE_IDS)) != RCAEVAL_RESOURCE_COUNT
        or sum(item.size_bytes for item in _resources) != RCAEVAL_TOTAL_BYTES
        or tuple(RCAEVAL_RESOURCE_CONTRACTS) != RCAEVAL_RESOURCE_IDS
    ):
        raise RuntimeError("invalid RCAEval resource closure")
    expected_fingerprints = (
        (_CASES_SCHEMA, _SCHEMA_FINGERPRINTS["cases"]),
        (_LOG_SCHEMA, _SCHEMA_FINGERPRINTS["logs"]),
        (_TRACE_SCHEMA, _SCHEMA_FINGERPRINTS["traces"]),
        (_CHECKOUT_EMAIL_SCHEMA, _SCHEMA_FINGERPRINTS["checkout-email"]),
        (_CURRENCY_SCHEMA, _SCHEMA_FINGERPRINTS["currency"]),
        (_PRODUCT_SCHEMA, _SCHEMA_FINGERPRINTS["product"]),
        (_RECOMMENDATION_SCHEMA, _SCHEMA_FINGERPRINTS["recommendation"]),
    )
    if any(
        parquet_schema_fingerprint(schema) != expected
        for schema, expected in expected_fingerprints
    ):
        raise RuntimeError("invalid RCAEval schema contract")


validate_frozen_rcaeval_contracts()


__all__ = [
    "RCAEVAL_CASE_ANSWER_CONTRACT",
    "RCAEVAL_CASE_KEY_SHA256_BY_SLOT",
    "RCAEVAL_CASE_TIMING_CONTRACT",
    "RCAEVAL_CATALOG_ID",
    "RCAEVAL_CATALOG_VERSION",
    "RCAEVAL_DATASET_ID",
    "RCAEVAL_DATASET_VERSION",
    "RCAEVAL_FIXTURE_CLASSIFICATION",
    "RCAEVAL_INDEX_RESOURCE_ID",
    "RCAEVAL_LICENSE_ID",
    "RCAEVAL_OPAQUE_SLOTS",
    "RCAEVAL_PIPELINE_ID",
    "RCAEVAL_RESOURCE_CONTRACTS",
    "RCAEVAL_RESOURCE_COUNT",
    "RCAEVAL_RESOURCE_IDS",
    "RCAEVAL_TELEMETRY_GROUPS",
    "RCAEVAL_TELEMETRY_RESOURCE_COUNT",
    "RCAEVAL_TELEMETRY_RESOURCE_IDS",
    "RCAEVAL_TOTAL_BYTES",
    "RCAEVAL_UPSTREAM_CLASSIFICATION",
    "RcaEvalResourceContract",
    "validate_frozen_rcaeval_contracts",
]
