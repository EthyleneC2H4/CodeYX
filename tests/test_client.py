"""Tests for the LLM client layer — the four protocol clients' streaming
behavior, stop-reason mapping, and token accounting.

These are the first tests covering codeyx/client.py (2026-08 audit finding:
the whole layer had zero coverage). All transports are faked in-process; no
network access happens."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from codeyx.client import (
    AnthropicClient,
    DeepSeekClient,
    OpenAIClient,
    OpenAICompatClient,
    _anthropic_effective_input,
)
from codeyx.conversation import ConversationManager
from codeyx.tools.base import (
    StreamEnd,
    ToolCallComplete,
)

# ---------------------------------------------------------------------------
# Fake transports
# ---------------------------------------------------------------------------


def _achunks(chunks) -> AsyncIterator:
    async def _gen():
        for c in chunks:
            yield c

    return _gen()


class _FakeChatCompletions:
    """Stands in for ``client.chat.completions``: create() is awaited and
    returns an async iterable of chunks."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self.kwargs: dict | None = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _achunks(self._chunks)


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeResponsesCreate:
    def __init__(self, events: list) -> None:
        self._events = events
        self.kwargs: dict | None = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _achunks(self._events)


def _chat_chunk(delta, finish_reason=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=None)


def _usage_chunk(prompt_tokens=5, completion_tokens=2):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


_TEXT_DELTA = SimpleNamespace(content="hi", tool_calls=None)
_EMPTY_DELTA = SimpleNamespace(content=None, tool_calls=None)


def _tool_call_delta(idx=0, call_id="call1", name="F", args=""):
    return SimpleNamespace(index=idx, id=call_id, function=SimpleNamespace(name=name, arguments=args))


def _make_compat_client(chunks):
    client = object.__new__(OpenAICompatClient)
    client.model = "test-model"
    client.max_output_tokens = 1024
    fake = _FakeChatCompletions(chunks)
    client._client = SimpleNamespace(chat=_FakeChat(fake))
    return client, fake


def _make_deepseek_client(chunks):
    client = object.__new__(DeepSeekClient)
    client.model = "deepseek-chat"
    client.max_output_tokens = 1024
    fake = _FakeChatCompletions(chunks)
    client._client = SimpleNamespace(chat=_FakeChat(fake))
    return client, fake


def _make_responses_client(events):
    client = object.__new__(OpenAIClient)
    client.model = "test-model"
    client.max_output_tokens = 2048
    fake = _FakeResponsesCreate(events)
    client._client = SimpleNamespace(responses=fake)
    return client, fake


async def _collect(stream) -> list:
    return [e async for e in stream]


# ---------------------------------------------------------------------------
# Anthropic effective input (cache tokens folded back in)
# ---------------------------------------------------------------------------


class TestAnthropicEffectiveInput:
    def test_cache_read_and_creation_added(self):
        usage = SimpleNamespace(
            input_tokens=100,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=200,
        )
        assert _anthropic_effective_input(usage) == 5300

    def test_missing_cache_attrs_default_zero(self):
        usage = SimpleNamespace(input_tokens=42)
        assert _anthropic_effective_input(usage) == 42

    def test_none_cache_values_treated_as_zero(self):
        usage = SimpleNamespace(input_tokens=10, cache_read_input_tokens=None)
        assert _anthropic_effective_input(usage) == 10

    @pytest.mark.asyncio
    async def test_stream_end_carries_effective_input(self):
        """Regression: with caching engaged the reported input must include
        cached tokens, else auto-compact never triggers."""
        final = SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=300,
                output_tokens=50,
                cache_read_input_tokens=150_000,
                cache_creation_input_tokens=1_000,
            ),
        )
        class _FakeAnthropicStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def get_final_message(self):
                return final

        class _StreamCtx:
            async def __aenter__(self):
                return _FakeAnthropicStream()

            async def __aexit__(self, *exc):
                return False

        class _FakeMessages:
            def stream(self, **kwargs):
                return _StreamCtx()

        client = object.__new__(AnthropicClient)
        client.model = "claude-test"
        client.thinking = False
        client.max_output_tokens = 1024
        client._client = SimpleNamespace(messages=_FakeMessages())

        conv = ConversationManager()
        conv.add_user_message("hello")
        events = await _collect(client.stream(conv))

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].input_tokens == 151_300
        assert ends[0].stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Chat Completions: StreamEnd guarantee + finish-reason mapping
# ---------------------------------------------------------------------------


