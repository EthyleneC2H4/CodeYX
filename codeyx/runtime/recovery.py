from __future__ import annotations

from codeyx.conversation import ConversationManager, ToolResultBlock
from codeyx.tools.base import ToolCallComplete, ToolResult


class ToolResultRecovery:
    """Builds synthetic tool results when execution cannot produce a normal result."""

    @staticmethod
    def synthetic_result(message: str) -> ToolResult:
        return ToolResult(output=message, is_error=True)

    @staticmethod
    def result_block(
        call: ToolCallComplete,
        result: ToolResult,
        content: str | None = None,
    ) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=call.tool_id,
            content=result.output if content is None else content,
            is_error=result.is_error,
        )

    @staticmethod
    def missing_result_blocks(
        conversation: ConversationManager,
        message: str = "Tool execution was interrupted before returning a result.",
    ) -> list[ToolResultBlock]:
        pending: dict[str, ToolCallComplete] = {}
        for msg in conversation.history:
            if msg.tool_uses:
                for use in msg.tool_uses:
                    pending[use.tool_use_id] = ToolCallComplete(
                        tool_id=use.tool_use_id,
                        tool_name=use.tool_name,
                        arguments=use.arguments,
                    )
            if msg.tool_results:
                for result in msg.tool_results:
                    pending.pop(result.tool_use_id, None)

        return [
            ToolResultBlock(
                tool_use_id=call.tool_id,
                content=message,
                is_error=True,
            )
            for call in pending.values()
        ]
