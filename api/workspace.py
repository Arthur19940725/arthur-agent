from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class WorkspaceBoundaryError(ValueError):
    """Raised when a path is not confined to the active session workspace."""


def validate_thread_id(raw_thread_id: str) -> str:
    value = raw_thread_id.strip()
    if not value:
        raise ValueError("invalid thread_id")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
            return value
        raise ValueError("invalid thread_id") from None


@dataclass(frozen=True)
class SessionWorkspace:
    """Thread-scoped file workspace with one fail-closed path boundary."""

    project_root: Path
    thread_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(self, "thread_id", validate_thread_id(self.thread_id))

    @property
    def output_dir(self) -> Path:
        return self.project_root / "output" / f"session_{self.thread_id}"

    @property
    def upload_dir(self) -> Path:
        return self.project_root / "updated" / f"session_{self.thread_id}"

    def prepare(self) -> None:
        self._prepare_session_root("output")
        self._prepare_session_root("updated")

    def resolve_artifact(self, relative_path: str) -> Path:
        return self._resolve_inside(self._session_root("output"), relative_path)

    def resolve_upload(self, filename: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."} or safe_name != filename:
            raise WorkspaceBoundaryError("upload filename must not contain a directory")
        return self._resolve_inside(self._session_root("updated"), safe_name)

    def save_upload(self, filename: str, source: BinaryIO) -> str:
        self.prepare()
        destination = self.resolve_upload(filename)
        with destination.open("wb") as target:
            shutil.copyfileobj(source, target)
        return destination.name

    def import_uploads(self) -> None:
        self.prepare()
        for source in self._session_root("updated").iterdir():
            if source.is_file() and not source.is_symlink():
                shutil.copy2(source, self.resolve_artifact(source.name))

    def list_artifacts(self) -> list[dict[str, Any]]:
        root = self._session_root("output")
        if not root.exists():
            return []
        items: list[dict[str, Any]] = []
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            try:
                confined = self.resolve_artifact(relative)
            except WorkspaceBoundaryError:
                continue
            stat = confined.stat()
            items.append(
                {
                    "name": confined.name,
                    "type": "file",
                    "path": relative,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        items.sort(key=lambda item: item["mtime"], reverse=True)
        return items

    def _prepare_session_root(self, area: str) -> Path:
        base = self.project_root / area
        base.mkdir(parents=True, exist_ok=True)
        self._ensure_inside(self.project_root, base.resolve())
        session_root = base / f"session_{self.thread_id}"
        session_root.mkdir(parents=True, exist_ok=True)
        return self._session_root(area)

    def _session_root(self, area: str) -> Path:
        resolved_base = (self.project_root / area).resolve()
        self._ensure_inside(self.project_root, resolved_base)
        resolved_session = (resolved_base / f"session_{self.thread_id}").resolve()
        self._ensure_inside(resolved_base, resolved_session)
        return resolved_session

    @staticmethod
    def _ensure_inside(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError:
            raise WorkspaceBoundaryError("session workspace root escapes the project") from None

    @staticmethod
    def _resolve_inside(root: Path, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if not relative_path or candidate.is_absolute() or candidate.drive:
            raise WorkspaceBoundaryError("path must be relative to the session workspace")
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            raise WorkspaceBoundaryError("path escapes the session workspace") from None
        return resolved
