"""같은 질문이 다시 오면 API를 부르지 않고 저장된 답을 돌려주는 SQLite 캐시."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .providers import Message, Reply


def make_key(system: str, messages: list[Message]) -> str:
    payload = json.dumps(
        {"system": system, "messages": messages}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, path: str, ttl_seconds: int):
        self.ttl = ttl_seconds
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            "key TEXT PRIMARY KEY, provider TEXT, model TEXT, text TEXT, created REAL)"
        )
        self._db.commit()

    def get(self, key: str) -> Reply | None:
        row = self._db.execute(
            "SELECT provider, model, text, created FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        provider, model, text, created = row
        if self.ttl > 0 and time.time() - created > self.ttl:
            self._db.execute("DELETE FROM responses WHERE key = ?", (key,))
            self._db.commit()
            return None
        return Reply(text=text, provider=provider, model=model)

    def put(self, key: str, reply: Reply) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?)",
            (key, reply.provider, reply.model, reply.text, time.time()),
        )
        self._db.commit()

    def clear(self) -> None:
        self._db.execute("DELETE FROM responses")
        self._db.commit()

    def close(self) -> None:
        self._db.close()
