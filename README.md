# arthur-agent

一个面向学习与小型单进程部署的多智能体研究服务。主智能体按问题选择网络搜索、数据库、RAGFlow、行情、天气和会话文件工具；FastAPI 提供认证、后台任务、人工审批和可重放事件流。

## 架构

```text
HTTP / WebSocket
       │
       ▼
AppRuntime (FastAPI lifespan)
       ├── TaskService ── TaskStore (SQLite)
       ├── ConnectionManager (WebSocket transport)
       └── AgentRuntime
              ├── DeepSeek model Adapter
              ├── Tavily search Adapter
              ├── optional MySQL / RAGFlow Adapters
              └── LangGraph checkpointer (SQLite)
```

几个重要边界：

- `TaskStore` 是任务归属、状态、审批幂等和事件日志的唯一事实来源；LangGraph checkpointer 只保存图状态。
- 事件先写 SQLite，再尝试通过 WebSocket 投递；断线或队列满不会丢失可重放事件。
- 外部客户端只在 FastAPI lifespan 中装配，导入模块和收集测试不需要服务密钥。
- 每个 Thread 都有一个 `SessionWorkspace`；文件工具缺少运行上下文、收到绝对路径、遍历路径或符号链接逃逸时会 fail closed。
- Codex 仓库配置默认 `danger-full-access`，但这不会放宽应用内用户与会话文件边界。

项目术语见 [CONTEXT.md](CONTEXT.md)。

## 环境要求

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/)
- DeepSeek API key
- Tavily API key

Python 3.14 暂不列入支持范围，因为当前 LangChain 依赖仍会触发 Pydantic V1 兼容警告。

## 安装

```bash
git clone https://github.com/Arthur19940725/arthur-agent.git
cd arthur-agent
uv sync --locked --group dev
```

按需启用可选能力：

```bash
uv sync --locked --extra documents --extra pdf --extra ragflow --group dev
```

复制环境模板并填写真实值：

```bash
cp .env.example .env
```

必填配置：

```env
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=sk-...
LLM_DEEPSEEK_MODEL=deepseek-v4-flash
LLM_DEEPSEEK_PRO=deepseek-v4-pro
TAVILY_API_KEY=tvly-...

AUTH_USERNAME=arthur
AUTH_USER_ID=arthur
AUTH_PASSWORD_HASH=<argon2id hash>
JWT_SECRET_KEY=<至少 32 字节随机值>
```

MySQL 和 RAGFlow 是显式可选 capability：只有相关环境变量完整时才会注册对应子智能体。RAGFlow SDK 目前要求 Python 3.12+；在 Python 3.11 上仍可运行核心服务，但不能启用该 extra。缺少已启用 capability 的 Python extra 会让启动明确失败，不会静默伪装成功。

## 启动

```bash
uv run --locked python api/server.py
```

打开 `http://localhost:8000`。API 文档位于 `http://localhost:8000/docs`。

## API

除登录外，HTTP 资源需要 Bearer token；WebSocket 的第一条消息必须是认证消息。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/login` | 登录并获取 JWT |
| `POST` | `/api/task` | 启动或继续一个 Thread 中的 Task |
| `GET` | `/api/task/{thread_id}` | 查询持久化状态、结果和事件 |
| `GET/POST` | `/api/task/{thread_id}/approval` | 查询或处理 Approval |
| `GET/POST` | `/api/task/{thread_id}/files` | 列出生成文件或上传文件 |
| `GET` | `/api/task/{thread_id}/files/{relative_path}` | 下载 Thread 内相对路径文件 |
| `WS` | `/ws/{thread_id}` | 认证后重放并接收 Research Events |

文件列表只返回相对路径；旧的绝对 `path` 查询参数接口已经删除。

## 持久化与恢复

- `CHECKPOINT_DB_PATH`：LangGraph 对话 checkpoint，默认 `data/checkpoints.sqlite`。
- `TASK_STORE_DB_PATH`：Task、归属、Approval 与 Research Event，默认 `data/tasks.sqlite`。
- 启动时遗留的 `started/running/queued` Task 会标记为 `interrupted`，不会未经用户确认自动重跑。
- SQLite 方案限定单进程。多个 Uvicorn worker 或多个实例需要替换为共享任务存储与事件传输实现。

## 工具结果合同

成功结果保持自然语言或结构化数据，便于模型直接使用。已知失败通过 LangChain `ToolException` 返回稳定 JSON：

```json
{"ok":false,"code":"upstream_timeout","message":"网络搜索服务响应超时","retryable":true}
```

外部 Adapter 会映射输入、配置、超时和服务异常，不向模型暴露原始 provider 错误；未知编程异常继续上抛并使 Task 明确失败。

## 验证

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pip-audit
```

CI 在 Python 3.11 和 3.13 上运行同一套门禁。

## 代码导航

- `api/runtime.py`：应用资源的 lifespan 所有权
- `api/task_service.py`：Task 生命周期、admission、锁与后台任务
- `api/task_store.py`：SQLite Task/Approval/Event 实现
- `api/workspace.py`：Thread-scoped 文件边界
- `api/monitor.py`：事件记录后的 WebSocket 投递
- `agent/runtime.py`：模型与外部 Adapter 装配
- `agent/main_agent.py`：多智能体图与执行流
- `tools/outcomes.py`：统一工具失败合同

## License

MIT
