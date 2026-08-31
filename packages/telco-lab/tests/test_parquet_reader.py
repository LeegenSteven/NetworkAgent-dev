from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import telco_lab.parquet_reader as reader_module
from telco_lab.adapters import AdapterError
from telco_lab.parquet_reader import (
    ParquetContract,
    ParquetLimits,
    parquet_schema_fingerprint,
    read_parquet_batches,
)


def _parquet_bytes(
    table: pa.Table,
    *,
    compression: str = "snappy",
    row_group_size: int | None = None,
) -> bytes:
    stream = io.BytesIO()
    pq.write_table(
        table,
        stream,
        compression=compression,
        row_group_size=row_group_size,
    )
    return stream.getvalue()


def _contract(
    table: pa.Table,
    *,
    projected_columns: tuple[str, ...] | None = None,
    row_groups: int = 1,
    allowed_codecs: tuple[str, ...] = ("SNAPPY",),
    limits: ParquetLimits | None = None,
) -> ParquetContract:
    schema = table.schema
    return ParquetContract(
        expected_schema=schema,
        expected_schema_fingerprint=parquet_schema_fingerprint(schema),
        projected_columns=projected_columns or tuple(schema.names),
        expected_rows=table.num_rows,
        expected_row_groups=row_groups,
        allowed_codecs=allowed_codecs,
        limits=limits or ParquetLimits(),
    )


def _error_code(error: pytest.ExceptionInfo[AdapterError]) -> str:
    return error.value.code


def test_reader_uses_held_handle_exact_projection_and_safe_arrow_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = pa.table(
        {
            "timestamp": pa.array([1, 2, 3], type=pa.int64()),
            "container_name": ["alpha", "beta", "alpha"],
            "message": ["LABEL_CANARY"] * 3,
        }
    )
    data = _parquet_bytes(table, row_group_size=2)
    contract = _contract(
        table,
        projected_columns=("timestamp", "container_name"),
        row_groups=2,
    )
    constructor_kwargs: dict[str, object] = {}
    batch_kwargs: dict[str, object] = {}
    original = pq.ParquetFile

    class _ObservedParquetFile:
        def __init__(self, source, **kwargs) -> None:
            constructor_kwargs.update(kwargs)
            self._inner = original(source, **kwargs)
            self.metadata = self._inner.metadata
            self.schema = self._inner.schema
            self.schema_arrow = self._inner.schema_arrow

        def iter_batches(self, **kwargs):
            batch_kwargs.update(kwargs)
            return self._inner.iter_batches(**kwargs)

    monkeypatch.setattr(reader_module.pq, "ParquetFile", _ObservedParquetFile)
    stream = io.BytesIO(data)
    batches = read_parquet_batches(stream, contract=contract)

    assert stream.closed is False
    assert sum(batch.num_rows for batch in batches) == 3
    expected_names = ["timestamp", "container_name"]
    assert all(batch.schema.names == expected_names for batch in batches)
    assert constructor_kwargs["memory_map"] is False
    assert constructor_kwargs["pre_buffer"] is False
    assert constructor_kwargs["buffer_size"] == 0
    assert constructor_kwargs["filesystem"] is None
    assert constructor_kwargs["thrift_string_size_limit"] == (
        contract.limits.thrift_string_size_limit
    )
    assert constructor_kwargs["thrift_container_size_limit"] == (
        contract.limits.thrift_container_size_limit
    )
    assert batch_kwargs["columns"] == ["timestamp", "container_name"]
    assert batch_kwargs["use_threads"] is False
    assert batch_kwargs["use_pandas_metadata"] is False
    assert "message" not in batch_kwargs["columns"]
    if int(pa.__version__.split(".", maxsplit=1)[0]) >= 21:
        assert constructor_kwargs["arrow_extensions_enabled"] is False
        assert constructor_kwargs["page_checksum_verification"] is True


@pytest.mark.parametrize(
    "source",
    [
        "sample.parquet",
        Path("sample.parquet"),
        b"PAR1not-a-held-handlePAR1",
        bytearray(b"PAR1not-a-held-handlePAR1"),
        memoryview(b"PAR1not-a-held-handlePAR1"),
    ],
)
def test_reader_rejects_paths_uris_and_byte_blobs(source: object) -> None:
    table = pa.table({"value": pa.array([1], type=pa.int64())})
    with pytest.raises(AdapterError) as error:
        read_parquet_batches(source, contract=_contract(table))
    assert _error_code(error) == "adapter_unsafe_field"


