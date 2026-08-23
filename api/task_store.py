from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class TaskNotFound(LookupError):
    pass


class TaskStore:
    """SQLite-backed task metadata and bounded event log for one app process."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_events: int = 100,
        max_bytes: int = 256 * 1024,
        ttl_seconds: float = 3600.0,
        clock=time.time,
    ) -> None:
        if max_events <= 0 or max_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("task retention limits must be positive")
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    thread_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '',
                    approvals_json TEXT NOT NULL DEFAULT '[]',
                    next_seq INTEGER NOT NULL DEFAULT 0,
                    dropped_events INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    thread_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    encoded_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (thread_id, seq),
                    FOREIGN KEY (thread_id) REFERENCES tasks(thread_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS consumed_approvals (
                    thread_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    consumed_at REAL NOT NULL,
                    PRIMARY KEY (thread_id, approval_id),
                    FOREIGN KEY (thread_id) REFERENCES tasks(thread_id) ON DELETE CASCADE
                );
                """
            )

    @classmethod
    def from_env(
        cls,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> TaskStore:
        values = os.environ if environ is None else environ
        configured = values.get("TASK_STORE_DB_PATH", "data/tasks.sqlite")
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = project_root / path
        return cls(
            path.resolve(),
            max_events=_positive_int(values, "DEEP_SEARCH_EVENT_HISTORY_MAX_EVENTS", 100),
            max_bytes=_positive_int(values, "DEEP_SEARCH_EVENT_HISTORY_MAX_BYTES", 256 * 1024),
            ttl_seconds=_positive_float(values, "DEEP_SEARCH_EVENT_HISTORY_TTL_SECONDS", 3600.0),
        )

    def create_task(self, thread_id: str, owner: str, query: str) -> None:
        now = self._clock()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT owner FROM tasks WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if existing is not None and existing["owner"] != owner:
                raise TaskNotFound(thread_id)
            self._connection.execute(
                """
                INSERT INTO tasks(thread_id, owner, query, status, created_at, updated_at)
                VALUES (?, ?, ?, 'started', ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    query = excluded.query,
                    status = 'started',
                    result = '',
                    approvals_json = '[]',
                    updated_at = excluded.updated_at
                """,
                (thread_id, owner, query, now, now),
            )

    def claim_thread(self, thread_id: str, owner: str) -> None:
        now = self._clock()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT owner FROM tasks WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise TaskNotFound(thread_id)
                return
            self._connection.execute(
                """
                INSERT INTO tasks(thread_id, owner, status, created_at, updated_at)
                VALUES (?, ?, 'idle', ?, ?)
                """,
                (thread_id, owner, now, now),
            )

    def ensure_owner(self, thread_id: str, owner: str) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT owner FROM tasks WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None or row["owner"] != owner:
            raise TaskNotFound(thread_id)

    def set_status(
        self,
        thread_id: str,
        status: str,
        *,
        result: str | None = None,
        approvals: list[Any] | None = None,
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, self._clock()]
        if result is not None:
            assignments.append("result = ?")
            values.append(result)
        if approvals is not None:
            assignments.append("approvals_json = ?")
            values.append(json.dumps(approvals, ensure_ascii=False))
        values.append(thread_id)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE thread_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise TaskNotFound(thread_id)

    def append_event(self, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection:
            task = self._connection.execute(
                "SELECT next_seq, dropped_events FROM tasks WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if task is None:
                raise TaskNotFound(thread_id)
            seq = task["next_seq"]
            stored = dict(event)
            stored["seq"] = seq
            stored.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            payload = json.dumps(stored, ensure_ascii=False, separators=(",", ":"))
            encoded_bytes = len(payload.encode("utf-8"))
            now = self._clock()
            self._connection.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?)",
                (thread_id, seq, payload, encoded_bytes, now),
            )
            status, result, approvals = self._event_state(stored)
            assignments = ["next_seq = ?", "updated_at = ?"]
            values: list[Any] = [seq + 1, now]
            if status is not None:
                assignments.append("status = ?")
                values.append(status)
            if result is not None:
                assignments.append("result = ?")
                values.append(result)
            if approvals is not None:
                assignments.append("approvals_json = ?")
                values.append(json.dumps(approvals, ensure_ascii=False))
            values.append(thread_id)
            self._connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE thread_id = ?",
                values,
            )
            self._trim_events(thread_id)
            return stored

    def get_task(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tasks WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if row is None:
                raise TaskNotFound(thread_id)
            events = self._connection.execute(
                "SELECT payload_json FROM task_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
        decoded = [json.loads(item["payload_json"]) for item in events]
        first_available = decoded[0]["seq"] if decoded else row["next_seq"]
        return {
            "thread_id": thread_id,
            "status": row["status"],
            "query": row["query"],
            "result": row["result"],
            "approvals": json.loads(row["approvals_json"]),
            "events": decoded,
            "first_available_seq": first_available,
            "next_seq": row["next_seq"],
            "dropped_events": row["dropped_events"],
        }

    def replay(self, thread_id: str, *, after_seq: int | None = None) -> list[dict[str, Any]]:
        task = self.get_task(thread_id)
        if after_seq is None:
            return task["events"]
        return [event for event in task["events"] if event["seq"] > after_seq]

    def consume_approval(self, thread_id: str, approval_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO consumed_approvals VALUES (?, ?, ?)",
                (thread_id, approval_id, self._clock()),
            )
            return cursor.rowcount == 1

    def approval_consumed(self, thread_id: str, approval_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM consumed_approvals WHERE thread_id = ? AND approval_id = ?",
                (thread_id, approval_id),
            ).fetchone()
        return row is not None

    def reconcile_incomplete(self) -> int:
        now = self._clock()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET status = 'interrupted', result = 'task interrupted by process restart', updated_at = ?
                WHERE status IN ('started', 'queued', 'running')
                """,
                (now,),
            )
            return cursor.rowcount

    def cleanup(self, *, now: float | None = None) -> int:
        cutoff = (self._clock() if now is None else now) - self.ttl_seconds
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM tasks
                WHERE updated_at < ? AND status IN ('idle', 'completed', 'error', 'interrupted', 'cancelled')
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def clear(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM tasks")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _trim_events(self, thread_id: str) -> None:
        rows = self._connection.execute(
            "SELECT seq, encoded_bytes FROM task_events WHERE thread_id = ? ORDER BY seq",
            (thread_id,),
        ).fetchall()
        total_bytes = sum(row["encoded_bytes"] for row in rows)
        dropped = 0
        while rows and (len(rows) > self.max_events or total_bytes > self.max_bytes):
            oldest = rows.pop(0)
            total_bytes -= oldest["encoded_bytes"]
            self._connection.execute(
                "DELETE FROM task_events WHERE thread_id = ? AND seq = ?",
                (thread_id, oldest["seq"]),
            )
            dropped += 1
        if dropped:
            self._connection.execute(
                "UPDATE tasks SET dropped_events = dropped_events + ? WHERE thread_id = ?",
                (dropped, thread_id),
            )

    @staticmethod
    def _event_state(event: dict[str, Any]):
        name = event.get("event")
        data = event.get("data") or {}
        if name == "task_result":
            return "completed", str(data.get("result", "")), []
        if name == "error":
            return "error", str(event.get("message", "")), []
        if name == "waiting_for_approval":
            return "waiting_for_approval", None, data.get("approvals", [])
        if name in {"approval_resumed", "approval_rejected", "tool_start", "assistant_call"}:
            return "running", None, [] if name.startswith("approval_") else None
        return None, None, None


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    value = default if raw is None or not raw.strip() else float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
