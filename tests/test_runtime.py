from __future__ import annotations

import pytest

from codeyx.agent import Agent
from codeyx.client import LLMClient
from codeyx.conversation import ConversationManager, ToolUseBlock
from codeyx.runtime import (
    AgentRuntimeState,
    ToolExecutionScheduler,
    ToolResultRecovery,
    partition_tool_calls,
)
from codeyx.tools import create_default_registry
from codeyx.tools.base import StreamEnd, TextDelta, ToolCallComplete, ToolResult


def test_runtime_state_tracks_turns_and_unknown_tools() -> None:
    state = AgentRuntimeState()

    assert state.next_turn() == 1
    assert state.next_turn() == 2

    state.record_tool_result(is_unknown=True)
    state.record_tool_result(is_unknown=True)
    assert state.consecutive_unknown_tools == 2

    state.record_tool_result(is_unknown=False)
    assert state.consecutive_unknown_tools == 0


def test_runtime_state_tracks_output_token_retries() -> None:
    state = AgentRuntimeState()

    assert state.can_retry_output_tokens(2)
    assert state.record_output_token_retry() == 1
    assert state.record_output_token_retry() == 2
    assert not state.can_retry_output_tokens(2)

    state.reset_output_recoveries()
    assert state.output_token_recoveries == 0


def test_scheduler_partitions_concurrent_safe_tools() -> None:
    registry = create_default_registry()
    calls = [
        ToolCallComplete("1", "ReadFile", {}),
        ToolCallComplete("2", "ReadFile", {}),
        ToolCallComplete("3", "EditFile", {}),
        ToolCallComplete("4", "ReadFile", {}),
    ]

    batches = partition_tool_calls(calls, registry)

    assert len(batches) == 3
    assert batches[0].concurrent
    assert [c.tool_id for c in batches[0].calls] == ["1", "2"]
    assert not batches[1].concurrent
    assert batches[1].calls[0].tool_id == "3"
    assert batches[2].concurrent
    assert batches[2].calls[0].tool_id == "4"


@pytest.mark.asyncio
async def test_scheduler_converts_parallel_executor_exception_to_tool_result() -> None:
    registry = create_default_registry()
    scheduler = ToolExecutionScheduler(registry)
    calls = [ToolCallComplete("1", "ReadFile", {})]

    async def failing_executor(_call: ToolCallComplete):
        raise RuntimeError("boom")

    results = await scheduler.run_parallel(calls, failing_executor)

    assert len(results) == 1
    assert results[0].tool_id == "1"
    assert results[0].tool_name == "ReadFile"
    assert results[0].result.is_error
    assert "boom" in results[0].result.output


def test_recovery_builds_missing_tool_result_blocks() -> None:
    conv = ConversationManager()
    conv.add_user_message("Read a file")
    conv.add_assistant_message(
        "Reading",
        tool_uses=[
            ToolUseBlock(
                tool_use_id="tool-1",
                tool_name="ReadFile",
                arguments={"file_path": "README.md"},
            )
        ],
    )

    missing = ToolResultRecovery.missing_result_blocks(conv)

    assert len(missing) == 1
    assert missing[0].tool_use_id == "tool-1"
    assert missing[0].is_error
    assert "interrupted" in missing[0].content


def test_recovery_does_not_duplicate_existing_results() -> None:
    conv = ConversationManager()
    conv.add_user_message("Read a file")
    conv.add_assistant_message(
        "Reading",
        tool_uses=[
            ToolUseBlock(
                tool_use_id="tool-1",
                tool_name="ReadFile",
                arguments={"file_path": "README.md"},
            )
        ],
    )
    conv.add_tool_results_message(
        [
            ToolResultRecovery.result_block(
                ToolCallComplete("tool-1", "ReadFile", {"file_path": "README.md"}),
                ToolResult(output="content"),
            )
        ]
    )

    assert ToolResultRecovery.missing_result_blocks(conv) == []


def test_agent_persisted_tool_result_sets_metadata(tmp_path) -> None:
    class _Client(LLMClient):
        async def stream(self, conversation, system="", tools=None):
            yield TextDelta(text="done")
            yield StreamEnd(stop_reason="end_turn")

    agent = Agent(_Client(), create_default_registry(), "anthropic", work_dir=str(tmp_path))
    result = ToolResult(output="x" * 6000)

    preview = agent._maybe_persist_or_truncate("tool-1", result)

    assert "<persisted-output>" in preview
    assert result.persisted_path is not None
    assert result.display_hint == "persisted_preview"
    assert result.metadata["original_chars"] == 6000
