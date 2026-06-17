from __future__ import annotations

from dataclasses import dataclass, field

from codeyx.tools.base import ToolCallComplete


@dataclass
class AgentRuntimeState:
    """Mutable state for one Agent.run execution."""

    turn_count: int = 0
    consecutive_unknown_tools: int = 0
    max_tokens_escalated: bool = False
    output_token_recoveries: int = 0
    pending_tool_calls: list[ToolCallComplete] = field(default_factory=list)
    aborted: bool = False

    def next_turn(self) -> int:
        self.turn_count += 1
        return self.turn_count

    def record_tool_result(self, is_unknown: bool) -> None:
        if is_unknown:
            self.consecutive_unknown_tools += 1
        else:
            self.consecutive_unknown_tools = 0

    def reset_output_recoveries(self) -> None:
        self.output_token_recoveries = 0

    def can_retry_output_tokens(self, max_recoveries: int) -> bool:
        return self.output_token_recoveries < max_recoveries

    def record_output_token_retry(self) -> int:
        self.output_token_recoveries += 1
        return self.output_token_recoveries
