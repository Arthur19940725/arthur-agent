"""Truncate bulky tool arguments before they reach WebSocket / HITL UI."""

from __future__ import annotations

from typing import Any

PREVIEW_CHARS = 400


def preview_tool_args(tool_name: str, args: Any, *, preview_chars: int = PREVIEW_CHARS) -> Any:
    if not isinstance(args, dict):
        return args
    if tool_name != "generate_markdown":
        return dict(args)

    previewed = dict(args)
    content = previewed.pop("content", None)
    if not isinstance(content, str):
        if content is not None:
            previewed["content"] = content
        return previewed

    previewed["content_chars"] = len(content)
    if len(content) <= preview_chars:
        previewed["content_preview"] = content
    else:
        previewed["content_preview"] = content[:preview_chars] + "…"
    return previewed


def preview_action_requests(action_requests: list) -> list:
    previewed = []
    for action in action_requests:
        if not isinstance(action, dict):
            previewed.append(action)
            continue
        item = dict(action)
        name = item.get("name") or ""
        if "args" in item:
            item["args"] = preview_tool_args(name, item.get("args"))
        previewed.append(item)
    return previewed
