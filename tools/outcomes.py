from __future__ import annotations

import json

from langchain_core.tools import ToolException


class ToolFailure(ToolException):
    """Expected tool failure with a stable, model-visible error contract."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


def render_tool_failure(error: ToolException) -> str:
    if isinstance(error, ToolFailure):
        payload = {
            "ok": False,
            "code": error.code,
            "message": error.public_message,
            "retryable": error.retryable,
        }
    else:
        payload = {
            "ok": False,
            "code": "tool_failure",
            "message": "工具执行失败",
            "retryable": False,
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def expose_failures(tool_object):
    tool_object.handle_tool_error = render_tool_failure
    return tool_object
