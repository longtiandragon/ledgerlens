"""Lossless, snapshot-local transport for model context.

The durable investigation state and tool/output contracts are not changed. The
packet only interns complete, exactly equal values and tabulates record fields.
It is deliberately JSON-readable: references never stand in for evidence IDs.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any


FORMAT = "ledger-context-v1"
DECODE_INSTRUCTION = (
    "$ref means pool[index]. $object contains literal key/value pairs. "
    "shared_evidence.records $records lists [table,row] in original order; "
    "record_tables use columns/rows and optional key_orders. "
    "Pool/table indexes are never evidence IDs."
)
_MARKERS = {"$ref", "$object", "$records"}
_MIN_POOL_BYTES = 80
_MAX_DEPTH = 128
# Independent decoder resource bounds, not changes to the Agent's request or
# Token budget. Eight MB of fully expanded context leaves ample room for exact
# policy/document text beyond the current 128 KB outbound request guard.
_MAX_EXPANDED_BYTES = 8_000_000
_MAX_EXPANDED_NODES = 1_000_000


@dataclass(frozen=True)
class _RecordRows:
    positions: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Shape:
    bytes: int
    nodes: int
    depth: int


def _check_shape(shape: _Shape) -> _Shape:
    if shape.bytes > _MAX_EXPANDED_BYTES or shape.nodes > _MAX_EXPANDED_NODES:
        raise ValueError("Expanded context exceeds the transport byte/node bound")
    if shape.depth > _MAX_DEPTH + 1:
        raise ValueError("Decoded context nesting exceeds the transport bound")
    return shape


def _container_shape(parts, *, keys=None) -> _Shape:
    encoded_bytes, nodes, depth, count = 2, 1, 1, 0
    for index, child in enumerate(parts):
        encoded_bytes += child.bytes + (1 if count else 0)
        if keys is not None:
            encoded_bytes += _json_bytes(keys[index]) + 1
        nodes += child.nodes
        depth = max(depth, child.depth + 1)
        count += 1
        _check_shape(_Shape(encoded_bytes, nodes, depth))
    return _Shape(encoded_bytes, nodes, depth)


def _wire_shape(item: Any, pool_shapes: list[_Shape], *, max_ref: int,
                record_shapes=(), path: tuple = (), allow_records=False, literal=False) -> _Shape:
    """Count the complete expansion without constructing or copying that data."""
    if type(item) is list:
        return _container_shape(
            _wire_shape(child, pool_shapes, max_ref=max_ref, record_shapes=record_shapes,
                        path=(*path, index), allow_records=allow_records, literal=literal)
            for index, child in enumerate(item)
        )
    if type(item) is not dict:
        return _check_shape(_Shape(_json_bytes(item), 1, 1))
    if not literal and "$ref" in item:
        index = item.get("$ref")
        if set(item) != {"$ref"} or type(index) is not int or not 0 <= index < max_ref:
            raise ValueError("Invalid context pool reference")
        return pool_shapes[index]
    if not literal and "$object" in item:
        pairs = item.get("$object")
        if (set(item) != {"$object"} or type(pairs) is not list
                or any(type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str for pair in pairs)
                or len({pair[0] for pair in pairs}) != len(pairs)):
            raise ValueError("Invalid escaped context object")
        return _container_shape(
            (_wire_shape(pair[1], pool_shapes, max_ref=max_ref, record_shapes=record_shapes,
                         path=(*path, pair[0]), allow_records=allow_records) for pair in pairs),
            keys=[pair[0] for pair in pairs],
        )
    if not literal and "$records" in item:
        positions = item.get("$records")
        if (set(item) != {"$records"} or type(positions) is not list or not allow_records
                or path != ("shared_evidence", "records")):
            raise ValueError("Invalid context record reference")
        selected = []
        for position in positions:
            if (type(position) is not list or len(position) != 2
                    or any(type(index) is not int for index in position)):
                raise ValueError("Invalid context record reference")
            table_index, row_index = position
            if (not 0 <= table_index < len(record_shapes)
                    or not 0 <= row_index < len(record_shapes[table_index])):
                raise ValueError("Invalid context record reference")
            selected.append(record_shapes[table_index][row_index])
        return _container_shape(selected)
    return _container_shape(
        (_wire_shape(child, pool_shapes, max_ref=max_ref, record_shapes=record_shapes,
                     path=(*path, key), allow_records=allow_records, literal=literal)
         for key, child in item.items()), keys=list(item),
    )


def _preflight_pool_tables(packet: dict) -> list[_Shape]:
    pool_shapes: list[_Shape] = []
    for index, entry in enumerate(packet["pool"]):
        pool_shapes.append(_wire_shape(entry, pool_shapes, max_ref=index))
    _check_shape(_Shape(sum(shape.bytes for shape in pool_shapes),
                        sum(shape.nodes for shape in pool_shapes),
                        max((shape.depth for shape in pool_shapes), default=1)))
    table_shapes = [_wire_shape(table, pool_shapes, max_ref=len(pool_shapes))
                    for table in packet["record_tables"]]
    _check_shape(_Shape(sum(shape.bytes for shape in table_shapes),
                        sum(shape.nodes for shape in table_shapes),
                        max((shape.depth for shape in table_shapes), default=1)))
    return pool_shapes


def _clone_json(value: Any, active: set[int] | None = None, depth: int = 0) -> Any:
    """Validate JSON types and reject cycles before processing any references."""
    if depth > _MAX_DEPTH:
        raise ValueError("Context nesting exceeds the transport bound")
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Context numbers must be finite")
        return value
    if type(value) not in {dict, list}:
        raise ValueError("Context must contain only JSON values")
    active = active if active is not None else set()
    identity = id(value)
    if identity in active:
        raise ValueError("Context contains a cycle")
    active.add(identity)
    try:
        if type(value) is list:
            return [_clone_json(item, active, depth + 1) for item in value]
        if not all(type(key) is str for key in value):
            raise ValueError("Context object keys must be strings")
        return {key: _clone_json(item, active, depth + 1) for key, item in value.items()}
    finally:
        active.remove(identity)


def _record_tables(context: dict) -> list[dict]:
    shared = context.get("shared_evidence")
    if type(shared) is not dict or type(shared.get("records")) is not list:
        return []
    records = shared["records"]
    if not all(type(record) is dict for record in records):
        return []
    groups: dict[tuple[str, ...], int] = {}
    tables: list[dict] = []
    positions = []
    for record in records:
        fields = tuple(sorted(record))
        if fields not in groups:
            groups[fields] = len(tables)
            tables.append({"columns": list(record), "rows": []})
        table_index = groups[fields]
        table = tables[table_index]
        row_index = len(table["rows"])
        table["rows"].append([record[column] for column in table["columns"]])
        if list(record) != table["columns"]:
            table.setdefault("key_orders", {})[str(row_index)] = [
                table["columns"].index(column) for column in record
            ]
        positions.append((table_index, row_index))
    shared["records"] = _RecordRows(tuple(positions))
    return tables


def _value_key(value: Any) -> tuple:
    # Object order, JSON scalar types and negative zero are retained as well as
    # values. A table marker has its own type, so no input value can collide.
    if isinstance(value, _RecordRows):
        return ("records", value.positions)
    if type(value) is dict:
        return ("object", tuple((key, _value_key(item)) for key, item in value.items()))
    if type(value) is list:
        return ("array", tuple(_value_key(item) for item in value))
    if type(value) is float:
        return ("float", value.hex())
    return (type(value).__name__, value)


def _json_bytes(value: Any) -> int:
    return len(json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        default=lambda item: {"$records": item.positions},
    ).encode("utf-8"))


def pack_context(value: dict) -> dict:
    """Return an independent, versioned JSON packet without changing ``value``.

    Pools belong exclusively to this call. Strings and complete list/object
    values are shared only by exact equality; documentary prose is not shortened.
    Original marker-shaped objects are escaped as literal key/value pairs.
    """
    if type(value) is not dict:
        raise ValueError("Context root must be an object")
    context = _clone_json(value)
    _wire_shape(context, [], max_ref=0, literal=True)
    tables = _record_tables(context)
    counts: Counter = Counter()
    sizes: dict[tuple, int] = {}

    def count(item: Any) -> None:
        if type(item) in {str, list, dict}:
            key = _value_key(item)
            counts[key] += 1
            if key not in sizes:
                sizes[key] = _json_bytes(item)
        if type(item) is dict:
            for child in item.values():
                count(child)
        elif type(item) is list:
            for child in item:
                count(child)

    count(context)
    for table in tables:
        count(table)
    pool: list = []
    indexes: dict[tuple, int] = {}
    container_pool_enabled = True

    def encode(item: Any, *, literal: bool = False) -> Any:
        if not literal and (type(item) is str or (container_pool_enabled and type(item) in {list, dict})):
            key = _value_key(item)
            if counts[key] > 1 and sizes[key] >= _MIN_POOL_BYTES:
                if key not in indexes:
                    encoded = encode(item, literal=True)
                    indexes[key] = len(pool)
                    pool.append(encoded)
                return {"$ref": indexes[key]}
        if isinstance(item, _RecordRows):
            return {"$records": [list(position) for position in item.positions]}
        if type(item) is list:
            return [encode(child) for child in item]
        if type(item) is dict:
            if _MARKERS.intersection(item):
                return {"$object": [[key, encode(child)] for key, child in item.items()]}
            return {key: encode(child) for key, child in item.items()}
        return item

    def packet_view():
        encoded_context = encode(context)
        encoded_tables = [encode(table, literal=True) for table in tables]
        return {
            "format": FORMAT, "decode": DECODE_INSTRUCTION, "pool": pool,
            "record_tables": encoded_tables, "context": encoded_context,
        }

    packet = packet_view()
    try:
        _preflight_pool_tables(packet)
    except ValueError:
        # Deeply nested repeated containers can create many overlapping pool
        # expansions even when the complete original context is small. Preserve
        # that valid context using string-only interning instead of returning a
        # packet the decoder would reject. No original data is removed.
        pool = []
        indexes = {}
        container_pool_enabled = False
        packet = packet_view()
        _preflight_pool_tables(packet)
    return packet


def unpack_context(packet: dict) -> dict:
    """Decode a packet, rejecting malformed, forward, cyclic or fake references."""
    packet = _clone_json(packet)
    if (type(packet) is not dict
            or set(packet) != {"format", "decode", "pool", "record_tables", "context"}
            or packet["format"] != FORMAT or packet["decode"] != DECODE_INSTRUCTION
            or type(packet["pool"]) is not list or type(packet["record_tables"]) is not list):
        raise ValueError("Invalid context transport envelope")
    decoded_pool: list = []
    decoded_tables: list[list[dict]] = []
    records_seen = False

    # A tiny acyclic wire graph can still describe exponentially large JSON.
    # Preflight EVERY pool expansion and the aggregate pool storage before any
    # reference is deep-copied, retaining exact byte/node counts for later use.
    pool_shapes = _preflight_pool_tables(packet)

    def decode(item: Any, *, max_ref: int, path: tuple = (), depth: int = 0,
               allow_records: bool = False) -> Any:
        nonlocal records_seen
        if depth > _MAX_DEPTH:
            raise ValueError("Decoded context nesting exceeds the transport bound")
        if type(item) is list:
            return [decode(child, max_ref=max_ref, path=(*path, index), depth=depth + 1,
                           allow_records=allow_records) for index, child in enumerate(item)]
        if type(item) is not dict:
            return item
        if "$ref" in item:
            index = item.get("$ref")
            if set(item) != {"$ref"} or type(index) is not int or not 0 <= index < max_ref:
                raise ValueError("Invalid context pool reference")
            return deepcopy(decoded_pool[index])
        if "$object" in item:
            pairs = item.get("$object")
            if set(item) != {"$object"} or type(pairs) is not list:
                raise ValueError("Invalid escaped context object")
            result = {}
            for pair in pairs:
                if (type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str
                        or pair[0] in result):
                    raise ValueError("Invalid escaped context object")
                result[pair[0]] = decode(pair[1], max_ref=max_ref, path=(*path, pair[0]),
                                         depth=depth + 1, allow_records=allow_records)
            return result
        if "$records" in item:
            positions = item.get("$records")
            if (set(item) != {"$records"} or type(positions) is not list or not allow_records
                    or path != ("shared_evidence", "records") or records_seen):
                raise ValueError("Invalid context record reference")
            records_seen = True
            seen = set()
            result = []
            for position in positions:
                if (type(position) is not list or len(position) != 2
                        or any(type(index) is not int for index in position)):
                    raise ValueError("Invalid context record reference")
                table_index, row_index = position
                if (not 0 <= table_index < len(decoded_tables)
                        or not 0 <= row_index < len(decoded_tables[table_index])
                        or (table_index, row_index) in seen):
                    raise ValueError("Invalid context record reference")
                seen.add((table_index, row_index))
                result.append(deepcopy(decoded_tables[table_index][row_index]))
            if len(seen) != sum(len(table) for table in decoded_tables):
                raise ValueError("Context record references omit table rows")
            return result
        if _MARKERS.intersection(item):
            raise ValueError("Invalid context transport marker")
        return {key: decode(child, max_ref=max_ref, path=(*path, key), depth=depth + 1,
                            allow_records=allow_records) for key, child in item.items()}

    for index, entry in enumerate(packet["pool"]):
        decoded_pool.append(decode(entry, max_ref=index))
    field_sets = set()
    for table in packet["record_tables"]:
        if (type(table) is not dict or not {"columns", "rows"} <= set(table)
                or set(table) - {"columns", "rows", "key_orders"}):
            raise ValueError("Invalid context record table")
        columns = decode(table["columns"], max_ref=len(decoded_pool))
        rows = decode(table["rows"], max_ref=len(decoded_pool))
        orders = decode(table.get("key_orders", {}), max_ref=len(decoded_pool))
        if (type(columns) is not list or any(type(column) is not str for column in columns)
                or len(set(columns)) != len(columns) or type(rows) is not list
                or any(type(row) is not list or len(row) != len(columns) for row in rows)
                or type(orders) is not dict):
            raise ValueError("Invalid context record table")
        fields = frozenset(columns)
        if fields in field_sets:
            raise ValueError("Duplicate context record field set")
        field_sets.add(fields)
        for row_index, order in orders.items():
            if (not row_index.isdecimal() or str(int(row_index)) != row_index
                    or not 0 <= int(row_index) < len(rows) or type(order) is not list
                    or any(type(index) is not int for index in order)
                    or sorted(order) != list(range(len(columns)))):
                raise ValueError("Invalid context record key order")
        decoded_tables.append([
            {columns[index]: row[index] for index in orders.get(str(row_index), range(len(columns)))}
            for row_index, row in enumerate(rows)
        ])
    record_shapes = [[_wire_shape(row, [], max_ref=0, literal=True) for row in table]
                     for table in decoded_tables]
    _wire_shape(packet["context"], pool_shapes, max_ref=len(pool_shapes),
                record_shapes=record_shapes, allow_records=True)
    context = decode(packet["context"], max_ref=len(decoded_pool), allow_records=True)
    if type(context) is not dict or (decoded_tables and not records_seen):
        raise ValueError("Invalid decoded context root")
    # Pool references can expand nesting. Apply the same JSON/depth check to the
    # complete result, while also returning objects independent of the packet.
    return _clone_json(context)


def serialize_context(value: dict) -> str:
    """Use the packet only if its compact UTF-8 JSON is smaller than plain JSON.

    The controlled brief reserves the root ``format`` field for this envelope;
    nested documentary ``format`` and marker fields remain ordinary data.
    """
    if type(value) is not dict or "format" in value:
        raise ValueError("Context root must be an object without reserved format")
    packet = pack_context(value)
    plain = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    packed = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return packed if len(packed.encode("utf-8")) < len(plain.encode("utf-8")) else plain


def deserialize_context(text: str) -> dict:
    """Read packed or plain context; nested document markers are never envelopes."""
    def reject_constant(_):
        raise ValueError("Context numbers must be finite")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate context object key")
            result[key] = value
        return result

    value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
    if type(value) is not dict:
        raise ValueError("Context root must be an object")
    if "format" in value:
        return unpack_context(value)
    result = _clone_json(value)
    _wire_shape(result, [], max_ref=0, literal=True)
    return result