class TestCompatStreamEndGuarantee:
    @pytest.mark.asyncio
    async def test_usage_chunk_terminates_stream(self):
        client, _ = _make_compat_client([
            _chat_chunk(_TEXT_DELTA),
            _chat_chunk(_EMPTY_DELTA, finish_reason="stop"),
            _usage_chunk(prompt_tokens=11, completion_tokens=7),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert (ends[0].input_tokens, ends[0].output_tokens) == (11, 7)

    @pytest.mark.asyncio
    async def test_missing_usage_chunk_still_yields_stream_end(self):
        """Regression: providers that ignore stream_options must still get a
        terminal StreamEnd — without one, last_input_tokens stays 0 forever."""
        client, _ = _make_compat_client([
            _chat_chunk(_TEXT_DELTA),
            _chat_chunk(_EMPTY_DELTA, finish_reason="stop"),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))

        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "end_turn"
        assert ends[0].input_tokens == 0

    @pytest.mark.asyncio
    async def test_length_finish_maps_to_max_tokens_and_flushes_calls(self):
        """Regression: 'length' was dropped entirely — truncation became
        undetectable and accumulated tool calls vanished."""
        client, _ = _make_compat_client([
            _chat_chunk(SimpleNamespace(content=None, tool_calls=[_tool_call_delta(args='{"a":')]), finish_reason=None),
            _chat_chunk(_EMPTY_DELTA, finish_reason="length"),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))

        calls = [e for e in events if isinstance(e, ToolCallComplete)]
        assert len(calls) == 1, "accumulated tool call was dropped on length"
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "max_tokens"

    @pytest.mark.asyncio
    async def test_no_duplicate_stream_end_when_usage_arrives(self):
        client, _ = _make_compat_client([
            _chat_chunk(_TEXT_DELTA),
            _usage_chunk(),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))
        assert sum(isinstance(e, StreamEnd) for e in events) == 1


class TestDeepSeekStreamEndGuarantee:
    @pytest.mark.asyncio
    async def test_missing_usage_chunk_still_yields_stream_end(self):
        client, _ = _make_deepseek_client([
            _chat_chunk(_TEXT_DELTA),
            _chat_chunk(_EMPTY_DELTA, finish_reason="stop"),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_length_finish_maps_to_max_tokens(self):
        client, _ = _make_deepseek_client([
            _chat_chunk(_TEXT_DELTA),
            _chat_chunk(_EMPTY_DELTA, finish_reason="length"),
        ])
        conv = ConversationManager()
        conv.add_user_message("hi")
        events = await _collect(client.stream(conv))
        ends = [e for e in events if isinstance(e, StreamEnd)]
        assert ends and ends[0].stop_reason == "max_tokens"


# ---------------------------------------------------------------------------
# Responses API: incomplete mapping + max_output_tokens actually sent
# ---------------------------------------------------------------------------


class TestResponsesStopReasons:
    @pytest.mark.asyncio
    async def test_incomplete_max_output_tokens_maps_to_max_tokens(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="partial answer"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="incomplete",
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                    usage=SimpleNamespace(input_tokens=9, output_tokens=4),
                ),
            ),
        ]
        client, fake = _make_responses_client(events)
        conv = ConversationManager()
        conv.add_user_message("hi")
        out = await _collect(client.stream(conv))

        ends = [e for e in out if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "max_tokens", (
            "truncation must be detectable so recovery can escalate"
        )
        assert (ends[0].input_tokens, ends[0].output_tokens) == (9, 4)
        assert fake.kwargs["max_output_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_completed_status_maps_to_end_turn(self):
        events = [
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    incomplete_details=None,
                    usage=SimpleNamespace(input_tokens=3, output_tokens=8),
                ),
            ),
        ]
        client, _ = _make_responses_client(events)
        conv = ConversationManager()
        conv.add_user_message("hi")
        out = await _collect(client.stream(conv))
        ends = [e for e in out if isinstance(e, StreamEnd)]
        assert ends and ends[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stream_without_terminal_event_synthesizes_one(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="text only"),
        ]
        client, _ = _make_responses_client(events)
        conv = ConversationManager()
        conv.add_user_message("hi")
        out = await _collect(client.stream(conv))
        ends = [e for e in out if isinstance(e, StreamEnd)]
        assert len(ends) == 1
        assert ends[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_function_call_roundtrip(self):
        events = [
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(type="function_call", name="ReadFile", call_id="c9"),
            ),
            # All argument content arrives via deltas; .done is a terminator.
            SimpleNamespace(type="response.function_call_arguments.delta", delta='{"fi'),
            SimpleNamespace(type="response.function_call_arguments.delta", delta='le": "x"}'),
            SimpleNamespace(type="response.function_call_arguments.done", delta='{"file": "x"}'),
        ]
        client, _ = _make_responses_client(events)
        conv = ConversationManager()
        conv.add_user_message("hi")
        out = await _collect(client.stream(conv))

        calls = [e for e in out if isinstance(e, ToolCallComplete)]
        assert len(calls) == 1
        assert calls[0].tool_id == "c9"
        assert calls[0].arguments == {"file": "x"}
