"""Resilience tests: error recovery, LLM malformed responses, and failure injection.

Tests that the Agent and its subsystems degrade gracefully under adverse
conditions rather than crashing or entering undefined states.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import pytest

from codeyx.agent import Agent, ErrorEvent, StreamText, ToolResultEvent, ToolUseEvent
from codeyx.client import LLMClient
from codeyx.context.manager import CompactCircuitBreaker
from codeyx.conversation import ConversationManager
from codeyx.tools import create_default_registry
from codeyx.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    ToolResult,
)


# ============================================================================
# Malformed LLM response scenarios
# ============================================================================

class MalformedResponseClient(LLMClient):
    """LLM client that returns deliberately broken responses."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.call_count = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.call_count += 1

        if self.scenario == "empty_response":
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=0)
            return

        if self.scenario == "malformed_json_tool_args":
            yield ToolCallStart(tool_name="ReadFile", tool_id="t1")
            yield ToolCallComplete(
                tool_id="t1", tool_name="ReadFile",
                arguments={"malformed": "json"},
            )
            yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
            return

        if self.scenario == "unknown_tool":
            yield ToolCallStart(tool_name="NonExistentTool", tool_id="t1")
            yield ToolCallComplete(
                tool_id="t1", tool_name="NonExistentTool", arguments={},
            )
            yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
            return

        if self.scenario == "consecutive_unknown_tools":
            for i in range(4):
                yield ToolCallStart(tool_name=f"FakeTool{i}", tool_id=f"t{i}")
                yield ToolCallComplete(
                    tool_id=f"t{i}", tool_name=f"FakeTool{i}", arguments={},
                )
            yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
            return

        if self.scenario == "missing_tool_id":
            yield ToolCallComplete(tool_id="", tool_name="Bash", arguments={"command": "ls"})
            yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
            return

        if self.scenario == "empty_tool_args":
            yield ToolCallStart(tool_name="Bash", tool_id="t1")
            yield ToolCallComplete(tool_id="t1", tool_name="Bash", arguments={})
            yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
            return

        if self.scenario == "text_before_tool":
            yield TextDelta(text="Let me help with that.\n")
            yield ToolCallStart(tool_name="ReadFile", tool_id="t1")
            yield ToolCallComplete(
                tool_id="t1", tool_name="ReadFile",
                arguments={"file_path": "main.py"},
            )
            yield StreamEnd(stop_reason="end_turn", input_tokens=15, output_tokens=10)
            return

        # Default: normal text
        yield TextDelta(text="OK")
        yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)


# ============================================================================
# Agent resilience tests
# ============================================================================

class TestAgentMalformedResponse:
    """Agent should handle malformed LLM responses without crashing."""

    async def _run_agent(self, scenario: str) -> list:
        client = MalformedResponseClient(scenario)
        registry = create_default_registry()
        conversation = ConversationManager()
        conversation.add_user_message("test")
        agent = Agent(
            client=client, registry=registry, protocol="openai-compat",
            work_dir="/tmp/test", max_iterations=5,
        )
        return [e async for e in agent.run(conversation)]

    async def test_empty_response_handled(self):
        events = await self._run_agent("empty_response")
        assert not any(isinstance(e, ErrorEvent) for e in events)

    async def test_unknown_tool_reported_as_error(self):
        events = await self._run_agent("unknown_tool")
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) > 0
        assert tool_results[0].is_error

    async def test_consecutive_unknown_terminates(self):
        """Agent should terminate after 3+ consecutive unknown tool calls."""
        events = await self._run_agent("consecutive_unknown_tools")
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) > 0
        assert "unknown" in errors[0].message.lower()

    async def test_empty_tool_args_validated(self):
        """Tool call with empty args should get validation error, not crash."""
        events = await self._run_agent("empty_tool_args")
        tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) > 0
        # Bash should reject empty command
        assert tool_results[0].is_error

    async def test_missing_tool_id_handled(self):
        events = await self._run_agent("missing_tool_id")
        assert not any(isinstance(e, ErrorEvent) for e in events)


class TestAgentMaxTurns:
    """Agent should respect max_iterations limit."""

    async def test_max_turns_enforced(self):
        """After max_iterations, agent must emit ErrorEvent and stop."""

        class InfiniteToolClient(LLMClient):
            async def stream(self, conversation, system="", tools=None):
                yield ToolCallStart(tool_name="ReadFile", tool_id="t1")
                yield ToolCallComplete(
                    tool_id="t1", tool_name="ReadFile",
                    arguments={"file_path": "main.py"},
                )
                yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)

        registry = create_default_registry()
        conversation = ConversationManager()
        conversation.add_user_message("test")
        agent = Agent(
            client=InfiniteToolClient(), registry=registry,
            protocol="openai-compat", work_dir="/tmp/test", max_iterations=3,
        )
        events = [e async for e in agent.run(conversation)]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) > 0
        assert "maximum iterations" in errors[0].message.lower() or "3" in errors[0].message


# ============================================================================
# CircuitBreaker tests
# ============================================================================

class TestCompactCircuitBreaker:
    """CircuitBreaker should prevent infinite retry loops on repeated failures."""

    def test_allows_up_to_three_failures(self):
        breaker = CompactCircuitBreaker()
        assert not breaker.is_open()
        for _ in range(2):
            breaker.record_failure()
        assert not breaker.is_open()

    def test_opens_after_three_failures(self):
        breaker = CompactCircuitBreaker()
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open()

    def test_new_instance_starts_closed(self):
        breaker = CompactCircuitBreaker()
        assert not breaker.is_open()


# ============================================================================
# Tool execution error recovery
# ============================================================================

class TestToolErrorRecovery:
    """Tool execution failures should not crash the Agent loop."""

    async def test_error_result_continues_loop(self):
        """Agent loop continues after a tool returns an error."""

        class ErrorThenTextClient(LLMClient):
            def __init__(self):
                self._call = 0

            async def stream(self, conversation, system="", tools=None):
                self._call += 1
                if self._call == 1:
                    yield ToolCallStart(tool_name="Bash", tool_id="t1")
                    yield ToolCallComplete(
                        tool_id="t1", tool_name="Bash",
                        arguments={"command": "nonexistent_command"},
                    )
                    yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)
                else:
                    yield TextDelta(text="I encountered an error but recovered.")
                    yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

        registry = create_default_registry()
        conversation = ConversationManager()
        conversation.add_user_message("run something")
        agent = Agent(
            client=ErrorThenTextClient(), registry=registry,
            protocol="openai-compat", work_dir="/tmp/test", max_iterations=5,
        )
        events = [e async for e in agent.run(conversation)]
        tool_errors = [e for e in events if isinstance(e, ToolResultEvent) and e.is_error]
        texts = [e for e in events if isinstance(e, StreamText)]
        assert len(tool_errors) > 0
        assert len(texts) > 0  # Agent recovered and produced text
