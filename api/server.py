import io
import os
import re
import sys

# Windows 默认把管道/终端当成 GBK，Cursor 按 UTF-8 读，中文会变成乱码。
# 必须在导入会 print 中文的模块（如 agent.prompts）之前强制 UTF-8。
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is None:
        continue
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
        if getattr(_stream, "encoding", None) == "utf-8":
            continue
    except Exception:
        pass
    try:
        setattr(
            sys,
            _name,
            io.TextIOWrapper(_stream.detach(), encoding="utf-8", errors="replace", line_buffering=True),
        )
    except Exception:
        pass

import uuid
import asyncio
import time
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from typing import Any, List, Literal
import shutil

# Add project root to sys.path
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import authentication and monitor services. Agent modules are loaded in lifespan.
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
from api.admission import AdmissionError, TaskAdmission


@asynccontextmanager
async def lifespan(app: FastAPI):
    AuthSettings.from_env()
    from agent.main_agent import create_main_agent, run_deep_agent
    from agent.persistence import open_sqlite_checkpointer

    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    app.state.background_tasks = set()
    app.state.agent_runner = run_deep_agent
    app.state.thread_locks = {}
    app.state.thread_owners = {}
    app.state.consumed_approvals = set()
    app.state.admission = TaskAdmission.from_env()

    async with open_sqlite_checkpointer() as checkpointer:
        app.state.main_agent = create_main_agent(checkpointer)
        print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")
        try:
            yield
        finally:
            background_tasks = list(app.state.background_tasks)
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            manager.event_history.cleanup()


app = FastAPI(title="DeepAgents API", lifespan=lifespan)

