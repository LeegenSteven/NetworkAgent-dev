"""Bounded, projection-only Parquet reads from already-held file handles."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass, fields
from typing import BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from .adapters import AdapterError


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COLUMN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_PARQUET_MAGIC = b"PAR1"


def _invalid() -> AdapterError:
    return AdapterError("adapter_invalid_input")


def _unsafe() -> AdapterError:
    return AdapterError("adapter_unsafe_field")


def _limit() -> AdapterError:
    return AdapterError("adapter_limit_exceeded")


def parquet_schema_fingerprint(schema: pa.Schema) -> str:
    """Return a stable digest of field names, types and nullability."""

    if not isinstance(schema, pa.Schema):
        raise _invalid()
    try:
        projection = [
            {
                "name": field.name,
                "nullable": field.nullable,
                "type": str(field.type),
            }
            for field in schema
        ]
        encoded = json.dumps(
            projection,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except AdapterError:
        raise
    except Exception as error:
        raise _invalid() from error
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ParquetLimits:
    """Independent caps applied before and during Parquet decoding."""

    max_file_bytes: int = 64 * 1024 * 1024
    max_footer_bytes: int = 2 * 1024 * 1024
    max_metadata_bytes: int = 2 * 1024 * 1024
    max_rows: int = 2_000_000
    max_row_groups: int = 128
    max_columns: int = 256
    max_row_group_rows: int = 500_000
    max_compressed_bytes: int = 64 * 1024 * 1024
    max_uncompressed_bytes: int = 256 * 1024 * 1024
    max_batch_rows: int = 4_096
    max_batch_bytes: int = 16 * 1024 * 1024
    max_decoded_bytes: int = 128 * 1024 * 1024
    max_string_bytes: int = 32 * 1024 * 1024
    thrift_string_size_limit: int = 2 * 1024 * 1024
    thrift_container_size_limit: int = 100_000

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 1:
                raise _invalid()


def _is_safe_primitive(data_type: pa.DataType) -> bool:
    if isinstance(data_type, pa.ExtensionType):
        return False
    return bool(
        pa.types.is_boolean(data_type)
        or pa.types.is_integer(data_type)
        or pa.types.is_floating(data_type)
        or pa.types.is_string(data_type)
        or pa.types.is_large_string(data_type)
    )


def _validate_flat_schema(schema: pa.Schema) -> None:
    if not isinstance(schema, pa.Schema) or len(schema) < 1:
        raise _invalid()
    names: list[str] = []
    for field in schema:
        if (
            type(field.name) is not str
            or _COLUMN_NAME.fullmatch(field.name) is None
            or not _is_safe_primitive(field.type)
        ):
            raise _unsafe()
        names.append(field.name)
    if len(names) != len(set(names)):
        raise _invalid()


@dataclass(frozen=True, slots=True)
class ParquetContract:
    """Exact audited schema and resource-shape contract for one artifact."""

    expected_schema: pa.Schema
    expected_schema_fingerprint: str
    projected_columns: tuple[str, ...]
    expected_rows: int
    expected_row_groups: int
    allowed_codecs: tuple[str, ...] = ("ZSTD",)
    expected_created_by: str | None = None
    expected_format_version: str | None = None
    expected_metadata_bytes: int | None = None
    expected_compressed_bytes: int | None = None
    expected_uncompressed_bytes: int | None = None
    limits: ParquetLimits = ParquetLimits()

    def __post_init__(self) -> None:
        _validate_flat_schema(self.expected_schema)
        if (
            type(self.expected_schema_fingerprint) is not str
            or _SHA256.fullmatch(self.expected_schema_fingerprint) is None
            or type(self.projected_columns) is not tuple
            or not self.projected_columns
            or any(type(item) is not str for item in self.projected_columns)
            or len(self.projected_columns) != len(set(self.projected_columns))
            or type(self.expected_rows) is not int
            or self.expected_rows < 1
            or type(self.expected_row_groups) is not int
            or self.expected_row_groups < 1
            or type(self.allowed_codecs) is not tuple
            or not self.allowed_codecs
            or any(
                type(item) is not str or item != item.upper() or not item
                for item in self.allowed_codecs
            )
            or len(self.allowed_codecs) != len(set(self.allowed_codecs))
            or (
                self.expected_created_by is not None
                and (
                    type(self.expected_created_by) is not str
                    or not self.expected_created_by
                    or len(self.expected_created_by) > 256
                )
            )
            or (
                self.expected_format_version is not None
                and (
                    type(self.expected_format_version) is not str
                    or not re.fullmatch(r"[0-9]+\.[0-9]+", self.expected_format_version)
                )
            )
            or any(
                value is not None and (type(value) is not int or value < 0)
                for value in (
                    self.expected_metadata_bytes,
                    self.expected_compressed_bytes,
                    self.expected_uncompressed_bytes,
                )
            )
            or type(self.limits) is not ParquetLimits
        ):
            raise _invalid()
        names = set(self.expected_schema.names)
        if any(item not in names for item in self.projected_columns):
            raise _unsafe()


def _normalized_contract(contract: object) -> ParquetContract:
    if type(contract) is not ParquetContract:
        raise _invalid()
    try:
        limits = ParquetLimits(
            **{
                field.name: getattr(contract.limits, field.name)
                for field in fields(ParquetLimits)
            }
        )
        return ParquetContract(
            expected_schema=contract.expected_schema,
            expected_schema_fingerprint=(contract.expected_schema_fingerprint),
            projected_columns=contract.projected_columns,
            expected_rows=contract.expected_rows,
            expected_row_groups=contract.expected_row_groups,
            allowed_codecs=contract.allowed_codecs,
            expected_created_by=contract.expected_created_by,
            expected_format_version=contract.expected_format_version,
            expected_metadata_bytes=contract.expected_metadata_bytes,
            expected_compressed_bytes=contract.expected_compressed_bytes,
            expected_uncompressed_bytes=contract.expected_uncompressed_bytes,
            limits=limits,
        )
    except AdapterError:
        raise
    except Exception as error:
        raise _invalid() from error


def _require_held_stream(stream: object) -> BinaryIO:
    if isinstance(
        stream,
        (str, bytes, bytearray, memoryview, os.PathLike),
    ):
        raise _unsafe()
    for method_name in ("read", "seek", "tell", "readable", "seekable"):
        if not callable(getattr(stream, method_name, None)):
            raise _unsafe()
    try:
        if bool(getattr(stream, "closed", False)):
            raise _invalid()
        if stream.readable() is not True or stream.seekable() is not True:
            raise _unsafe()
    except AdapterError:
        raise
    except Exception as error:
        raise _invalid() from error
    return stream  # type: ignore[return-value]


def _inspect_envelope(stream: BinaryIO, limits: ParquetLimits) -> int:
    try:
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        if type(size) is not int or size < 12:
            raise _invalid()
        if size > limits.max_file_bytes:
            raise _limit()
        stream.seek(0, io.SEEK_SET)
        leading_magic = stream.read(4)
        if type(leading_magic) is not bytes or leading_magic != _PARQUET_MAGIC:
            raise _invalid()
        stream.seek(size - 8, io.SEEK_SET)
        trailer = stream.read(8)
        if type(trailer) is not bytes or len(trailer) != 8:
            raise _invalid()
        if trailer[4:] != _PARQUET_MAGIC:
            raise _invalid()
        footer_size = int.from_bytes(trailer[:4], "little", signed=False)
        if footer_size < 1 or footer_size + 8 > size - 4:
            raise _invalid()
        if footer_size > limits.max_footer_bytes:
            raise _limit()
        stream.seek(0, io.SEEK_SET)
        return size
    except AdapterError:
        raise
    except MemoryError as error:
        raise _limit() from error
    except Exception as error:
        raise _invalid() from error


def _parquet_file_kwargs(limits: ParquetLimits) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "buffer_size": 0,
        "coerce_int96_timestamp_unit": None,
        "decryption_properties": None,
        "filesystem": None,
        "memory_map": False,
        "pre_buffer": False,
        "read_dictionary": None,
        "thrift_container_size_limit": limits.thrift_container_size_limit,
        "thrift_string_size_limit": limits.thrift_string_size_limit,
    }
    try:
        arrow_major = int(pa.__version__.split(".", maxsplit=1)[0])
    except (AttributeError, TypeError, ValueError) as error:
        raise _invalid() from error
    if arrow_major >= 21:
        kwargs["arrow_extensions_enabled"] = False
        kwargs["page_checksum_verification"] = True
    return kwargs


def _metadata_size(metadata) -> int:
    size = getattr(metadata, "serialized_size", None)
    if type(size) is not int or size < 0:
        raise _invalid()
    return size


def _validate_metadata(
    parquet_file,
    contract: ParquetContract,
    file_size: int,
) -> None:
    limits = contract.limits
    metadata = parquet_file.metadata
    actual_schema = parquet_file.schema_arrow
    if not isinstance(actual_schema, pa.Schema):
        raise _invalid()
    _validate_flat_schema(actual_schema)
    if len(actual_schema) > limits.max_columns:
        raise _limit()
    if not actual_schema.equals(contract.expected_schema, check_metadata=False):
        raise _unsafe()
    if parquet_schema_fingerprint(contract.expected_schema) != (
        contract.expected_schema_fingerprint
    ):
        raise _invalid()
    if parquet_schema_fingerprint(actual_schema) != (
        contract.expected_schema_fingerprint
    ):
        raise _unsafe()
    metadata_bytes = _metadata_size(metadata)
    if metadata_bytes > limits.max_metadata_bytes:
        raise _limit()
    if (
        contract.expected_metadata_bytes is not None
        and metadata_bytes != contract.expected_metadata_bytes
    ):
        raise _invalid()
    if (
        contract.expected_created_by is not None
        and getattr(metadata, "created_by", None) != contract.expected_created_by
    ):
        raise _invalid()
    if (
        contract.expected_format_version is not None
        and getattr(metadata, "format_version", None)
        != contract.expected_format_version
    ):
        raise _invalid()
    if (
        type(metadata.num_rows) is not int
        or type(metadata.num_row_groups) is not int
        or type(metadata.num_columns) is not int
    ):
        raise _invalid()
    if metadata.num_rows > limits.max_rows:
        raise _limit()
    if metadata.num_row_groups > limits.max_row_groups:
        raise _limit()
    if metadata.num_columns > limits.max_columns:
        raise _limit()
    if (
        metadata.num_rows != contract.expected_rows
        or metadata.num_row_groups != contract.expected_row_groups
        or metadata.num_columns != len(contract.expected_schema)
    ):
        raise _invalid()

    compressed = 0
    uncompressed = 0
    expected_names = contract.expected_schema.names
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        if row_group.num_rows > limits.max_row_group_rows:
            raise _limit()
        if row_group.num_columns != len(expected_names):
            raise _invalid()
        for column_index, expected_name in enumerate(expected_names):
            column = row_group.column(column_index)
            path = getattr(column, "path_in_schema", None)
            if path != expected_name:
                raise _unsafe()
            codec = getattr(column, "compression", None)
            allowed_codecs = contract.allowed_codecs
            if type(codec) is not str or codec.upper() not in allowed_codecs:
                raise _unsafe()
            compressed_size = getattr(column, "total_compressed_size", None)
            uncompressed_size = getattr(
                column,
                "total_uncompressed_size",
                None,
            )
            if (
                type(compressed_size) is not int
                or compressed_size < 0
                or type(uncompressed_size) is not int
                or uncompressed_size < 0
            ):
                raise _invalid()
            compressed += compressed_size
            uncompressed += uncompressed_size
            if compressed > limits.max_compressed_bytes:
                raise _limit()
            if uncompressed > limits.max_uncompressed_bytes:
                raise _limit()
    if compressed > file_size:
        raise _invalid()
    if (
        contract.expected_compressed_bytes is not None
        and compressed != contract.expected_compressed_bytes
    ):
        raise _invalid()
    if (
        contract.expected_uncompressed_bytes is not None
        and uncompressed != contract.expected_uncompressed_bytes
    ):
        raise _invalid()


def _validate_batch(
    batch: pa.RecordBatch,
    contract: ParquetContract,
) -> tuple[int, int]:
    limits = contract.limits
    if not isinstance(batch, pa.RecordBatch) or batch.num_rows < 1:
        raise _invalid()
    if batch.num_rows > limits.max_batch_rows:
        raise _limit()
    if batch.schema.names != list(contract.projected_columns):
        raise _unsafe()
    for name, actual_field in zip(
        contract.projected_columns,
        batch.schema,
        strict=True,
    ):
        expected_field = contract.expected_schema.field(name)
        if not actual_field.equals(expected_field, check_metadata=True):
            raise _unsafe()
    batch_bytes = batch.nbytes
    if type(batch_bytes) is not int or batch_bytes < 0:
        raise _invalid()
    if batch_bytes > limits.max_batch_bytes:
        raise _limit()

    string_bytes = 0
    for array in batch.columns:
        data_type = array.type
        if pa.types.is_floating(data_type):
            for value in array.to_pylist():
                if value is not None and not math.isfinite(value):
                    raise _invalid()
        else:
            is_string = pa.types.is_string(data_type)
            is_large_string = pa.types.is_large_string(data_type)
            if not (is_string or is_large_string):
                continue
            buffers = array.buffers()
            data_buffer = buffers[-1] if buffers else None
            if data_buffer is not None:
                string_bytes += data_buffer.size
            if string_bytes > limits.max_string_bytes:
                raise _limit()
    return batch_bytes, string_bytes


def _read_parquet_batches(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
) -> tuple[pa.RecordBatch, ...]:
    """Read only the audited projection without taking a path or closing it."""

    contract = _normalized_contract(contract)
    source = _require_held_stream(stream)
    file_size = _inspect_envelope(source, contract.limits)
    try:
        parquet_file = pq.ParquetFile(
            source,
            **_parquet_file_kwargs(contract.limits),
        )
        _validate_metadata(parquet_file, contract, file_size)
        batches: list[pa.RecordBatch] = []
        decoded_bytes = 0
        string_bytes = 0
        rows = 0
        for batch in parquet_file.iter_batches(
            batch_size=contract.limits.max_batch_rows,
            columns=list(contract.projected_columns),
            use_threads=False,
            use_pandas_metadata=False,
        ):
            batch_bytes, batch_string_bytes = _validate_batch(batch, contract)
            decoded_bytes += batch_bytes
            string_bytes += batch_string_bytes
            rows += batch.num_rows
            if decoded_bytes > contract.limits.max_decoded_bytes:
                raise _limit()
            if string_bytes > contract.limits.max_string_bytes:
                raise _limit()
            if rows > contract.expected_rows:
                raise _invalid()
            batches.append(batch)
        if rows != contract.expected_rows or not batches:
            raise _invalid()
        return tuple(batches)
    except AdapterError:
        raise
    except MemoryError as error:
        raise _limit() from error
    except Exception as error:
        raise _invalid() from error
    finally:
        try:
            source.seek(0, io.SEEK_SET)
        except Exception:
            pass


def read_parquet_batches(
    stream: BinaryIO,
    *,
    contract: ParquetContract,
) -> tuple[pa.RecordBatch, ...]:
    """Read a held stream behind a fixed, fully detached error boundary."""

    failure_code = "adapter_invalid_input"
    try:
        return _read_parquet_batches(stream, contract=contract)
    except AdapterError as error:
        failure_code = error.code
    except MemoryError:
        failure_code = "adapter_limit_exceeded"
    except Exception:
        failure_code = "adapter_invalid_input"
    raise AdapterError(failure_code) from None  # type: ignore[arg-type]


__all__ = [
    "ParquetContract",
    "ParquetLimits",
    "parquet_schema_fingerprint",
    "read_parquet_batches",
]
