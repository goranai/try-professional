"""Append-only audit log. Each record hashes the one before it, so edits are detectable.

Removing records from the end leaves a valid chain, so the ledger also stores the
latest hash and verification compares against it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    records: int
    head: str
    problem: str | None = None


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._head = GENESIS
        if path.exists():
            result = verify_log(path)
            if not result.ok:
                raise ValueError(f"audit log {path} is corrupt: {result.problem}")
            self._seq = result.records
            self._head = result.head

    @property
    def head(self) -> str:
        return self._head

    def append(self, timestamp: str, kind: str, data: dict[str, Any]) -> str:
        self._seq += 1
        record = {"seq": self._seq, "ts": timestamp, "kind": kind, "data": data, "prev": self._head}
        record["hash"] = _digest(record)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._head = str(record["hash"])
        return self._head


def verify_log(path: Path, expected_head: str | None = None) -> VerifyResult:
    head = GENESIS
    count = 0
    if not path.exists():
        return VerifyResult(False, 0, head, "log file is missing")
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return VerifyResult(False, count, head, f"line {number} is not valid JSON")
            if record.get("seq") != number:
                return VerifyResult(False, count, head, f"line {number} has seq {record.get('seq')}")
            if record.get("prev") != head:
                return VerifyResult(False, count, head, f"line {number} does not follow the previous record")
            if _digest(record) != record.get("hash"):
                return VerifyResult(False, count, head, f"line {number} was modified")
            head = record["hash"]
            count += 1
    if expected_head is not None and head != expected_head:
        return VerifyResult(False, count, head, "log does not end at the recorded head: records were removed")
    return VerifyResult(True, count, head)


def read_records(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]
