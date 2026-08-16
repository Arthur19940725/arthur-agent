from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class HistoryEntry:
    seq: int
    event: dict[str, Any]
    created_at: float
    encoded_bytes: int


@dataclass(frozen=True)
class HistorySnapshot:
    events: tuple[dict[str, Any], ...]
    first_available_seq: int
    next_seq: int
    dropped_events: int


@dataclass
class _ThreadHistory:
    entries: deque[HistoryEntry]
    next_seq: int = 0
    dropped_events: int = 0


class EventHistory:
    def __init__(
        self,
        *,
        max_events: int = 100,
        max_bytes: int = 256 * 1024,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_events <= 0 or max_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("history limits must be positive")
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._histories: dict[str, _ThreadHistory] = {}
        self._bytes: dict[str, int] = {}

    @classmethod
    def from_env(
        cls,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "EventHistory":
        def positive_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            value = int(raw)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            return value

        def positive_float(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            value = float(raw)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            return value

        return cls(
            max_events=positive_int("DEEP_SEARCH_EVENT_HISTORY_MAX_EVENTS", 100),
            max_bytes=positive_int("DEEP_SEARCH_EVENT_HISTORY_MAX_BYTES", 256 * 1024),
            ttl_seconds=positive_float("DEEP_SEARCH_EVENT_HISTORY_TTL_SECONDS", 3600.0),
            clock=clock,
        )

    @staticmethod
    def encoded_size(event: Mapping[str, Any]) -> int:
        return len(
            json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    def append(
        self,
        thread_id: str,
        event: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        current = self._clock() if now is None else now
        copied = dict(event)
        with self._lock:
            history = self._histories.setdefault(thread_id, _ThreadHistory(deque()))
            self._prune_expired(thread_id, history, current)
            seq = history.next_seq
            history.next_seq += 1
            encoded_bytes = self.encoded_size(copied)
            if encoded_bytes > self.max_bytes:
                history.dropped_events += 1
                return None

            copied.setdefault("seq", seq)
            encoded_bytes = self.encoded_size(copied)
            if encoded_bytes > self.max_bytes:
                history.dropped_events += 1
                return None
            entry = HistoryEntry(seq, copied, current, encoded_bytes)
            history.entries.append(entry)
            self._bytes[thread_id] = self._bytes.get(thread_id, 0) + encoded_bytes
            self._trim(thread_id, history)
            return dict(copied)

    def replay(self, thread_id: str, *, now: float | None = None) -> HistorySnapshot:
        current = self._clock() if now is None else now
        with self._lock:
            history = self._histories.get(thread_id)
            if history is None:
                return HistorySnapshot((), 0, 0, 0)
            self._prune_expired(thread_id, history, current)
            first = history.entries[0].seq if history.entries else history.next_seq
            return HistorySnapshot(
                tuple(dict(entry.event) for entry in history.entries),
                first,
                history.next_seq,
                history.dropped_events,
            )

    def cleanup(self, thread_id: str | None = None, *, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        removed = 0
        with self._lock:
            ids = [thread_id] if thread_id is not None else list(self._histories)
            for current_id in ids:
                history = self._histories.get(current_id)
                if history is not None:
                    removed += self._prune_expired(current_id, history, current)
        return removed

    def __getitem__(self, thread_id: str) -> list[dict[str, Any]]:
        return list(self.replay(thread_id).events)

    def __setitem__(self, thread_id: str, events: list[Mapping[str, Any]]) -> None:
        self.clear(thread_id)
        current = self._clock()
        with self._lock:
            history = self._histories.setdefault(thread_id, _ThreadHistory(deque()))
            for event in events:
                copied = dict(event)
                seq = history.next_seq
                history.next_seq += 1
                encoded_bytes = self.encoded_size(copied)
                if encoded_bytes > self.max_bytes:
                    history.dropped_events += 1
                    continue
                history.entries.append(HistoryEntry(seq, copied, current, encoded_bytes))
                self._bytes[thread_id] = self._bytes.get(thread_id, 0) + encoded_bytes
                self._trim(thread_id, history)

    def __contains__(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._histories

    def clear(self, thread_id: str | None = None) -> int:
        with self._lock:
            if thread_id is not None:
                existed = int(thread_id in self._histories)
                self._histories.pop(thread_id, None)
                self._bytes.pop(thread_id, None)
                return existed
            count = len(self._histories)
            self._histories.clear()
            self._bytes.clear()
            return count

    def _prune_expired(self, thread_id: str, history: _ThreadHistory, now: float) -> int:
        cutoff = now - self.ttl_seconds
        removed = 0
        while history.entries and history.entries[0].created_at <= cutoff:
            entry = history.entries.popleft()
            self._bytes[thread_id] = self._bytes.get(thread_id, 0) - entry.encoded_bytes
            history.dropped_events += 1
            removed += 1
        if not history.entries:
            self._bytes[thread_id] = 0
        return removed

    def _trim(self, thread_id: str, history: _ThreadHistory) -> None:
        while len(history.entries) > self.max_events or self._bytes.get(thread_id, 0) > self.max_bytes:
            entry = history.entries.popleft()
            self._bytes[thread_id] -= entry.encoded_bytes
            history.dropped_events += 1
        if not history.entries:
            self._bytes[thread_id] = 0
