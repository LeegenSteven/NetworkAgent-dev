"""Bounded local-only adapters for approved open telecom datasets.

This module never downloads data and never writes derived artifacts.  Callers
must first obtain and verify an artifact through the ``TelcoLab`` workspace (or
explicitly provide a local test CSV), then pass that local path here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from pydantic import ValidationError

from telco_domain import Technology

from .errors import LabError
from .safe_json import StrictJsonError, load_strict_json
from .schema import (
    MAX_BUNDLE_EPISODES,
    MAX_BUNDLE_OBSERVATIONS,
    MAX_BUNDLE_SERIALIZED_BYTES,
    LabBundle,
    LabBundleManifest,
    LabEpisode,
    LabObservation,
    PredictedEpisode,
    canonical_json_bytes,
    compute_bundle_content_sha256,
    stable_content_id,
)


BUBBLERAN_DATASET_ID = "bubbleran-persistent-interference"
BUBBLERAN_DATASET_VERSION = "fa4e3333855d64474e710bc5bebf11a9ec075e0b"
BUBBLERAN_SOURCE_LICENSE = "CC-BY-SA-4.0"
BUBBLERAN_CSV_ADAPTER_ID = "bubbleran_persistent_interference_v1"
BUBBLERAN_ALERT_ADAPTER_ID = "bubbleran_alerts_v1"
ADAPTER_VERSION = "1.0"

MAX_INPUT_CSV_BYTES = 128 * 1024 * 1024
MAX_INPUT_ALERT_BYTES = 4 * 1024 * 1024
MAX_CSV_COLUMNS = 256
MAX_CSV_FIELD_BYTES = 64 * 1024
MAX_CSV_ROW_BYTES = 256 * 1024
MAX_ALERT_ITEMS = 10_000
MAX_JSON_DEPTH = 8
MAX_GNB_IDENTIFIER = (1 << 36) - 1


@dataclass(frozen=True, slots=True)
class MetricColumn:
    """Explicit mapping from one upstream column to a safe canonical KPI."""

    metric_name: str
    unit: str


_COLUMN_MAP = {
    "mac_bsr": MetricColumn("ran.mac.buffer_status_report", "count"),
    "mac_dl_aggr_prb": MetricColumn("ran.mac.dl_aggregated_prb", "PRB"),
    "mac_ul_aggr_prb": MetricColumn("ran.mac.ul_aggregated_prb", "PRB"),
    "mac_dl_aggr_retx_prb": MetricColumn(
        "ran.mac.dl_retransmission_prb", "PRB"
    ),
    "mac_ul_aggr_retx_prb": MetricColumn(
        "ran.mac.ul_retransmission_prb", "PRB"
    ),
    "mac_dl_sched_rb": MetricColumn("ran.mac.dl_scheduled_rb", "PRB"),
    "mac_ul_sched_rb": MetricColumn("ran.mac.ul_scheduled_rb", "PRB"),
    "mac_dl_bler": MetricColumn("ran.mac.dl_bler", "ratio"),
    "mac_ul_bler": MetricColumn("ran.mac.ul_bler", "ratio"),
    "mac_dl_mcs1": MetricColumn("ran.mac.dl_mcs", "index"),
    "mac_ul_mcs1": MetricColumn("ran.mac.ul_mcs", "index"),
    "mac_ul_harq2": MetricColumn("ran.mac.ul_harq_process_2", "count"),
    "mac_ul_harq3": MetricColumn("ran.mac.ul_harq_process_3", "count"),
    "mac_pucch_snr": MetricColumn("ran.mac.pucch_snr", "dB"),
    "mac_pusch_snr": MetricColumn("ran.mac.pusch_snr", "dB"),
    "mac_phr": MetricColumn("ran.mac.power_headroom", "dB"),
    "mac_wb_cqi": MetricColumn("ran.mac.wideband_cqi", "index"),
    "mac_dlsch_errors": MetricColumn("ran.mac.dl_shared_channel_errors", "count"),
    "mac_ulsch_errors": MetricColumn("ran.mac.ul_shared_channel_errors", "count"),
    "rlc_txpdu_retx_pkts": MetricColumn(
        "ran.rlc.tx_retransmitted_pdus", "count"
    ),
    "rlc_txbuf_occ_bytes": MetricColumn("ran.rlc.tx_buffer_occupancy", "bytes"),
    "rlc_rxpdu_dd_pkts": MetricColumn("ran.rlc.rx_dropped_pdus", "count"),
    "rlc_rxbuf_occ_bytes": MetricColumn("ran.rlc.rx_buffer_occupancy", "bytes"),
    "rlc_txsdu_avg_time_to_tx": MetricColumn(
        "ran.rlc.average_sdu_tx_delay", "microseconds"
    ),
    "rlc_txsdu_wt_us": MetricColumn(
        "ran.rlc.head_of_line_sdu_delay", "microseconds"
    ),
    "pdcp_rxpdu_oo_pkts": MetricColumn(
        "ran.pdcp.rx_out_of_order_pdus", "count"
    ),
    "pdcp_rxpdu_dd_pkts": MetricColumn("ran.pdcp.rx_discarded_pdus", "count"),
    "kpm_drb_ue_thp_dl": MetricColumn("ran.kpm.dl_throughput", "bps"),
    "kpm_drb_ue_thp_ul": MetricColumn("ran.kpm.ul_throughput", "bps"),
    "kpm_rru_prb_used_dl": MetricColumn("ran.kpm.dl_used_prb", "PRB"),
    "kpm_rru_prb_used_ul": MetricColumn("ran.kpm.ul_used_prb", "PRB"),
    "kpm_rru_prb_tot_dl": MetricColumn("ran.kpm.dl_total_prb", "PRB"),
    "kpm_rru_prb_tot_ul": MetricColumn("ran.kpm.ul_total_prb", "PRB"),
}
BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP: Mapping[str, MetricColumn] = (
    MappingProxyType(_COLUMN_MAP)
)

_BASE_COLUMNS = frozenset(
    {
        "timestamp",
        "timestamp_iso",
        "ran_ue_id",
        "e2node_nb_id",
        "persistent_anomaly",
    }
)
_REQUIRED_COLUMNS = _BASE_COLUMNS | frozenset(_COLUMN_MAP)
_IGNORED_UPSTREAM_METRIC_COLUMNS = frozenset(
    {
        "kpm_carr_mupdschmcs_dist_bin",
        "kpm_carr_wbcqi_dist_bin",
        "kpm_drb_pdcp_sdu_volume_dl",
        "kpm_drb_pdcp_sdu_volume_ul",
        "kpm_drb_rlc_sdu_delay_dl",
        "kpm_rru_prb_avail_dl",
        "kpm_rru_prb_avail_ul",
        "mac_dl_aggr_bytes_sdus",
        "mac_dl_aggr_sdus",
        "mac_dl_aggr_tbs",
        "mac_dl_curr_tbs",
        "mac_dl_harq0",
        "mac_dl_harq1",
        "mac_dl_harq2",
        "mac_dl_harq3",
        "mac_dl_mcs2",
        "mac_dl_num_harq",
        "mac_frame",
        "mac_slot",
        "mac_ul_aggr_bytes_sdus",
        "mac_ul_aggr_sdus",
        "mac_ul_aggr_tbs",
        "mac_ul_curr_tbs",
        "mac_ul_harq0",
        "mac_ul_harq1",
        "mac_ul_mcs2",
        "mac_ul_num_harq",
        "pdcp_rxpdu_bytes",
        "pdcp_rxpdu_dd_bytes",
        "pdcp_rxpdu_oo_bytes",
        "pdcp_rxpdu_pkts",
        "pdcp_rxpdu_ro_count",
        "pdcp_rxpdu_sn",
        "pdcp_rxsdu_bytes",
        "pdcp_rxsdu_pkts",
        "pdcp_txpdu_bytes",
        "pdcp_txpdu_pkts",
        "pdcp_txpdu_sn",
        "pdcp_txsdu_bytes",
        "pdcp_txsdu_pkts",
        "rlc_rxbuf_occ_pkts",
        "rlc_rxpdu_bytes",
        "rlc_rxpdu_dd_bytes",
        "rlc_rxpdu_dup_bytes",
        "rlc_rxpdu_dup_pkts",
        "rlc_rxpdu_ow_bytes",
        "rlc_rxpdu_ow_pkts",
        "rlc_rxpdu_pkts",
        "rlc_rxpdu_status_bytes",
        "rlc_rxpdu_status_pkts",
        "rlc_rxsdu_bytes",
        "rlc_rxsdu_dd_bytes",
        "rlc_rxsdu_dd_pkts",
        "rlc_rxsdu_pkts",
        "rlc_txbuf_occ_pkts",
        "rlc_txpdu_bytes",
        "rlc_txpdu_dd_bytes",
        "rlc_txpdu_dd_pkts",
        "rlc_txpdu_pkts",
        "rlc_txpdu_retx_bytes",
        "rlc_txpdu_segmented",
        "rlc_txpdu_status_bytes",
        "rlc_txpdu_status_pkts",
        "rlc_txpdu_wt_ms",
        "rlc_txsdu_bytes",
        "rlc_txsdu_pkts",
    }
)
_MINIMAL_ALLOWED_COLUMNS = _REQUIRED_COLUMNS | frozenset({""})
_UPSTREAM_ALLOWED_COLUMNS = (
    _MINIMAL_ALLOWED_COLUMNS | _IGNORED_UPSTREAM_METRIC_COLUMNS
)
_ALLOWED_SCHEMA_VARIANTS = frozenset(
    {
        _REQUIRED_COLUMNS,
        _MINIMAL_ALLOWED_COLUMNS,
        _REQUIRED_COLUMNS | _IGNORED_UPSTREAM_METRIC_COLUMNS,
        _UPSTREAM_ALLOWED_COLUMNS,
    }
)
_DECIMAL_GNB = re.compile(r"^[0-9]{1,11}$")
_MISSING_VALUES = frozenset({"", "null", "none"})


class AdapterError(LabError):
    """An adapter failure with a fixed, non-disclosing message and code."""

    def __init__(self, code: Literal[
        "adapter_invalid_input",
        "adapter_unsafe_field",
        "adapter_limit_exceeded",
    ]) -> None:
        super().__init__(code)


def _invalid() -> AdapterError:
    return AdapterError("adapter_invalid_input")


def _unsafe() -> AdapterError:
    return AdapterError("adapter_unsafe_field")


def _limit() -> AdapterError:
    return AdapterError("adapter_limit_exceeded")


def _assert_local_file(value: str | Path, *, suffix: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise _invalid()
    raw = str(value)
    if "://" in raw or "\x00" in raw:
        raise _unsafe()
    path = Path(value)
    try:
        if path.suffix.casefold() != suffix or not path.is_file():
            raise _invalid()
    except OSError as error:
        raise _invalid() from error
    return path


def _stream_sha256(path: Path, *, byte_limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > byte_limit:
                    raise _limit()
                digest.update(chunk)
    except AdapterError:
        raise
    except OSError as error:
        raise _invalid() from error
    if size == 0:
        raise _invalid()
    return digest.hexdigest(), size


def _bounded_positive_limit(value: int | None, *, hard_limit: int) -> int:
    if value is None:
        return hard_limit
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid()
    if value < 1 or value > hard_limit:
        raise _limit()
    return value


def _parse_float(value: str) -> float | None:
    normalized = value.strip()
    if normalized.casefold() in _MISSING_VALUES:
        return None
    try:
        result = float(normalized)
    except (TypeError, ValueError) as error:
        raise _invalid() from error
    if not math.isfinite(result):
        raise _invalid()
    return result


def _parse_label(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true"}:
        return True
    if normalized in {"0", "false"}:
        return False
    raise _invalid()


def _parse_csv_time(unix_value: str, iso_value: str) -> datetime:
    raw_unix = unix_value.strip()
    if not re.fullmatch(r"[0-9]{1,11}", raw_unix):
        raise _invalid()
    try:
        unix_time = datetime.fromtimestamp(int(raw_unix, 10), tz=UTC)
        raw_iso = iso_value.strip()
        if raw_iso.endswith(("Z", "z")):
            raw_iso = f"{raw_iso[:-1]}+00:00"
        iso_time = datetime.fromisoformat(raw_iso)
    except (OverflowError, OSError, ValueError) as error:
        raise _invalid() from error
    if iso_time.tzinfo is None or iso_time.utcoffset() is None:
        iso_time = iso_time.replace(tzinfo=UTC)
    else:
        iso_time = iso_time.astimezone(UTC)
    if iso_time != unix_time or not 2000 <= unix_time.year <= 2100:
        raise _invalid()
    return unix_time


def _safe_resource_id(
    raw_gnb_id: str,
    *,
    dataset_id: str,
    dataset_version: str,
) -> str:
    normalized = raw_gnb_id.strip()
    if _DECIMAL_GNB.fullmatch(normalized) is None:
        raise _unsafe()
    parsed = int(normalized, 10)
    if parsed > MAX_GNB_IDENTIFIER:
        raise _unsafe()
    canonical = str(parsed)
    digest = hashlib.sha256()
    digest.update(b"telco-lab:safe-gnb-alias:v1\0")
    digest.update(dataset_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(dataset_version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical.encode("ascii"))
    return f"lab:5g-sa:gnb:{digest.hexdigest()[:24]}"


@dataclass(slots=True)
class _OpenEpisode:
    resource_id: str
    window_start: datetime
    window_end: datetime
    sample_count: int
    first_observation_id: str
    last_observation_id: str


def _close_episode(
    state: _OpenEpisode,
    *,
    dataset_id: str,
    dataset_version: str,
    source_sha256: str,
) -> LabEpisode:
    payload = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source_artifact_sha256": source_sha256,
        "resource_id": state.resource_id,
        "label": "persistent_interference",
        "window_start": state.window_start.isoformat(),
        "window_end": state.window_end.isoformat(),
        "sample_count": state.sample_count,
        "first_observation_id": state.first_observation_id,
        "last_observation_id": state.last_observation_id,
    }
    return LabEpisode(
        episode_id=stable_content_id("truth", payload),
        **payload,
    )


def adapt_bubbleran_persistent_interference_csv(
    path: str | Path,
    *,
    dataset_id: str = BUBBLERAN_DATASET_ID,
    dataset_version: str = BUBBLERAN_DATASET_VERSION,
    max_observations: int | None = None,
    max_episodes: int | None = None,
    max_output_bytes: int | None = None,
) -> LabBundle:
    """Stream one local BubbleRAN CSV into a bounded safe lab bundle.

    ``ran_ue_id`` is required only to recognize the upstream schema.  Its value
    is never read into an output model, identifier, exception, or manifest.
    The ``persistent_anomaly`` label is likewise excluded from observations and
    used only to construct maximally contiguous ground-truth episodes.
    """

    csv_path = _assert_local_file(path, suffix=".csv")
    observation_limit = _bounded_positive_limit(
        max_observations,
        hard_limit=MAX_BUNDLE_OBSERVATIONS,
    )
    episode_limit = _bounded_positive_limit(
        max_episodes,
        hard_limit=MAX_BUNDLE_EPISODES,
    )
    output_limit = _bounded_positive_limit(
        max_output_bytes,
        hard_limit=MAX_BUNDLE_SERIALIZED_BYTES,
    )
    source_sha256, _ = _stream_sha256(csv_path, byte_limit=MAX_INPUT_CSV_BYTES)

    observations: list[LabObservation] = []
    episodes: list[LabEpisode] = []
    open_episodes: dict[str, _OpenEpisode] = {}
    previous_time: dict[str, datetime] = {}
    output_bytes = 0

    def append_episode(state: _OpenEpisode) -> None:
        nonlocal output_bytes
        if len(episodes) >= episode_limit:
            raise _limit()
        episode = _close_episode(
            state,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source_sha256=source_sha256,
        )
        output_bytes += len(canonical_json_bytes(episode))
        if output_bytes > output_limit:
            raise _limit()
        episodes.append(episode)

    try:
        with csv_path.open("rb") as binary_stream:
            text_stream = io.TextIOWrapper(
                binary_stream,
                encoding="utf-8-sig",
                errors="strict",
                newline="",
            )
            reader = csv.reader(text_stream, strict=True)
            try:
                raw_headers = next(reader)
            except StopIteration as error:
                raise _invalid() from error
            if not raw_headers or len(raw_headers) > MAX_CSV_COLUMNS:
                raise _limit()
            headers = tuple(item.strip() for item in raw_headers)
            folded = tuple(item.casefold() for item in headers)
            if len(folded) != len(set(folded)):
                raise _invalid()
            if any(
                len(item.encode("utf-8")) > MAX_CSV_FIELD_BYTES
                or any(ord(character) < 32 for character in item)
                for item in headers
            ):
                raise _invalid()
            header_set = frozenset(headers)
            if header_set not in _ALLOWED_SCHEMA_VARIANTS:
                if _REQUIRED_COLUMNS.issubset(header_set):
                    raise _unsafe()
                raise _invalid()
            positions = {name: index for index, name in enumerate(headers)}

            for record_number, row in enumerate(reader, start=2):
                if len(observations) >= observation_limit:
                    raise _limit()
                if len(row) != len(headers):
                    raise _invalid()
                if tuple(item.strip() for item in row) == headers:
                    raise _invalid()
                if row[positions["timestamp"]].strip().casefold() == "timestamp":
                    raise _invalid()
                field_sizes = tuple(len(item.encode("utf-8")) for item in row)
                if (
                    any(size > MAX_CSV_FIELD_BYTES for size in field_sizes)
                    or sum(field_sizes) > MAX_CSV_ROW_BYTES
                ):
                    raise _limit()

                for ignored_name in _IGNORED_UPSTREAM_METRIC_COLUMNS & header_set:
                    _parse_float(row[positions[ignored_name]])

                observed_at = _parse_csv_time(
                    row[positions["timestamp"]],
                    row[positions["timestamp_iso"]],
                )
                resource_id = _safe_resource_id(
                    row[positions["e2node_nb_id"]],
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                )
                if (
                    resource_id in previous_time
                    and observed_at <= previous_time[resource_id]
                ):
                    raise _invalid()

                metrics: dict[str, float] = {}
                units: dict[str, str] = {}
                missing_metric = False
                for source_name, mapping in _COLUMN_MAP.items():
                    value = _parse_float(row[positions[source_name]])
                    if value is None:
                        missing_metric = True
                        continue
                    metrics[mapping.metric_name] = value
                    units[mapping.metric_name] = mapping.unit
                if not metrics:
                    raise _invalid()
                metrics = dict(sorted(metrics.items()))
                units = dict(sorted(units.items()))
                quality_flags = (
                    ("MISSING_METRIC_VALUES",) if missing_metric else ()
                )
                identity_payload = {
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "source_artifact_sha256": source_sha256,
                    "source_row_number": record_number,
                    "observed_at": observed_at.isoformat(),
                    "resource_id": resource_id,
                    "technology": Technology.FIVE_G_SA.value,
                    "metrics": metrics,
                    "units": units,
                    "quality_flags": quality_flags,
                }
                observation = LabObservation(
                    observation_id=stable_content_id("obs", identity_payload),
                    **identity_payload,
                )
                output_bytes += len(canonical_json_bytes(observation))
                if output_bytes > output_limit:
                    raise _limit()
                observations.append(observation)

                is_anomalous = _parse_label(
                    row[positions["persistent_anomaly"]]
                )
                current = open_episodes.get(resource_id)
                if is_anomalous:
                    if current is not None and observed_at == (
                        current.window_end + timedelta(seconds=1)
                    ):
                        current.window_end = observed_at
                        current.sample_count += 1
                        current.last_observation_id = observation.observation_id
                    else:
                        if current is not None:
                            append_episode(current)
                        open_episodes[resource_id] = _OpenEpisode(
                            resource_id=resource_id,
                            window_start=observed_at,
                            window_end=observed_at,
                            sample_count=1,
                            first_observation_id=observation.observation_id,
                            last_observation_id=observation.observation_id,
                        )
                elif current is not None:
                    append_episode(current)
                    del open_episodes[resource_id]
                previous_time[resource_id] = observed_at
    except AdapterError:
        raise
    except (csv.Error, UnicodeError, OSError) as error:
        raise _invalid() from error
    except ValidationError as error:
        raise _unsafe() from error

    for resource_id in sorted(open_episodes):
        append_episode(open_episodes[resource_id])
    if not observations:
        raise _invalid()

    second_sha256, _ = _stream_sha256(csv_path, byte_limit=MAX_INPUT_CSV_BYTES)
    if second_sha256 != source_sha256:
        raise _invalid()

    ordered_episodes = tuple(
        sorted(
            episodes,
            key=lambda item: (item.window_start, item.resource_id, item.episode_id),
        )
    )
    observation_items = tuple(observations)
    content_sha256 = compute_bundle_content_sha256(
        observation_items,
        ordered_episodes,
    )
    manifest_payload = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source_artifact_sha256": source_sha256,
        "source_license": BUBBLERAN_SOURCE_LICENSE,
        "adapter_id": BUBBLERAN_CSV_ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "content_hash_algorithm": "sha256-canonical-json-lines-v1",
        "content_sha256": content_sha256,
        "observation_count": len(observation_items),
        "ground_truth_episode_count": len(ordered_episodes),
        "window_start": min(item.observed_at for item in observation_items),
        "window_end": max(item.observed_at for item in observation_items),
        "resource_ids": tuple(
            sorted({item.resource_id for item in observation_items})
        ),
        "metric_names": tuple(
            sorted({name for item in observation_items for name in item.metrics})
        ),
    }
    try:
        manifest = LabBundleManifest(
            bundle_id=stable_content_id("bundle", manifest_payload),
            **manifest_payload,
        )
        bundle = LabBundle(
            manifest=manifest,
            observations=observation_items,
            ground_truth_episodes=ordered_episodes,
        )
    except ValidationError as error:
        raise _unsafe() from error
    if (
        output_bytes + len(canonical_json_bytes(manifest))
        > output_limit
    ):
        raise _limit()
    return bundle


class BubbleRanPersistentInterferenceAdapter:
    """Object-oriented entry point used by adapter registries and applications."""

    adapter_id = BUBBLERAN_CSV_ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    dataset_id = BUBBLERAN_DATASET_ID
    dataset_version = BUBBLERAN_DATASET_VERSION

    def adapt(
        self,
        path: str | Path,
        *,
        max_observations: int | None = None,
        max_episodes: int | None = None,
        max_output_bytes: int | None = None,
    ) -> LabBundle:
        return adapt_bubbleran_persistent_interference_csv(
            path,
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            max_observations=max_observations,
            max_episodes=max_episodes,
            max_output_bytes=max_output_bytes,
        )


def _exact_object(value: Any, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise _invalid()
    return value


def _finite_number(value: Any, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid()
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise _invalid()
    return result


def _parse_alert_time(value: Any, *, naive_utc: bool) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise _invalid()
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise _invalid() from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not naive_utc:
            raise _invalid()
        parsed = parsed.replace(tzinfo=UTC)
    elif not naive_utc and parsed.utcoffset() != timedelta(0):
        raise _invalid()
    return parsed.astimezone(UTC)


def adapt_bubbleran_alerts(
    path: str | Path,
    *,
    resource_id: str,
    dataset_id: str = BUBBLERAN_DATASET_ID,
    dataset_version: str = BUBBLERAN_DATASET_VERSION,
    max_predictions: int | None = None,
    max_output_bytes: int | None = None,
) -> tuple[PredictedEpisode, ...]:
    """Parse the upstream BubbleRAN alert artifact as detector predictions.

    The JSON artifact is a detector output, not dataset ground truth.  Only a
    fixed set of numeric fields and allowlisted feature names is accepted; free
    text, identifiers, and unknown fields are rejected and never propagated.
    """

    json_path = _assert_local_file(path, suffix=".json")
    prediction_limit = _bounded_positive_limit(
        max_predictions,
        hard_limit=MAX_ALERT_ITEMS,
    )
    output_limit = _bounded_positive_limit(
        max_output_bytes,
        hard_limit=MAX_BUNDLE_SERIALIZED_BYTES,
    )
    source_sha256, size = _stream_sha256(
        json_path,
        byte_limit=MAX_INPUT_ALERT_BYTES,
    )
    try:
        raw_bytes = json_path.read_bytes()
        if len(raw_bytes) != size:
            raise _invalid()
        payload = load_strict_json(
            raw_bytes,
            max_bytes=MAX_INPUT_ALERT_BYTES,
            max_depth=MAX_JSON_DEPTH,
        )
    except AdapterError:
        raise
    except (UnicodeError, OSError, StrictJsonError) as error:
        raise _invalid() from error
    if not isinstance(payload, list):
        raise _invalid()
    if len(payload) > MAX_ALERT_ITEMS:
        raise _limit()

    root_keys = frozenset(
        {"timestamp_utc", "chunk_start", "chunk_end", "model", "anomaly", "top_features"}
    )
    model_keys = frozenset({"type", "window_sec"})
    anomaly_keys = frozenset(
        {"score", "is_anomalous", "severity_ratio", "violations", "violated_features"}
    )
    feature_keys = frozenset(
        {"feature_id", "reconstruction_error", "threshold", "kpi_severity"}
    )

    predictions: list[PredictedEpisode] = []
    output_bytes = 0
    for item_number, raw_item in enumerate(payload, start=1):
        item = _exact_object(raw_item, root_keys)
        model = _exact_object(item["model"], model_keys)
        anomaly = _exact_object(item["anomaly"], anomaly_keys)
        top_features = item["top_features"]
        if (
            model["type"] != "LSTM_Autoencoder"
            or isinstance(model["window_sec"], bool)
            or model["window_sec"] != 60
            or not isinstance(top_features, list)
            or len(top_features) > len(_COLUMN_MAP)
        ):
            raise _invalid()

        detected_at = _parse_alert_time(item["timestamp_utc"], naive_utc=False)
        window_start = _parse_alert_time(item["chunk_start"], naive_utc=True)
        window_end = _parse_alert_time(item["chunk_end"], naive_utc=True)
        if window_end < window_start or detected_at < window_end:
            raise _invalid()

        is_anomalous = anomaly["is_anomalous"]
        violations = anomaly["violations"]
        raw_features = anomaly["violated_features"]
        if (
            not isinstance(is_anomalous, bool)
            or isinstance(violations, bool)
            or not isinstance(violations, int)
            or violations < 0
            or not isinstance(raw_features, list)
            or len(raw_features) > len(_COLUMN_MAP)
            or violations != len(raw_features)
            or len(raw_features) != len(set(raw_features))
        ):
            raise _invalid()
        _finite_number(anomaly["score"], nonnegative=True)
        _finite_number(anomaly["severity_ratio"], nonnegative=True)
        if any(not isinstance(name, str) or name not in _COLUMN_MAP for name in raw_features):
            raise _unsafe()

        top_feature_ids: list[str] = []
        for raw_feature in top_features:
            feature = _exact_object(raw_feature, feature_keys)
            feature_id = feature["feature_id"]
            if not isinstance(feature_id, str) or feature_id not in _COLUMN_MAP:
                raise _unsafe()
            top_feature_ids.append(feature_id)
            _finite_number(feature["reconstruction_error"], nonnegative=True)
            _finite_number(feature["threshold"], nonnegative=True)
            _finite_number(feature["kpi_severity"], nonnegative=True)
        if len(top_feature_ids) != len(set(top_feature_ids)) or set(
            top_feature_ids
        ) != set(raw_features):
            raise _invalid()
        if not is_anomalous:
            if violations != 0 or raw_features or top_features:
                raise _invalid()
            continue
        if len(predictions) >= prediction_limit:
            raise _limit()

        canonical_features = tuple(
            sorted(_COLUMN_MAP[name].metric_name for name in raw_features)
        )
        prediction_payload = {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "source_artifact_sha256": source_sha256,
            "source_item_number": item_number,
            "resource_id": resource_id,
            "label": "persistent_interference",
            "window_start": window_start,
            "window_end": window_end,
            "detected_at": detected_at,
            "detector_id": BUBBLERAN_ALERT_ADAPTER_ID,
            "score": _finite_number(anomaly["score"], nonnegative=True),
            "features": canonical_features,
        }
        identity_payload = {
            **prediction_payload,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "detected_at": detected_at.isoformat(),
        }
        try:
            prediction = PredictedEpisode(
                prediction_id=stable_content_id("pred", identity_payload),
                **prediction_payload,
            )
        except ValidationError as error:
            raise _unsafe() from error
        output_bytes += len(canonical_json_bytes(prediction))
        if output_bytes > output_limit:
            raise _limit()
        predictions.append(prediction)

    second_sha256, _ = _stream_sha256(
        json_path,
        byte_limit=MAX_INPUT_ALERT_BYTES,
    )
    if second_sha256 != source_sha256:
        raise _invalid()
    prediction_ids = tuple(item.prediction_id for item in predictions)
    if len(prediction_ids) != len(set(prediction_ids)):
        raise _invalid()
    return tuple(predictions)


class BubbleRanAlertsAdapter:
    """Object-oriented entry point for the separately typed alert artifact."""

    adapter_id = BUBBLERAN_ALERT_ADAPTER_ID
    adapter_version = ADAPTER_VERSION
    dataset_id = BUBBLERAN_DATASET_ID
    dataset_version = BUBBLERAN_DATASET_VERSION

    def adapt(
        self,
        path: str | Path,
        *,
        resource_id: str,
        max_predictions: int | None = None,
        max_output_bytes: int | None = None,
    ) -> tuple[PredictedEpisode, ...]:
        return adapt_bubbleran_alerts(
            path,
            resource_id=resource_id,
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            max_predictions=max_predictions,
            max_output_bytes=max_output_bytes,
        )


__all__ = [
    "ADAPTER_VERSION",
    "AdapterError",
    "BUBBLERAN_ALERT_ADAPTER_ID",
    "BUBBLERAN_CSV_ADAPTER_ID",
    "BUBBLERAN_DATASET_ID",
    "BUBBLERAN_DATASET_VERSION",
    "BUBBLERAN_PERSISTENT_INTERFERENCE_COLUMN_MAP",
    "BUBBLERAN_SOURCE_LICENSE",
    "BubbleRanAlertsAdapter",
    "BubbleRanPersistentInterferenceAdapter",
    "MAX_ALERT_ITEMS",
    "MAX_CSV_COLUMNS",
    "MAX_CSV_FIELD_BYTES",
    "MAX_CSV_ROW_BYTES",
    "MAX_INPUT_ALERT_BYTES",
    "MAX_INPUT_CSV_BYTES",
    "MetricColumn",
    "adapt_bubbleran_alerts",
    "adapt_bubbleran_persistent_interference_csv",
]
