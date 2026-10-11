"""
Storage backend: the same Lambda handlers run on DynamoDB or on local JSON files.

WHY THIS EXISTS
---------------
The hackathon offers two ways to build: locally with AWS open-source tools, or
deployed on AWS services. Most projects pick one and the other becomes dead code.

This module makes the choice a runtime flag instead of a rewrite. `aws/lambda_planner.py`
and `aws/lambda_ingest.py` are the real handlers either way -- they never import boto3
directly, they ask this module for a table. Set SHADECAST_BACKEND=dynamodb and they talk to
DynamoDB; leave it unset and they read and write JSON under .shadecast-data/.

So the function you invoke on your laptop with SAM CLI is byte-for-byte the function that
would run in Lambda. No "local version" that drifts from the deployed one, and no AWS
account needed to run or demo the whole pipeline.

THE LOCAL BACKEND IS NOT A DATABASE
-----------------------------------
It is a dict in a JSON file. No concurrency control, no indexes, no pagination. It exists
so the handlers can run without an account, and it is honest about being a stand-in --
which is the point, because the deployed path is one environment variable away.
"""

from __future__ import annotations

import json
import os
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

BACKEND = os.environ.get("SHADECAST_BACKEND", "local").lower()
LOCAL_DIR = Path(os.environ.get("SHADECAST_DATA_DIR", ".shadecast-data"))

_lock = threading.Lock()


def _decimalise(obj: Any) -> Any:
    """DynamoDB rejects floats. Mirror that rule locally so the two backends agree."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: _decimalise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimalise(v) for v in obj]
    return obj


def _plain(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


class LocalTable:
    """A JSON file pretending, just enough, to be a DynamoDB table."""

    def __init__(self, name: str, keys: tuple[str, ...]):
        self.name = name
        self.keys = keys
        self.path = LOCAL_DIR / f"{name}.json"
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, rows: list[dict]) -> None:
        self.path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def _identity(self, item: dict) -> tuple:
        return tuple(str(item.get(k)) for k in self.keys)

    # -- the four operations the handlers actually use --------------------

    def put_item(self, Item: dict) -> None:  # noqa: N803 (boto3 casing)
        with _lock:
            rows = self._read()
            ident = self._identity(Item)
            rows = [r for r in rows if self._identity(r) != ident]
            rows.append(_plain(Item))
            self._write(rows)

    def get_item(self, Key: dict) -> dict:  # noqa: N803
        wanted = tuple(str(Key[k]) for k in self.keys if k in Key)
        for row in self._read():
            if tuple(str(row.get(k)) for k in self.keys if k in Key) == wanted:
                return {"Item": _decimalise(row)}
        return {}

    def scan(self) -> dict:
        return {"Items": [_decimalise(r) for r in self._read()]}

    def query_prefix(self, partition: str, sort_prefix: str) -> list[dict]:
        pk, sk = self.keys
        out = [
            _decimalise(r)
            for r in self._read()
            if str(r.get(pk)) == partition and str(r.get(sk, "")).startswith(sort_prefix)
        ]
        return sorted(out, key=lambda r: str(r.get(sk, "")))

    def batch_put(self, items: Iterable[dict]) -> int:
        with _lock:
            rows = self._read()
            index = {self._identity(r): i for i, r in enumerate(rows)}
            n = 0
            for item in items:
                plain = _plain(item)
                ident = self._identity(plain)
                if ident in index:
                    rows[index[ident]] = plain
                else:
                    index[ident] = len(rows)
                    rows.append(plain)
                n += 1
            self._write(rows)
            return n


class DynamoTable:
    """Thin wrapper so the handlers call the same four methods on either backend."""

    def __init__(self, name: str, keys: tuple[str, ...]):
        import boto3  # imported lazily: the local backend must not need boto3

        self.name = name
        self.keys = keys
        self._t = boto3.resource("dynamodb").Table(name)

    def put_item(self, Item: dict) -> None:  # noqa: N803
        self._t.put_item(Item=_decimalise(Item))

    def get_item(self, Key: dict) -> dict:  # noqa: N803
        return self._t.get_item(Key=Key)

    def scan(self) -> dict:
        return self._t.scan()

    def query_prefix(self, partition: str, sort_prefix: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key as DKey  # noqa: PLC0415

        pk, sk = self.keys
        resp = self._t.query(
            KeyConditionExpression=DKey(pk).eq(partition) & DKey(sk).begins_with(sort_prefix)
        )
        return sorted(resp.get("Items", []), key=lambda r: str(r.get(sk, "")))

    def batch_put(self, items: Iterable[dict]) -> int:
        n = 0
        with self._t.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=_decimalise(item))
                n += 1
        return n


# ---------------------------------------------------------------------------
# Table registry
# ---------------------------------------------------------------------------

_SCHEMA = {
    "sites": (os.environ.get("SITES_TABLE", "shadecast-sites"), ("site_id",)),
    "weather": (os.environ.get("WEATHER_TABLE", "shadecast-weather"), ("grid", "hour_ist")),
    "plans": (os.environ.get("PLANS_TABLE", "shadecast-plans"), ("site_id", "date")),
}

_cache: dict[str, LocalTable | DynamoTable] = {}


def table(logical: str) -> LocalTable | DynamoTable:
    """Get a table by logical name: 'sites', 'weather' or 'plans'."""
    if logical not in _cache:
        name, keys = _SCHEMA[logical]
        _cache[logical] = (
            DynamoTable(name, keys) if BACKEND == "dynamodb" else LocalTable(name, keys)
        )
    return _cache[logical]


def backend_name() -> str:
    return "DynamoDB" if BACKEND == "dynamodb" else f"local files ({LOCAL_DIR}/)"
