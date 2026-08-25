"""Regression tests for the 2026-08 audit Wave 3 fixes: aggregate budget
off-by-one, overflow-classifier precedence, empty max_tokens recovery
message, TaskManager/TraceManager retention, fork background param,
mid-stream prompt-command feedback, and gitignore-aware Glob/Grep."""

from __future__ import annotations

import asyncio
import time

import pytest

# ---------------------------------------------------------------------------
# Aggregate budget counts the current message's own content
# ---------------------------------------------------------------------------


class TestAggregateBudget:
    def test_current_message_content_counts_toward_budget(self, tmp_path):
        from codeyx.context.manager import (
            AGGREGATE_CHAR_LIMIT,
            PERSISTED_TAG,
            apply_tool_result_budget,
            create_replacement_state,
        )
        from codeyx.conversation import ConversationManager, Message, ToolResultBlock

        # Assistant text just under the aggregate limit; the 3k tool result
        # on top pushes it over ONLY if msg.content is counted (and is large
        # enough that its on-disk preview shrinks it). The old code omitted
        # len(msg.content) and left the pair intact.
        big_text = "x" * (AGGREGATE_CHAR_LIMIT - 300)
        conv = ConversationManager()
        conv.history = [
            Message(role="user", content="q"),
            Message(
                role="assistant",
                content=big_text,
                tool_uses=[],
                tool_results=[
                    ToolResultBlock(tool_use_id="t1", content="y" * 3_000)
                ],
            ),
        ]

        new_conv, records = apply_tool_result_budget(
            conv, tmp_path, create_replacement_state()
        )

        replaced = [
            tr for m in new_conv.history for tr in m.tool_results
        ]
        assert any(tr.content.startswith(PERSISTED_TAG) for tr in replaced), (
            "aggregate budget ignored the current message's own content"
        )
        assert records

    def test_small_totals_left_alone(self, tmp_path):
        from codeyx.context.manager import (
            apply_tool_result_budget,
            create_replacement_state,
        )
        from codeyx.conversation import ConversationManager, Message, ToolResultBlock

        conv = ConversationManager()
        conv.history = [
            Message(role="user", content="q"),
            Message(
                role="assistant",
                content="short",
                tool_results=[ToolResultBlock(tool_use_id="t1", content="ok")],
            ),
        ]
        new_conv, _ = apply_tool_result_budget(
            conv, tmp_path, create_replacement_state()
        )
        tr = new_conv.history[1].tool_results[0]
        assert tr.content == "ok"


# ---------------------------------------------------------------------------
# Context-overflow classifier (and/or precedence)
# ---------------------------------------------------------------------------


class TestOverflowClassifier:
    def test_real_overflow_signatures_match(self):
        from codeyx.context.manager import looks_like_context_overflow

        assert looks_like_context_overflow(
            "API error (400): prompt is too long: 250000 tokens > 200000 maximum"
        )
        assert looks_like_context_overflow(
            "Error: This model's maximum context length is 8192 tokens"
        )
        assert looks_like_context_overflow("context_length_exceeded")
        assert looks_like_context_overflow("too many input tokens")

    def test_rate_limit_is_not_overflow(self):
        from codeyx.context.manager import looks_like_context_overflow

        # The bug: `... or "too many" in err_msg` classified 429s as
        # overflow and dropped summary input on a transient throttle.
        assert not looks_like_context_overflow("Error 429: too many requests")
        assert not looks_like_context_overflow("Too Many Requests")
        assert not looks_like_context_overflow("connection reset by peer")