def test_reader_detaches_private_stream_failure() -> None:
    class _StreamBomb:
        closed = False

        def read(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-PARQUET-STREAM-CANARY")

        def seek(self, *_args, **_kwargs):
            raise RuntimeError("PRIVATE-PARQUET-STREAM-CANARY")

        def tell(self):
            raise RuntimeError("PRIVATE-PARQUET-STREAM-CANARY")

        def readable(self):
            return True

        def seekable(self):
            return True

    table = pa.table({"value": pa.array([1], type=pa.int64())})

    with pytest.raises(AdapterError) as error:
        read_parquet_batches(_StreamBomb(), contract=_contract(table))

    assert _error_code(error) == "adapter_invalid_input"
    assert "PRIVATE-PARQUET" not in str(error.value)
    assert "PRIVATE-PARQUET" not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_reader_enforces_exact_schema_and_fingerprint() -> None:
    expected = pa.table({"value": pa.array([1], type=pa.int64())})
    unknown = pa.table(
        {
            "value": pa.array([1], type=pa.int64()),
            "unknown": pa.array([2], type=pa.int64()),
        }
    )
    with pytest.raises(AdapterError) as extra_error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(unknown)),
            contract=_contract(expected),
        )
    assert _error_code(extra_error) == "adapter_unsafe_field"

    invalid_pin = _contract(expected)
    invalid_pin = ParquetContract(
        expected_schema=invalid_pin.expected_schema,
        expected_schema_fingerprint="0" * 64,
        projected_columns=invalid_pin.projected_columns,
        expected_rows=invalid_pin.expected_rows,
        expected_row_groups=invalid_pin.expected_row_groups,
        allowed_codecs=invalid_pin.allowed_codecs,
        limits=invalid_pin.limits,
    )
    with pytest.raises(AdapterError) as pin_error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(expected)),
            contract=invalid_pin,
        )
    assert _error_code(pin_error) == "adapter_invalid_input"


def test_schema_fingerprint_ignores_writer_metadata_but_not_fields() -> None:
    plain = pa.schema([pa.field("value", pa.int64(), nullable=True)])
    metadata = plain.with_metadata({b"pandas": b"writer-specific"})
    changed = pa.schema([pa.field("value", pa.int64(), nullable=False)])

    assert parquet_schema_fingerprint(plain) == parquet_schema_fingerprint(metadata)
    assert parquet_schema_fingerprint(plain) != parquet_schema_fingerprint(changed)


def test_reader_rejects_nested_fields_before_materialization() -> None:
    table = pa.table(
        {
            "value": pa.array(
                [[{"secret": "LABEL_CANARY"}]],
                type=pa.list_(pa.struct([("secret", pa.string())])),
            )
        }
    )
    with pytest.raises(AdapterError) as error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(table)),
            contract=_contract(table),
        )
    assert _error_code(error) == "adapter_unsafe_field"


def test_reader_rejects_arrow_extension_types() -> None:
    class _CanaryExtension(pa.ExtensionType):
        def __init__(self) -> None:
            super().__init__(pa.int64(), "networkagent.test.canary")

        def __arrow_ext_serialize__(self) -> bytes:
            return b""

        @classmethod
        def __arrow_ext_deserialize__(cls, storage_type, serialized):
            del storage_type, serialized
            return cls()

    extension_type = _CanaryExtension()
    pa.register_extension_type(extension_type)
    try:
        array = pa.ExtensionArray.from_storage(
            extension_type,
            pa.array([1], type=pa.int64()),
        )
        table = pa.table({"value": array})
        data = _parquet_bytes(table)
        expected = pa.table({"value": pa.array([1], type=pa.int64())})
        with pytest.raises(AdapterError) as error:
            read_parquet_batches(
                io.BytesIO(data),
                contract=_contract(expected),
            )
        assert _error_code(error) == "adapter_unsafe_field"
    finally:
        pa.unregister_extension_type(extension_type.extension_name)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_reader_rejects_non_finite_projected_values(value: float) -> None:
    table = pa.table({"metric": pa.array([value], type=pa.float64())})
    with pytest.raises(AdapterError) as error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(table)),
            contract=_contract(table),
        )
    assert _error_code(error) == "adapter_invalid_input"


