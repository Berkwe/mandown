"""Persistent, credential-free Jev scores and portable seed snapshots.

Only successful scores are stored. SQLite provides atomic writes across processes;
striped locks coalesce identical concurrent requests within a Python process.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_TTL = 30 * 24 * 60 * 60
_LOCKS = [threading.RLock() for _ in range(64)]


def comparison_lock(key: str):
    return _LOCKS[int(key[:8], 16) % len(_LOCKS)]


def default_cache() -> JevCache | None:
    """MANDOWN_JEV_CACHE=off disables caching; otherwise it is an SQLite path."""
    configured = os.environ.get("MANDOWN_JEV_CACHE")
    if configured == "off":
        return None
    root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return JevCache(Path(configured) if configured else root / "mandown" / "jev.sqlite3")


class JevCache:
    def __init__(self, path: str | Path, *, ttl: float = DEFAULT_TTL):
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Cache TTL must be positive and finite")
        self.path = Path(path)
        self.ttl = ttl

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS scores "
                    "(key TEXT PRIMARY KEY, probability REAL, model TEXT, created REAL)"
                )
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _valid(row: dict[str, Any]) -> bool:
        return (
            isinstance(row, dict)
            and isinstance(row.get("key"), str)
            and len(row["key"]) == 64
            and all(c in "0123456789abcdef" for c in row["key"])
            and type(row.get("probability")) in (float, int)
            and math.isfinite(row["probability"])
            and 0 <= row["probability"] <= 1
            and isinstance(row.get("model"), str)
            and type(row.get("created")) in (float, int)
            and math.isfinite(row["created"])
            and 0 < row["created"] <= time.time()
        )

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            record = connection.execute(
                "SELECT probability, model, created FROM scores WHERE key=?", (key,)
            ).fetchone()
        if record is None:
            return None
        row = dict(zip(("probability", "model", "created"), record))
        row["key"] = key
        if not self._valid(row) or time.time() - row["created"] >= self.ttl:
            return None
        return row

    def put(self, key: str, result: dict[str, Any]) -> None:
        if result.get("status") != "ok":
            return
        row = {
            "key": key,
            "probability": result.get("probability"),
            "model": result.get("model"),
            "created": time.time(),
        }
        self._store([row])

    def _store(self, rows: list[dict[str, Any]]) -> None:
        if not all(self._valid(row) for row in rows):
            raise ValueError("Invalid cache score")
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO scores VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "probability=excluded.probability, model=excluded.model, created=excluded.created "
                "WHERE excluded.created > scores.created",
                [(r["key"], r["probability"], r["model"], r["created"]) for r in rows],
            )

    def export_seed(self, destination: str | Path) -> int:
        """Export live scores only: no records, API responses, keys or usage data."""
        with self._connect() as connection:
            records = connection.execute(
                "SELECT key, probability, model, created FROM scores ORDER BY key"
            ).fetchall()
        rows = [dict(zip(("key", "probability", "model", "created"), r)) for r in records]
        rows = [r for r in rows if self._valid(r) and time.time() - r["created"] < self.ttl]
        Path(destination).write_text(
            json.dumps({"version": SCHEMA_VERSION, "scores": rows}, indent=2) + "\n",
            encoding="utf-8",
        )
        return len(rows)

    def import_seed(self, source: str | Path) -> int:
        """Import an explicitly chosen trusted snapshot, preserving score timestamps."""
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != SCHEMA_VERSION
            or not isinstance(payload.get("scores"), list)
        ):
            raise ValueError("Unsupported cache seed")
        rows = payload["scores"]
        if not all(self._valid(row) for row in rows):
            raise ValueError("Invalid cache seed score")
        rows = [r for r in rows if time.time() - r["created"] < self.ttl]
        self._store(rows)
        return len(rows)