# ---------------------------------------------------------------------------
# Empty assistant message in the max_tokens recovery branch
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal stream client scripting raw StreamEnd responses."""

    def __init__(self, stops: list[tuple[str, str]]) -> None:
        # Each entry: (stop_reason, text)
        self._stops = list(stops)
        self._i = 0
        self.max_output_tokens: int | None = None

    def set_max_output_tokens(self, value: int) -> None:
        self.max_output_tokens = value

    async def stream(self, conversation, system="", tools=None):
        from codeyx.tools.base import StreamEnd

        stop, text = (
            self._stops[self._i] if self._i < len(self._stops) else ("end_turn", "")
        )
        self._i += 1
        if text:
            from codeyx.tools.base import TextDelta

            yield TextDelta(text)
        yield StreamEnd(stop, input_tokens=10, output_tokens=5)


class TestMaxTokensRecoveryEmptyText:
    @pytest.mark.asyncio
    async def test_empty_text_does_not_append_empty_assistant_message(self):
        from codeyx.agent import Agent, RetryEvent
        from codeyx.conversation import ConversationManager
        from codeyx.tools import create_default_registry

        client = _FakeClient([
            ("max_tokens", ""),   # escalation branch: guarded by response.text
            ("max_tokens", ""),   # recovery branch: previously appended ""
            ("end_turn", "done"),
        ])
        agent = Agent(client, create_default_registry(), "anthropic", work_dir=".")
        conv = ConversationManager()
        conv.add_user_message("go")

        retries: list[str] = []
        async for ev in agent.run(conv):
            if isinstance(ev, RetryEvent):
                retries.append(ev.reason)

        assert any("escalation" in r for r in retries)
        assert any("recovery" in r for r in retries), retries
        empty_assistant = [
            m
            for m in conv.history
            if m.role == "assistant" and not m.content and not m.tool_uses
            and not m.thinking_blocks
        ]
        assert not empty_assistant, (
            "max_tokens recovery appended an empty assistant message — "
            "the next API call would 400"
        )


# ---------------------------------------------------------------------------
# TaskManager / TraceManager bounded retention
# ---------------------------------------------------------------------------


class TestTaskManagerReap:
    def _mgr(self):
        from codeyx.agents.task_manager import TaskManager

        return TaskManager()

    def _terminal(self, task_id: str, end_offset: float, status="completed"):
        from codeyx.agents.task_manager import BackgroundTask

        return BackgroundTask(
            id=task_id,
            name=task_id,
            agent=object(),
            task="t",
            status=status,
            end_time=time.monotonic() - end_offset,
        )

    def test_expired_terminal_tasks_removed_running_kept(self):
        mgr = self._mgr()
        mgr._tasks["old"] = self._terminal("old", end_offset=700.0)
        running = self._terminal("run", end_offset=0.0, status="running")
        mgr._tasks["run"] = running

        removed = mgr.reap()

        assert removed == 1
        assert "old" not in mgr._tasks
        assert "run" in mgr._tasks

    def test_recent_terminal_tasks_survive_retention_window(self):
        from codeyx.agents.task_manager import TERMINAL_TASK_RETENTION_SECONDS

        mgr = self._mgr()
        mgr._tasks["fresh"] = self._terminal(
            "fresh", end_offset=TERMINAL_TASK_RETENTION_SECONDS / 2
        )
        mgr.reap()
        assert "fresh" in mgr._tasks

    def test_hard_cap_evicts_oldest_terminal_tasks(self):
        from codeyx.agents.task_manager import MAX_TERMINAL_TASKS

        mgr = self._mgr()
        total = MAX_TERMINAL_TASKS + 10
        for i in range(total):
            mgr._tasks[f"t{i}"] = self._terminal(f"t{i}", end_offset=60.0 - i)

        mgr.reap()

        terminal = [k for k, bg in mgr._tasks.items() if bg.status != "running"]
        assert len(terminal) == MAX_TERMINAL_TASKS
        # Oldest (largest end_offset) evicted first.
        assert "t0" not in mgr._tasks
        assert f"t{total - 1}" in mgr._tasks


class TestTraceManagerPrune:
    def test_finished_nodes_capped_running_never_evicted(self):
        from codeyx.agents.trace import MAX_FINISHED_NODES, TraceManager

        tm = TraceManager()
        for i in range(MAX_FINISHED_NODES + 25):
            node = tm.create("general", parent_id=None, trace_id=f"tr{i}")
            tm.complete(node.agent_id)

        runner = tm.create("general", parent_id=None, trace_id="live")
        assert runner.status == "running"

        finished = [n for n in tm._nodes.values() if n.status != "running"]
        assert len(finished) <= MAX_FINISHED_NODES
        assert tm.get(runner.agent_id) is not None

    def test_prune_keeps_newest(self):
        from codeyx.agents.trace import MAX_FINISHED_NODES, TraceManager

        tm = TraceManager()
        first = tm.create("general", trace_id="early")
        tm.complete(first.agent_id)
        for i in range(MAX_FINISHED_NODES + 5):
            node = tm.create("general", trace_id=f"late{i}")
            tm.complete(node.agent_id)

        assert tm.get(first.agent_id) is None, "oldest finished node must go"
        recent = tm.get_tree(f"late{MAX_FINISHED_NODES + 4}")
        assert recent


# ---------------------------------------------------------------------------
# run_in_background explicit choice respected
# ---------------------------------------------------------------------------


class TestBackgroundModeResolution:
    def test_explicit_true_wins_even_for_named_agent(self):
        from codeyx.tools.agent_tool import _resolve_background_mode

        assert _resolve_background_mode(True, False, is_fork=False) is True

    def test_explicit_false_wins_even_for_fork(self):
        from codeyx.tools.agent_tool import _resolve_background_mode

        assert _resolve_background_mode(False, False, is_fork=True) is False

    def test_fork_defaults_to_background(self):
        from codeyx.tools.agent_tool import _resolve_background_mode

        assert _resolve_background_mode(None, False, is_fork=True) is True

    def test_named_agent_defaults_to_definition_flag(self):
        from codeyx.tools.agent_tool import _resolve_background_mode

        assert _resolve_background_mode(None, False, is_fork=False) is False
        assert _resolve_background_mode(None, True, is_fork=False) is True


# ---------------------------------------------------------------------------
# Mid-stream prompt commands get feedback instead of vanishing
# ---------------------------------------------------------------------------


class TestSendUserMessageMidStream:
    def _app(self, streaming: bool):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        app._streaming = streaming
        app._turn_starting = False
        app.agent = object()  # non-None sentinel
        shown: list[str] = []
        app.add_system_message = shown.append  # type: ignore[method-assign]
        return app, shown

    @pytest.mark.asyncio
    async def test_streaming_reports_instead_of_silent_drop(self):
        app, shown = self._app(streaming=True)

        app.send_user_message("/review")

        assert shown, "user must be told why the command did not run"

    @pytest.mark.asyncio
    async def test_idle_dispatches_turn_via_claim(self):
        app, shown = self._app(streaming=False)
        created: list[str] = []

        async def fake_send(text: str) -> None:
            created.append(text)

        app._send_message = fake_send  # type: ignore[method-assign]

        app.send_user_message("hello")

        await asyncio.sleep(0)
        assert created == ["hello"]
        assert not shown


# ---------------------------------------------------------------------------
# Glob/Grep honor .gitignore and skip hidden directories
# ---------------------------------------------------------------------------


def _project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("noise VALUE = 3\n", encoding="utf-8")
    build = tmp_path / "build"
    build.mkdir()
    (build / "out.py").write_text("VALUE = 4\n", encoding="utf-8")
    hidden = tmp_path / ".cache"
    hidden.mkdir()
    (hidden / "junk.py").write_text("VALUE = 5\n", encoding="utf-8")
    gh = tmp_path / ".github"
    gh.mkdir()
    (gh / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        "*.log\nbuild/\n", encoding="utf-8"
    )
    return tmp_path


class TestIgnoreSpec:
    def test_gitignore_patterns_and_hidden_dirs_filtered(self, tmp_path):
        from codeyx.tools.ignore import IgnoreSpec

        base = _project(tmp_path)
        spec = IgnoreSpec.load(base)

        assert not spec.is_ignored(("keep.py",))
        assert spec.is_ignored(("debug.log",))
        assert spec.is_ignored(("build", "out.py")), "dir rule must cover children"
        assert spec.is_ignored((".cache", "junk.py")) is False  # hidden handled separately
        assert not spec.is_ignored(("src", "app.py"))

    def test_negation_last_match_wins(self, tmp_path):
        from codeyx.tools.ignore import IgnoreSpec

        (tmp_path / ".gitignore").write_text("*.log\n!keep.log\n", encoding="utf-8")
        spec = IgnoreSpec.load(tmp_path)
        assert spec.is_ignored(("other.log",))
        assert not spec.is_ignored(("keep.log",))

    def test_path_filter_skips_hidden_dirs_but_allows_explicit_dot_segment(
        self, tmp_path
    ):
        from codeyx.tools.ignore import build_path_filter

        base = _project(tmp_path)

        generic = build_path_filter(base, "**/*.py")
        assert generic(base / "keep.py")
        assert not generic(base / "debug.log")
        assert not generic(base / "build" / "out.py")
        assert not generic(base / ".cache" / "junk.py")

        explicit = build_path_filter(base, ".github/**")
        assert explicit(base / ".github" / "ci.yml"), (
            "pattern naming a dot-directory explicitly must still match"
        )


class TestGlobGrepIntegration:
    @pytest.mark.asyncio
    async def test_glob_excludes_ignored_and_hidden(self, tmp_path):
        from codeyx.tools.glob import Glob

        base = _project(tmp_path)
        result = await asyncio.wait_for(
            Glob().execute(type("P", (), {"pattern": "**/*.py", "path": str(base)})()),
            timeout=5,
        )
        files = set(result.output.splitlines())
        assert files == {"keep.py", "src/app.py"}

    @pytest.mark.asyncio
    async def test_grep_skips_ignored_files(self, tmp_path):
        from codeyx.tools.grep import Grep

        base = _project(tmp_path)
        result = await asyncio.wait_for(
            Grep().execute(
                type("P", (), {"pattern": "VALUE", "path": str(base), "include": ""})()
            ),
            timeout=5,
        )
        paths = {line.split(":")[0] for line in result.output.splitlines()}
        assert "keep.py" in paths
        assert "src/app.py" in paths
        assert not any("debug.log" in p for p in paths)
        assert not any("build/" in p for p in paths)
