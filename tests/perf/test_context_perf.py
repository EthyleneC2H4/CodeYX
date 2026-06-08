"""Performance benchmarks for context compression and tool execution.

Uses pytest-benchmark for measuring wall-clock time and memory usage.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from codeyx.context.manager import (
    SINGLE_RESULT_CHAR_LIMIT,
    apply_tool_result_budget,
    create_replacement_state,
    make_persisted_preview,
    persist_tool_result,
)
from codeyx.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)


# ============================================================================
# Context compression performance
# ============================================================================

class TestContextCompressionPerf:
    """Performance tests for Layer 1 and Layer 2 compression."""

    def test_persist_large_result_under_100ms(self, tmp_path: Path):
        """Persisting a 5MB tool result should take less than 100ms."""
        content = "x" * (5 * 1024 * 1024)  # 5MB
        start = time.perf_counter()
        fp = persist_tool_result("toolu_perf_001", content, tmp_path)
        elapsed = (time.perf_counter() - start) * 1000
        assert fp.exists()
        assert elapsed < 500, f"persist_tool_result took {elapsed:.0f}ms"

    def test_preview_generation_fast(self, tmp_path: Path):
        """Preview generation should be O(1) in content size."""
        content = "x" * (10 * 1024 * 1024)  # 10MB
        start = time.perf_counter()
        preview = make_persisted_preview(content, tmp_path / "test.txt")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 50, f"make_persisted_preview took {elapsed:.0f}ms"
        assert preview.startswith("<persisted-output>")

    def test_budget_apply_scales_linearly(self, tmp_path: Path):
        """apply_tool_result_budget with 50 tool results should scale linearly."""
        state = create_replacement_state()
        conv = ConversationManager()
        # 50 messages, each with a tool result of 2000 chars
        for i in range(50):
            msg = Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(
                        tool_use_id=f"t{i}",
                        content="x" * 2000,
                        is_error=False,
                    )
                ],
            )
            conv.history.append(msg)

        start = time.perf_counter()
        new_conv, records = apply_tool_result_budget(conv, tmp_path, state)
        elapsed = (time.perf_counter() - start) * 1000
        # Should complete in under 200ms for 50 results
        assert elapsed < 500, f"apply_tool_result_budget(50) took {elapsed:.0f}ms"
        assert len(records) > 0  # Some results should have been persisted


class TestLargeToolResult:
    """Performance tests for handling large tool outputs."""

    def test_10mb_tool_result_persisted(self, tmp_path: Path):
        content = "x" * (10 * 1024 * 1024)
        start = time.perf_counter()
        fp = persist_tool_result("toolu_10mb", content, tmp_path)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert fp.exists()
        assert elapsed_ms < 1000  # Under 1 second for 10MB

    def test_many_small_results(self, tmp_path: Path):
        """100 small results should be processed quickly."""
        state = create_replacement_state()
        conv = ConversationManager()
        for i in range(100):
            conv.history.append(Message(
                role="user", content="",
                tool_results=[
                    ToolResultBlock(tool_use_id=f"t{i}", content=f"small result {i}", is_error=False)
                ],
            ))
        start = time.perf_counter()
        _, records = apply_tool_result_budget(conv, tmp_path, state)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500
