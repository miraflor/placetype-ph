from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class DecisionCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        # WAL plus relaxed sync: one fsync per decision was a needless cost on a run that
        # writes thousands of cache rows.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS decisions (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        self.db.commit()
        self._pending = 0

    @staticmethod
    def key(*parts: object) -> str:
        raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload FROM decisions WHERE cache_key = ?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO decisions(cache_key, payload) VALUES (?, ?)",
            (key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        self._pending += 1
        if self._pending >= 50:
            self.db.commit()
            self._pending = 0

    def flush(self) -> None:
        if self._pending:
            self.db.commit()
            self._pending = 0

    def close(self) -> None:
        self.flush()
        self.db.close()

    def __enter__(self) -> DecisionCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
