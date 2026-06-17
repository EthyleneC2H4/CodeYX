from __future__ import annotations

from typing import Any, AsyncIterator

from codeyx.client import LLMClient
from codeyx.conversation import ConversationManager
from codeyx.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete


class ScriptedLLMClient(LLMClient):
    def __init__(self, script: list[list[dict[str, Any]]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append({
            "messages": conversation.get_messages(),
            "system": system,
            "tools": tools or [],
        })
        if not self._script:
            yield TextDelta(text="")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
            return

        turn = self._script.pop(0)
        for item in turn:
            kind = item.get("type", "text")
            if kind == "text":
                yield TextDelta(text=str(item.get("text", "")))
            elif kind == "tool":
                yield ToolCallComplete(
                    tool_id=str(item.get("id", f"tool-{len(self.calls)}")),
                    tool_name=str(item["name"]),
                    arguments=dict(item.get("args", {})),
                )
            elif kind == "end":
                yield StreamEnd(
                    stop_reason=str(item.get("stop_reason", "end_turn")),
                    input_tokens=int(item.get("input_tokens", 1)),
                    output_tokens=int(item.get("output_tokens", 1)),
                )
        if not turn or turn[-1].get("type") != "end":
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
