from __future__ import annotations

import math
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdmissionSettings:
    max_active_per_user: int = 2
    max_active_process: int = 8
    rate_limit: int = 10
    rate_window_seconds: float = 60.0
    rate_history_ttl_seconds: float = 3600.0
    max_query_bytes: int = 32 * 1024

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AdmissionSettings:
        values = os.environ if environ is None else environ
        return cls(
            max_active_per_user=_positive_int(
                values, "DEEP_SEARCH_MAX_ACTIVE_PER_USER", cls.max_active_per_user
            ),
            max_active_process=_positive_int(
                values, "DEEP_SEARCH_MAX_ACTIVE_PROCESS", cls.max_active_process
            ),
            rate_limit=_positive_int(values, "DEEP_SEARCH_RATE_LIMIT", cls.rate_limit),
            rate_window_seconds=_positive_float(
                values, "DEEP_SEARCH_RATE_WINDOW_SECONDS", cls.rate_window_seconds
            ),
            rate_history_ttl_seconds=_positive_float(
                values, "DEEP_SEARCH_RATE_HISTORY_TTL_SECONDS", cls.rate_history_ttl_seconds
            ),
            max_query_bytes=_positive_int(
                values, "DEEP_SEARCH_MAX_QUERY_BYTES", cls.max_query_bytes
            ),
        )


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


class AdmissionError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, status_code: int, retry_after: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass
class _UserState:
    request_times: deque[float] = field(default_factory=deque)
    active: int = 0
    last_seen: float = 0.0


class TaskLease:
    def __init__(self, admission: TaskAdmission, user_id: str, thread_id: str, token: str):
        self._admission = admission
        self.user_id = user_id
        self.thread_id = thread_id
        self.token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._admission._release(self)


class TaskAdmission:
    def __init__(
        self,
        settings: AdmissionSettings | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings or AdmissionSettings.from_env()
        self._clock = clock
        self._lock = threading.RLock()
        self._users: dict[str, _UserState] = {}
        self._active_threads: dict[str, str] = {}
        self._leases: dict[str, TaskLease] = {}
        self._active_process = 0

    @classmethod
    def from_env(
        cls,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> TaskAdmission:
        return cls(AdmissionSettings.from_env(), clock=clock)

    @property
    def active_process(self) -> int:
        with self._lock:
            return self._active_process

    def active_for_user(self, user_id: str) -> int:
        with self._lock:
            state = self._users.get(user_id)
            return state.active if state else 0

    def acquire(self, user_id: str, thread_id: str) -> TaskLease:
        now = self._clock()
        with self._lock:
            state = self._users.setdefault(user_id, _UserState(last_seen=now))
            self._purge_request_times(state, now)
            state.last_seen = now

            if len(state.request_times) >= self.settings.rate_limit:
                oldest = state.request_times[0]
                retry_after = max(1, math.ceil(oldest + self.settings.rate_window_seconds - now))
                raise AdmissionError(
                    "rate_limited",
                    "request frequency limit exceeded",
                    status_code=429,
                    retry_after=retry_after,
                )

            # Count rejected active-conflict attempts in the request window.
            state.request_times.append(now)

            if thread_id in self._active_threads:
                raise AdmissionError(
                    "thread_active",
                    "thread already has an active task",
                    status_code=409,
                )
            if state.active >= self.settings.max_active_per_user:
                raise AdmissionError(
                    "user_active_limit",
                    "user active task limit exceeded",
                    status_code=409,
                )
            if self._active_process >= self.settings.max_active_process:
                raise AdmissionError(
                    "process_active_limit",
                    "process active task limit exceeded",
                    status_code=409,
                )

            token = uuid.uuid4().hex
            lease = TaskLease(self, user_id, thread_id, token)
            state.active += 1
            self._active_process += 1
            self._active_threads[thread_id] = token
            self._leases[token] = lease
            return lease

    def _release(self, lease: TaskLease) -> None:
        with self._lock:
            stored = self._leases.pop(lease.token, None)
            if stored is None:
                return
            state = self._users.get(lease.user_id)
            if state is not None:
                state.active = max(0, state.active - 1)
                state.last_seen = self._clock()
            if self._active_threads.get(lease.thread_id) == lease.token:
                del self._active_threads[lease.thread_id]
            self._active_process = max(0, self._active_process - 1)

    def cleanup_idle_users(self, *, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        removed = 0
        with self._lock:
            for user_id, state in list(self._users.items()):
                self._purge_request_times(state, current)
                if (
                    state.active == 0
                    and current - state.last_seen >= self.settings.rate_history_ttl_seconds
                ):
                    del self._users[user_id]
                    removed += 1
        return removed

    def _purge_request_times(self, state: _UserState, now: float) -> None:
        cutoff = now - self.settings.rate_window_seconds
        while state.request_times and state.request_times[0] <= cutoff:
            state.request_times.popleft()
