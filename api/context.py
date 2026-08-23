from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from api.workspace import SessionWorkspace, WorkspaceBoundaryError

_workspace_ctx: ContextVar[SessionWorkspace | None] = ContextVar("workspace", default=None)
_thread_id_ctx: ContextVar[str | None] = ContextVar("thread_id", default=None)


@dataclass(frozen=True)
class RunContextTokens:
    workspace: Token
    thread_id: Token


def bind_run_context(thread_id: str, workspace: SessionWorkspace) -> RunContextTokens:
    if workspace.thread_id != thread_id:
        raise ValueError("thread_id does not match workspace")
    return RunContextTokens(
        workspace=_workspace_ctx.set(workspace),
        thread_id=_thread_id_ctx.set(thread_id),
    )


def require_workspace() -> SessionWorkspace:
    workspace = _workspace_ctx.get()
    if workspace is None:
        raise WorkspaceBoundaryError("no session workspace is bound to this run")
    return workspace


def get_thread_context() -> str | None:
    return _thread_id_ctx.get()


def reset_run_context(tokens: RunContextTokens) -> None:
    _workspace_ctx.reset(tokens.workspace)
    _thread_id_ctx.reset(tokens.thread_id)
