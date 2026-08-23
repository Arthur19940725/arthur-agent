# Arthur Agent Research Runtime

本上下文定义 Arthur Agent 中对话执行、文件隔离与人工审批的项目术语，避免把框架 thread、后台 task 和文件 session 混为一谈。

## Language

**Thread**:
由 `thread_id` 标识、可跨多次请求与进程重启延续的一段研究对话。
_Avoid_: Task, request, session

**Task**:
在一个 **Thread** 中执行或恢复的一次后台研究运行。
_Avoid_: Thread, conversation

**Session Workspace**:
仅属于一个 **Thread** 的上传文件与生成文件边界。
_Avoid_: Working directory, arbitrary path

**Approval**:
针对一个已暂停敏感动作的一次可幂等处理决定。
_Avoid_: Permission, confirmation

**Research Event**:
按序记录一个 **Task** 进度或状态变化的可重放事实。
_Avoid_: WebSocket message, log line

## Relationships

- 一个 **Thread** 拥有零个或多个顺序执行的 **Tasks**
- 一个 **Thread** 恰好拥有一个 **Session Workspace**
- 一个 **Task** 产生零个或多个有序 **Research Events**
- 一个暂停的 **Task** 可产生一个或多个 **Approvals**，每个 **Approval** 最多处理一次
- WebSocket 投递 **Research Events**，但不拥有这些事件或 **Task** 状态

## Example dialogue

> **Dev:** “用户断线后，这个 WebSocket 消息还算 **Research Event** 吗？”
> **Domain expert:** “算；事件先写入 **Thread** 的持久日志，WebSocket 只是投递它，重连后按序重放。”

## Flagged ambiguities

- “session”过去同时表示 LangGraph 对话和磁盘目录——已拆分为 **Thread** 与 **Session Workspace**。
- “task”过去有时表示整段对话——现在只表示 **Thread** 中的一次后台运行。
- “permission”容易与 Codex `full-access` 混淆——业务中的敏感工具决定统一称为 **Approval**。
