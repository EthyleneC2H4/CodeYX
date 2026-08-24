from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from codeyx.hooks.conditions import ConditionGroup

# $EVENT / $TOOL_NAME / … and $TOOL_ARGS.<key>. Word-boundary after the fixed
# names prevents partial-name collisions (e.g. $TOOL_NAME_X); \w+ covers the
# conventional JSON argument keys.
_PLACEHOLDER_RE = re.compile(
    r"\$(EVENT|TOOL_NAME|FILE_PATH|MESSAGE|ERROR)\b|\$TOOL_ARGS\.(\w+)"
)


@dataclass
class Action:
    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    timeout: int = 30


@dataclass
class ActionResult:
    output: str = ""
    success: bool = True


@dataclass
class Hook:
    id: str
    event: str
    action: Action
    condition: ConditionGroup | None = None
    reject: bool = False
    once: bool = False
    async_exec: bool = False
    executed: bool = False


    def should_run(self) -> bool:
        if self.once and self.executed:
            return False
        return True


    def mark_executed(self) -> None:
        self.executed = True


@dataclass
class HookContext:
    event_name: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""

    def get_field(self, name: str) -> str:
        """Resolve a condition field. Supports the same names as expand():
        tool/event/file_path/message/error and args.<key>."""
        direct = {
            "tool": self.tool_name,
            "event": self.event_name,
            "file_path": self.file_path,
            "message": self.message,
            "error": self.error,
        }
        if name in direct:
            return direct[name]
        if name.startswith("args."):
            key = name[5:]
            value = self.tool_args.get(key, "")
            return str(value) if value else ""
        return ""

    def expand(self, template: str, *, shell_quote: bool = False) -> str:
        """Expand $VARS / $TOOL_ARGS.<key> in a hook template.

        Substitution is single-pass: a value containing text that looks like
        another placeholder is never expanded a second time. With
        ``shell_quote=True`` every substituted value is quoted with
        ``shlex.quote`` so interpolated tool arguments cannot break out of the
        command executed by the `command` executor."""
        values: dict[str, str] = {k: str(v) for k, v in self.tool_args.items()}
        # Reserved context keys always win: a tool argument named "MESSAGE"
        # or "TOOL_NAME" must never be able to spoof hook context.
        values.update({
            "EVENT": self.event_name,
            "TOOL_NAME": self.tool_name,
            "FILE_PATH": self.file_path,
            "MESSAGE": self.message,
            "ERROR": self.error,
        })

        def _repl(m: re.Match[str]) -> str:
            key = m.group(1) or m.group(2)
            if key not in values:
                return m.group(0)
            val = values[key]
            return shlex.quote(val) if shell_quote else val

        return _PLACEHOLDER_RE.sub(_repl, template)


class ToolRejectedError(Exception):
    def __init__(self, tool: str, reason: str, hook_id: str) -> None:
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id
        super().__init__(f"Tool '{tool}' rejected by hook '{hook_id}': {reason}")