def test_reader_enforces_magic_and_footer_budget_before_pyarrow() -> None:
    table = pa.table({"value": pa.array([1, 2], type=pa.int64())})
    data = _parquet_bytes(table)
    damaged = data[:-4] + b"NOPE"
    with pytest.raises(AdapterError) as magic_error:
        read_parquet_batches(io.BytesIO(damaged), contract=_contract(table))
    assert _error_code(magic_error) == "adapter_invalid_input"

    limits = ParquetLimits(max_footer_bytes=8)
    with pytest.raises(AdapterError) as footer_error:
        read_parquet_batches(
            io.BytesIO(data),
            contract=_contract(table, limits=limits),
        )
    assert _error_code(footer_error) == "adapter_limit_exceeded"


@pytest.mark.parametrize(
    ("limits", "row_group_size"),
    [
        (ParquetLimits(max_file_bytes=12), None),
        (ParquetLimits(max_metadata_bytes=1), None),
        (ParquetLimits(max_rows=1), None),
        (ParquetLimits(max_row_groups=1), 1),
        (ParquetLimits(max_columns=1), None),
        (ParquetLimits(max_row_group_rows=1), None),
        (ParquetLimits(max_compressed_bytes=1), None),
        (ParquetLimits(max_uncompressed_bytes=1), None),
        (ParquetLimits(max_decoded_bytes=1), None),
    ],
)
def test_reader_enforces_row_group_column_and_decoded_budgets(
    limits: ParquetLimits,
    row_group_size: int | None,
) -> None:
    table = pa.table(
        {
            "left": pa.array([1, 2], type=pa.int64()),
            "right": pa.array([3, 4], type=pa.int64()),
        }
    )
    groups = 2 if row_group_size == 1 else 1
    with pytest.raises(AdapterError) as error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(table, row_group_size=row_group_size)),
            contract=_contract(
                table,
                row_groups=groups,
                limits=limits,
            ),
        )
    assert _error_code(error) == "adapter_limit_exceeded"


def test_reader_enforces_codec_string_and_batch_budgets() -> None:
    table = pa.table({"value": ["abcdefgh", "ijklmnop"]})
    data = _parquet_bytes(table)

    with pytest.raises(AdapterError) as codec_error:
        read_parquet_batches(
            io.BytesIO(data),
            contract=_contract(table, allowed_codecs=("ZSTD",)),
        )
    assert _error_code(codec_error) == "adapter_unsafe_field"

    with pytest.raises(AdapterError) as string_error:
        read_parquet_batches(
            io.BytesIO(data),
            contract=_contract(
                table,
                limits=ParquetLimits(max_string_bytes=4),
            ),
        )
    assert _error_code(string_error) == "adapter_limit_exceeded"

    with pytest.raises(AdapterError) as batch_error:
        read_parquet_batches(
            io.BytesIO(data),
            contract=_contract(
                table,
                limits=ParquetLimits(max_batch_bytes=1),
            ),
        )
    assert _error_code(batch_error) == "adapter_limit_exceeded"


def test_reader_contract_rejects_duplicate_or_unprojected_columns() -> None:
    schema = pa.schema([("value", pa.int64())])
    common = dict(
        expected_schema=schema,
        expected_schema_fingerprint=parquet_schema_fingerprint(schema),
        expected_rows=1,
        expected_row_groups=1,
        allowed_codecs=("SNAPPY",),
    )
    with pytest.raises(AdapterError) as duplicate:
        ParquetContract(projected_columns=("value", "value"), **common)
    assert _error_code(duplicate) == "adapter_invalid_input"
    with pytest.raises(AdapterError) as unknown:
        ParquetContract(projected_columns=("unknown",), **common)
    assert _error_code(unknown) == "adapter_unsafe_field"


def test_reader_revalidates_a_frozen_contract_at_the_trust_boundary() -> None:
    table = pa.table({"value": pa.array([1], type=pa.int64())})
    contract = _contract(table)
    object.__setattr__(contract, "projected_columns", ["value"])
    with pytest.raises(AdapterError) as error:
        read_parquet_batches(
            io.BytesIO(_parquet_bytes(table)),
            contract=contract,
        )
    assert _error_code(error) == "adapter_invalid_input"