# 挂载输出目录，以便前端访问生成的静态文件
# 假设输出目录位于项目根目录下的 output
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# 定义上传目录 updated
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 配置 CORS；默认只允许同源请求，跨域来源需显式配置。
cors_origins = [
    origin.strip()
    for origin in os.getenv("AUTH_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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


def _validated_thread_id(raw_thread_id: str | None) -> str:
    if raw_thread_id is None:
        return str(uuid.uuid4())
    raw_thread_id = raw_thread_id.strip()
    try:
        return str(uuid.UUID(raw_thread_id))
    except ValueError:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", raw_thread_id):
            return raw_thread_id
        raise HTTPException(status_code=422, detail="invalid thread_id")


def _admission():
    admission = getattr(app.state, "admission", None)
    if admission is None:
        admission = app.state.admission = TaskAdmission()
    return admission


def _thread_lock(thread_id: str) -> asyncio.Lock:
    locks = getattr(app.state, "thread_locks", None)
    if locks is None:
        locks = app.state.thread_locks = {}
    return locks.setdefault(thread_id, asyncio.Lock())


def _ensure_thread_owner(thread_id: str, subject: str, *, create: bool = False) -> None:
    owners = getattr(app.state, "thread_owners", None)
    if owners is None:
        owners = app.state.thread_owners = {}
    owner = owners.get(thread_id)
    if owner is None and create:
        owners[thread_id] = subject
        return
    if owner != subject:
        raise HTTPException(status_code=404, detail="Task not found")


def _snapshot_approvals(snapshot) -> list[dict[str, Any]]:
    approvals = []
    for interrupt_item in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(interrupt_item, "value", None)
        approval_id = getattr(interrupt_item, "id", None)
        if not approval_id or not isinstance(value, dict):
            continue
        approvals.append({
            "approval_id": approval_id,
            "action_requests": value.get("action_requests", []),
            "review_configs": value.get("review_configs", []),
        })
    return approvals


async def _get_pending_approvals(thread_id: str) -> list[dict[str, Any]]:
    config = {"configurable": {"thread_id": thread_id}}
    graph = getattr(app.state, "main_agent", None)
    if graph is None or not hasattr(graph, "aget_state"):
        return []
    snapshot = await graph.aget_state(config)
    return _snapshot_approvals(snapshot)


def _schedule_agent_run(thread_id: str, query: str, lease, resume_decisions=None):
    async def guarded_run():
        try:
            async with _thread_lock(thread_id):
                if resume_decisions is None:
                    return await app.state.agent_runner(
                        app.state.main_agent,
                        query,
                        thread_id,
                    )
                return await app.state.agent_runner(
                    app.state.main_agent,
                    query,
                    thread_id,
                    resume_decisions=resume_decisions,
                )
        finally:
            lease.release()

    try:
        task = asyncio.create_task(guarded_run())
    except Exception:
        lease.release()
        raise

    app.state.background_tasks.add(task)

    def _finish(done_task):
        app.state.background_tasks.discard(done_task)
        lease.release()
        if not done_task.cancelled():
            done_task.exception()

    task.add_done_callback(_finish)
    return task


@app.get("/")
async def root():
    """浏览器打开 http://localhost:8000 时进入对话页，可直接看智能体回复。"""
    return FileResponse(project_root / "static" / "index.html")

@app.post("/api/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    settings = get_auth_settings()
    principal = authenticate_credentials(
        form_data.username,
        form_data.password,
        settings,
    )
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
    # 1. [ID 初始化]
    thread_id = _validated_thread_id(request.thread_id)
    query_bytes = len(request.query.encode("utf-8"))
    max_query_bytes = _admission().settings.max_query_bytes
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
    _ensure_thread_owner(thread_id, current_user.subject, create=True)
    if await _get_pending_approvals(thread_id):
        raise HTTPException(status_code=409, detail="Task is waiting for approval")
    try:
        lease = _admission().acquire(current_user.subject, thread_id)
    except AdmissionError as exc:
        detail = {"code": exc.code, "message": exc.message}
        headers = {}
        if exc.retry_after is not None:
            detail["retry_after_seconds"] = exc.retry_after
            headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc
    manager.mark_started(thread_id, request.query)

    # 2. [后台执行] 异步运行 Agent，不阻塞主线程
    try:
        _schedule_agent_run(thread_id, request.query, lease)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to schedule task") from exc

    # 3. [立即响应] 最终回复请查 GET /api/task/{thread_id} 或打开首页对话页
    return {"status": "started", "thread_id": thread_id, "result_url": f"/api/task/{thread_id}"}


@app.get("/api/task/{thread_id}")
async def get_task(
    thread_id: str,
    current_user: Principal = Depends(get_current_user),
):
    _ensure_thread_owner(thread_id, current_user.subject)
    pending = await _get_pending_approvals(thread_id)
    result = manager.get_task(thread_id)
    if pending:
        result["status"] = "waiting_for_approval"
        result["approval"] = pending[0]
    return result


@app.get("/api/task/{thread_id}/approval")
async def get_task_approval(
    thread_id: str,
    current_user: Principal = Depends(get_current_user),
):
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
    _ensure_thread_owner(thread_id, current_user.subject)
    async with _thread_lock(thread_id):
        pending = await _get_pending_approvals(thread_id)
        approval = next(
            (item for item in pending if item["approval_id"] == request.approval_id),
            None,
        )
        if approval is None:
            if request.approval_id in app.state.consumed_approvals:
                return {"status": "already_processed", "approval_id": request.approval_id}
            raise HTTPException(status_code=409, detail="Approval is no longer pending")

        if len(request.decisions) != len(approval["action_requests"]):
            raise HTTPException(status_code=422, detail="Decision count does not match actions")
        allowed = approval["review_configs"]
        for decision, review in zip(request.decisions, allowed):
            if decision.type not in review.get("allowed_decisions", []):
                raise HTTPException(status_code=422, detail="Decision is not allowed")

        app.state.consumed_approvals.add(request.approval_id)
        manager._record_event(thread_id, {
            "type": "monitor_event",
            "event": "approval_resumed",
            "message": "审批决定已提交，任务继续执行",
            "data": {},
        })
        try:
            lease = _admission().acquire(current_user.subject, thread_id)
            _schedule_agent_run(
                thread_id,
                "",
                lease,
                resume_decisions=[decision.model_dump() for decision in request.decisions],
            )
        except AdmissionError as exc:
            detail = {"code": exc.code, "message": exc.message}
            headers = {}
            if exc.retry_after is not None:
                detail["retry_after_seconds"] = exc.retry_after
                headers["Retry-After"] = str(exc.retry_after)
            raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc
        return {
            "status": "accepted",
            "approval_id": request.approval_id,
            "thread_id": thread_id,
            "task_status": "running",
        }


@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    thread_id: str = Form(...),
    current_user: Principal = Depends(get_current_user),
):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    thread_id = thread_id.strip()
    try:
        thread_id = str(uuid.UUID(thread_id))
    except ValueError as exc:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", thread_id):
            raise HTTPException(status_code=422, detail="invalid thread_id") from exc
    _ensure_thread_owner(thread_id, current_user.subject)
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    # 2. [保存] 遍历并写入文件
    for file in files:
        filename = Path(file.filename or "").name
        if not filename or filename in {".", ".."}:
            raise HTTPException(status_code=422, detail="Invalid upload filename")
        file_path = target_dir / filename
        # 使用二进制模式写入，支持各种文件格式 (图片、PDF、文本等)
        # shutil.copyfileobj 高效复制文件流，避免一次性加载大文件到内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(filename)

    # 3. [响应] 返回成功保存的文件列表
    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(
    path: str,
    current_user: Principal = Depends(get_current_user),
):
    try:
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 必须确保请求的文件在 output 目录下
        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}
    # 2. [存在性检查]
    if not abs_path.exists():
        return {"error": "文件不存在"}

    # 3. [响应] 返回文件流 (浏览器自动触发下载)
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(
    path: str,
    current_user: Principal = Depends(get_current_user),
):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、时间、下载链接）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    # 1. [调试] 打印请求路径
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 2. [解析] 获取绝对路径对象
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        # 3. [安全] 检查路径是否越界 (Path Traversal Check)
        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    # 4. [检查] 目录是否存在
    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 5. [遍历] 递归查找所有文件
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                # 计算相对路径，生成下载 URL
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "type": "file",
                    "path": str(file_path),
                    # "url": f"/outputs/{url_path}",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime
                })

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 6. [排序] 按修改时间倒序排列 (最新的在前)
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """Authenticate the first message before registering or replaying events."""
    await websocket.accept()
    authenticated = False
    try:
        settings = AuthSettings.from_env()
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=settings.ws_auth_timeout_seconds,
            )
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
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
        except ValueError:
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "auth_ok"})
        authenticated = True
        expires_in = (principal.expires_at or 0) - time.time()
        if expires_in <= 0:
            await websocket.close(code=1008)
            return

        thread_id = thread_id.strip()
        try:
            thread_id = str(uuid.UUID(thread_id))
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", thread_id):
                await websocket.close(code=1008)
                return
        try:
            _ensure_thread_owner(thread_id, principal.subject, create=True)
        except HTTPException:
            await websocket.close(code=1008)
            return

        async def run_authenticated_socket():
            await manager.register_authenticated(websocket, thread_id)
            while True:
                data = await websocket.receive_text()
                await websocket.send_json({
                    "type": "pong",
                    "message": f"服务端已收到: {data}",
                })

        try:
            await asyncio.wait_for(
                run_authenticated_socket(),
                timeout=expires_in,
            )
        except asyncio.TimeoutError:
            await websocket.close(code=1008)
            return

    except WebSocketDisconnect:
        pass
    except AuthConfigurationError:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    except Exception as exc:
        print(f"[WebSocket] 连接异常: {exc}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if authenticated:
            manager.disconnect(websocket, thread_id)

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)