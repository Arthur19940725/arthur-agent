import os
from pathlib import Path
from typing import Optional


def resolve_path(filename: str, session_dir: Optional[str] = None) -> str:
    """Resolve a path and reject paths outside the active session directory."""
    path = Path(filename)
    if not session_dir:
        return str(path.resolve())

    session_path = Path(session_dir).resolve()
    candidate = path if path.is_absolute() else session_path / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(session_path)
    except ValueError as exc:
        raise ValueError("Path must stay inside the session directory") from exc
    return str(resolved)
