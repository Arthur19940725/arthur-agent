from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from api.admission import AdmissionError
from api.auth import (
    AuthConfigurationError,
    AuthSettings,
    Principal,
    authenticate_credentials,
    authentication_error,
    decode_access_token,
    get_auth_settings,
    get_current_user,
    issue_access_token,
)
from api.monitor import manager
from api.runtime import AppRuntime, open_app_runtime
from api.task_store import TaskNotFound
from api.workspace import SessionWorkspace, WorkspaceBoundaryError, validate_thread_id

project_root = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    AuthSettings.from_env()
    async with open_app_runtime(project_root) as runtime:
        app.state.runtime = runtime
        yield


app = FastAPI(title="arthur-agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in os.getenv("AUTH_CORS_ORIGINS", "").split(",") if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskRequest(BaseModel):
    query: str
    thread_id: str | None = None


class ApprovalDecision(BaseModel):
    type: Literal["approve", "reject"]
    message: str | None = Field(default=None, max_length=500)


class ApprovalRequest(BaseModel):
    approval_id: str
    decisions: list[ApprovalDecision]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _runtime() -> AppRuntime:
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("application runtime is not started")
    return runtime


def _validated_thread_id(raw_thread_id: str | None) -> str:
    if raw_thread_id is None:
        return str(uuid.uuid4())
    try:
        return validate_thread_id(raw_thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid thread_id") from exc


def _ensure_thread_owner(thread_id: str, subject: str, *, create: bool = False) -> None:
    try:
        if create:
            _runtime().tasks.claim(thread_id, subject)
        else:
            _runtime().tasks.ensure_owner(thread_id, subject)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


def _snapshot_approvals(snapshot) -> list[dict[str, Any]]:
    approvals = []
    for interrupt_item in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(interrupt_item, "value", None)
        approval_id = getattr(interrupt_item, "id", None)
        if not approval_id or not isinstance(value, dict):
            continue
        approvals.append(
            {
                "approval_id": approval_id,
                "action_requests": value.get("action_requests", []),
                "review_configs": value.get("review_configs", []),
            }
        )
    return approvals


async def _get_pending_approvals(thread_id: str) -> list[dict[str, Any]]:
    graph = _runtime().agent.graph
    if not hasattr(graph, "aget_state"):
        return []
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return _snapshot_approvals(snapshot)


def _raise_admission(exc: AdmissionError) -> None:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    headers = None
    if exc.retry_after is not None:
        detail["retry_after_seconds"] = exc.retry_after
        headers = {"Retry-After": str(exc.retry_after)}
    raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc


@app.get("/")
async def root():
    return FileResponse(project_root / "static" / "index.html")


@app.post("/api/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_auth_settings()
    principal = authenticate_credentials(form_data.username, form_data.password, settings)
    if principal is None:
        raise authentication_error("Invalid credentials")
    return TokenResponse(
        access_token=issue_access_token(principal.subject, settings),
        expires_in=settings.token_expire_minutes * 60,
    )


@app.post("/api/task")
async def run_task(
    request: TaskRequest,
    current_user: Principal = Depends(get_current_user),
):
    runtime = _runtime()
    thread_id = _validated_thread_id(request.thread_id)
    query_bytes = len(request.query.encode("utf-8"))
    max_query_bytes = runtime.tasks.admission.settings.max_query_bytes
    if query_bytes > max_query_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "query_too_large",
                "message": "query exceeds the configured byte limit",
                "max_bytes": max_query_bytes,
                "actual_bytes": query_bytes,
            },
        )
    try:
        runtime.tasks.claim(thread_id, current_user.subject)
        if await _get_pending_approvals(thread_id):
            raise HTTPException(status_code=409, detail="Task is waiting for approval")
        runtime.tasks.schedule(
            owner=current_user.subject,
            thread_id=thread_id,
            query=request.query,
            runner=runtime.agent.run,
        )
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except AdmissionError as exc:
        _raise_admission(exc)
    return {
        "status": "started",
        "thread_id": thread_id,
        "result_url": f"/api/task/{thread_id}",
    }


@app.get("/api/task/{thread_id}")
async def get_task(
    thread_id: str,
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    try:
        result = _runtime().tasks.get(thread_id, current_user.subject)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    pending = await _get_pending_approvals(thread_id)
    if pending:
        result["status"] = "waiting_for_approval"
        result["approval"] = pending[0]
    return result


@app.get("/api/task/{thread_id}/approval")
async def get_task_approval(
    thread_id: str,
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    _ensure_thread_owner(thread_id, current_user.subject)
    pending = await _get_pending_approvals(thread_id)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending approval")
    return pending[0]


@app.post("/api/task/{thread_id}/approval")
async def decide_task_approval(
    thread_id: str,
    request: ApprovalRequest,
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    runtime = _runtime()
    _ensure_thread_owner(thread_id, current_user.subject)
    async with runtime.tasks.locked(thread_id):
        pending = await _get_pending_approvals(thread_id)
        approval = next(
            (item for item in pending if item["approval_id"] == request.approval_id),
            None,
        )
        if approval is None:
            if runtime.tasks.approval_consumed(thread_id, request.approval_id):
                return {"status": "already_processed", "approval_id": request.approval_id}
            raise HTTPException(status_code=409, detail="Approval is no longer pending")
        if len(request.decisions) != len(approval["action_requests"]):
            raise HTTPException(status_code=422, detail="Decision count does not match actions")
        for decision, review in zip(
            request.decisions,
            approval["review_configs"],
            strict=True,
        ):
            if decision.type not in review.get("allowed_decisions", []):
                raise HTTPException(status_code=422, detail="Decision is not allowed")
        decisions = [decision.model_dump() for decision in request.decisions]
        try:
            runtime.tasks.schedule(
                owner=current_user.subject,
                thread_id=thread_id,
                query="",
                runner=runtime.agent.run,
                resume_decisions=decisions,
                approval_id=request.approval_id,
            )
        except AdmissionError as exc:
            _raise_admission(exc)
    return {
        "status": "accepted",
        "approval_id": request.approval_id,
        "thread_id": thread_id,
        "task_status": "running",
    }


@app.post("/api/task/{thread_id}/files")
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    _ensure_thread_owner(thread_id, current_user.subject)
    workspace = SessionWorkspace(project_root, thread_id)
    saved = []
    try:
        for uploaded in files:
            saved.append(workspace.save_upload(uploaded.filename or "", uploaded.file))
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "uploaded", "files": saved}


@app.get("/api/task/{thread_id}/files")
async def list_files(
    thread_id: str,
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    _ensure_thread_owner(thread_id, current_user.subject)
    try:
        files = SessionWorkspace(project_root, thread_id).list_artifacts()
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=404, detail="File workspace not found") from exc
    return {"files": files}


@app.get("/api/task/{thread_id}/files/{artifact_path:path}")
async def download_file(
    thread_id: str,
    artifact_path: str,
    current_user: Principal = Depends(get_current_user),
):
    thread_id = _validated_thread_id(thread_id)
    _ensure_thread_owner(thread_id, current_user.subject)
    try:
        target = SessionWorkspace(project_root, thread_id).resolve_artifact(artifact_path)
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    await websocket.accept()
    authenticated = False
    try:
        settings = AuthSettings.from_env()
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.ws_auth_timeout_seconds
            )
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=1008)
            return
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
            await websocket.close(code=1008)
            return
        token = auth_message.get("token")
        if not isinstance(token, str) or not token:
            await websocket.close(code=1008)
            return
        try:
            principal = decode_access_token(token, settings)
            thread_id = validate_thread_id(thread_id)
            _runtime().tasks.claim(thread_id, principal.subject)
        except (ValueError, TaskNotFound):
            await websocket.close(code=1008)
            return
        expires_in = (principal.expires_at or 0) - time.time()
        if expires_in <= 0:
            await websocket.close(code=1008)
            return
        await websocket.send_json({"type": "auth_ok"})
        authenticated = True

        async def run_authenticated_socket():
            await manager.register_authenticated(websocket, thread_id)
            while True:
                data = await websocket.receive_text()
                await websocket.send_json({"type": "pong", "message": f"服务端已收到: {data}"})

        try:
            await asyncio.wait_for(run_authenticated_socket(), timeout=expires_in)
        except TimeoutError:
            await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    except AuthConfigurationError:
        await websocket.close(code=1011)
    finally:
        if authenticated:
            manager.disconnect(websocket, thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
