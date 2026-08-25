"""Live-API integration tests.

Every test here carries the ``integration`` marker (declared in
pyproject.toml): the default suite and push-triggered CI exclude it via
``-m 'not integration'``, while the manual-dispatch CI job runs exactly
these. Each test self-skips unless its provider key is present, so the job
degrades to "ran, nothing to do" instead of failing without credentials.
"""

from __future__ import annotations

import os

import pytest

from codeyx.client import AnthropicClient, OpenAICompatClient
from codeyx.config import ProviderConfig
from codeyx.conversation import ConversationManager
from codeyx.tools.base import StreamEnd, TextDelta

pytestmark = pytest.mark.integration


def _anthropic_config() -> ProviderConfig | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return ProviderConfig(
        name="anthropic",
        protocol="anthropic",
        base_url="",
        model=os.environ.get("CODEYX_IT_MODEL", "claude-haiku-4-5-20251001"),
    )


def _openai_compat_config() -> ProviderConfig | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    return ProviderConfig(
        name="openai",
        protocol="openai-compat",
        base_url="https://api.openai.com/v1",
        model=os.environ.get("CODEYX_IT_OPENAI_MODEL", "gpt-4o-mini"),
    )


class TestLiveAnthropic:
    @pytest.mark.asyncio
    async def test_stream_round_trip(self):
        cfg = _anthropic_config()
        if cfg is None:
            pytest.skip("ANTHROPIC_API_KEY not set")
        client = AnthropicClient(cfg)

        conv = ConversationManager()
        conv.add_user_message("Reply with exactly the word: pong")
        events: list = []
        async for event in client.stream(conv, system="You are a test echo."):
            events.append(event)

        texts = [e.text for e in events if isinstance(e, TextDelta)]
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert texts, "no text streamed"
        assert len(ends) == 1, f"expected one StreamEnd, got {len(ends)}"
        assert ends[0].input_tokens > 0, "usage missing from terminal event"
        assert ends[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_tool_call_round_trip(self):
        from codeyx.client import ToolCallComplete

        cfg = _anthropic_config()
        if cfg is None:
            pytest.skip("ANTHROPIC_API_KEY not set")
        client = AnthropicClient(cfg)

        conv = ConversationManager()
        conv.add_user_message("Read /tmp/example.txt with the ReadFile tool.")
        tools = [{
            "name": "ReadFile",
            "description": "Read a file from disk.",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        }]
        tool_calls = []
        async for event in client.stream(conv, tools=tools):
            if isinstance(event, ToolCallComplete):
                tool_calls.append(event)

        assert tool_calls, "model did not issue the tool call"
        assert tool_calls[0].tool_name == "ReadFile"
        assert tool_calls[0].arguments.get("file_path")


class TestLiveOpenAICompat:
    @pytest.mark.asyncio
    async def test_stream_yields_single_terminal_event(self):
        cfg = _openai_compat_config()
        if cfg is None:
            pytest.skip("OPENAI_API_KEY not set")
        client = OpenAICompatClient(cfg)

        conv = ConversationManager()
        conv.add_user_message("Reply with exactly the word: pong")
        ends = []
        async for event in client.stream(conv):
            if isinstance(event, StreamEnd):
                ends.append(event)

        assert len(ends) == 1, (
            f"expected exactly one StreamEnd, got {len(ends)} "
            "(double terminal events break token accounting)"
        )
