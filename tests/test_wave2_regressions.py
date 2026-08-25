"""Regression tests for the 2026-08 audit Wave 2 fixes: compact threshold,
ReadFile cache freshness, thinking-block persistence, SharedTaskStore
integrity, streaming-command guard, notification-poll shutdown, teammate
spawn rollback and dirty-worktree preservation on team delete."""

from __future__ import annotations

import asyncio
import json

import pytest

# ---------------------------------------------------------------------------
# Compact threshold on small context windows
# ---------------------------------------------------------------------------


class TestCompactThreshold:
    def test_small_window_gets_positive_threshold(self):
        from codeyx.context.manager import compute_compact_threshold

        # 8k window cannot absorb SUMMARY_OUTPUT_RESERVE + margin; the old
        # code returned a negative threshold, tripping auto-compact (and
        # wiping history) after every single turn.
        assert compute_compact_threshold(8_000) > 0
        assert compute_compact_threshold(8_000, manual=True) > 0

    def test_large_window_keeps_reserve_semantics(self):
        from codeyx.context.manager import (
            AUTO_COMPACT_SAFETY_MARGIN,
            SUMMARY_OUTPUT_RESERVE,
            compute_compact_threshold,
        )

        assert compute_compact_threshold(200_000) == (
            200_000 - SUMMARY_OUTPUT_RESERVE - AUTO_COMPACT_SAFETY_MARGIN
        )


# ---------------------------------------------------------------------------
# ReadFile cache freshness across external edits
# ---------------------------------------------------------------------------


class TestFileCacheFreshness:
    def test_external_edit_invalidates_without_invalidate_call(self, tmp_path):
        from codeyx.cache import FileCache

        f = tmp_path / "note.txt"
        f.write_text("v1", encoding="utf-8")
        cache = FileCache()
        cache.put(str(f), "v1")

        assert cache.get_fresh(str(f)) == "v1"

        f.write_text("v2", encoding="utf-8")  # e.g. Bash `sed -i`
        # Force a distinct mtime even on coarse filesystems.
        st = f.stat()
        import os

        os.utime(f, ns=(st.st_atime_ns + 2_000_000, st.st_mtime_ns + 2_000_000))

        assert cache.get_fresh(str(f)) is None, (
            "external edit must not be served stale content forever"
        )

    def test_missing_file_invalidates(self, tmp_path):
        from codeyx.cache import FileCache

        f = tmp_path / "gone.txt"
        f.write_text("x", encoding="utf-8")
        cache = FileCache()
        cache.put(str(f), "x")
        f.unlink()
        assert cache.get_fresh(str(f)) is None

    def test_readfile_tool_serves_fresh_content(self, tmp_path):
        from codeyx.cache import FileCache
        from codeyx.tools.read_file import Params, ReadFile

        f = tmp_path / "doc.txt"
        f.write_text("stale", encoding="utf-8")
        tool = ReadFile(file_cache=FileCache())

        import asyncio

        first = asyncio.run(tool.execute(Params(file_path=str(f))))
        assert "stale" in first.output

        f.write_text("fresh", encoding="utf-8")
        second = asyncio.run(tool.execute(Params(file_path=str(f))))
        assert "fresh" in second.output, "ReadFile served cached stale text"


# ---------------------------------------------------------------------------
# Thinking blocks survive session persistence
# ---------------------------------------------------------------------------


def _roundtrip(records):
    from codeyx.memory.session import SessionRecord, records_to_messages

    return records_to_messages(
        [SessionRecord.from_jsonl(r.to_jsonl()) for r in records]
    )


class TestThinkingBlockPersistence:
    def _make_message(self):
        from codeyx.conversation import Message, ThinkingBlock, ToolUseBlock

        return Message(
            role="assistant",
            content="Let me check.",
            tool_uses=[
                ToolUseBlock(tool_use_id="t1", tool_name="Bash", arguments={})
            ],
            thinking_blocks=[
                ThinkingBlock(thinking="reasoning...", signature="sig123")
            ],
        )

    def test_assistant_tool_use_turn_roundtrips_thinking(self):
        from codeyx.memory.session import SessionRecord

        msg = self._make_message()
        records = SessionRecord.from_message(msg)
        messages = _roundtrip(records)

        restored = next(m for m in messages if m.role == "assistant")
        assert len(restored.thinking_blocks) == 1
        assert restored.thinking_blocks[0].thinking == "reasoning..."
        assert restored.thinking_blocks[0].signature == "sig123"
        assert restored.tool_uses[0].tool_use_id == "t1"
        # Anthropic ordering requirement: thinking precedes tool_use.
        blocks = records[0].content
        types = [b["type"] for b in blocks]
        assert types.index("thinking") < types.index("tool_use")

    def test_anthropic_serializer_emits_restored_thinking(self):
        from codeyx.memory.session import SessionRecord

        messages = _roundtrip(SessionRecord.from_message(self._make_message()))
        conv_like = [m for m in messages if m.role == "assistant"][0]

        from codeyx.conversation import ConversationManager

        cm = ConversationManager()
        cm.history = [conv_like]
        serialized = cm.serialize("anthropic")
        content = serialized[0]["content"]
        assert any(b.get("type") == "thinking" for b in content), (
            "restored thinking block lost at serialization — Anthropic "
            "rejects replayed assistant tool_use turns without it"
        )


# ---------------------------------------------------------------------------
# SharedTaskStore: atomic writes + tolerant load + cross-instance lock
# ---------------------------------------------------------------------------


class TestSharedTaskStoreIntegrity:
    def _store(self, path):
        from codeyx.teams.shared_task import SharedTaskStore

        return SharedTaskStore(path)

    def test_save_is_atomic_no_tmp_left_behind(self, tmp_path):
        store = self._store(tmp_path / "tasks.json")
        store.create("task one")
        leftovers = [
            p for p in tmp_path.iterdir() if p.name.endswith(".tmp")
        ]
        assert not leftovers, "atomic save must clean up its temp file"
        data = json.loads((tmp_path / "tasks.json").read_text())
        assert data["tasks"][0]["title"] == "task one"

    def test_corrupt_file_backed_up_not_crashing(self, tmp_path):
        path = tmp_path / "tasks.json"
        path.write_text("{not valid json!!", encoding="utf-8")
        store = self._store(path)  # must not raise
        task = store.create("after corruption")
        assert path.exists()
        backup = tmp_path / "tasks.json.corrupt"
        assert backup.exists(), "damaged bytes preserved for inspection"
        assert task.id == "1"

    def test_second_instance_sees_first_updates(self, tmp_path):
        # Simulates two teammate processes sharing one tasks.json.
        s1 = self._store(tmp_path / "tasks.json")
        t1 = s1.create("from process 1")
        s2 = self._store(tmp_path / "tasks.json")
        got = s2.get(t1.id)
        assert got is not None and got.title == "from process 1"

    def test_interleaved_writes_do_not_lose_updates(self, tmp_path):
        s1 = self._store(tmp_path / "tasks.json")
        s2 = self._store(tmp_path / "tasks.json")
        a = s1.create("a")
        b = s2.create("b")
        merged_ids = {t.id for t in s1.list_tasks()} | {
            t.id for t in s2.list_tasks()
        }
        assert {a.id, b.id} <= merged_ids, (
            "cross-process write lost an update"
        )


# ---------------------------------------------------------------------------
# Streaming guard for conversation-mutating commands
# ---------------------------------------------------------------------------


class TestStreamingCommandGuard:
    def test_unsafe_commands_detected(self):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        assert app._is_stream_unsafe_command("/clear")
        assert app._is_stream_unsafe_command("/compact")
        assert app._is_stream_unsafe_command("/session resume abc")
        assert not app._is_stream_unsafe_command("/help")
        assert not app._is_stream_unsafe_command("/model")
        assert not app._is_stream_unsafe_command("plain message")

    @pytest.mark.asyncio
    async def test_handler_refuses_while_streaming(self):
        from codeyx.app import ChatInput, CodeYXApp

        app = object.__new__(CodeYXApp)
        app._streaming = True
        app._agent_task = None
        shown: list[str] = []
        dispatched: list[str] = []

        app._show_system_message = shown.append  # type: ignore[method-assign]

        async def fake_dispatch(text: str) -> None:
            dispatched.append(text)

        app._dispatch_command = fake_dispatch  # type: ignore[method-assign]

        await CodeYXApp.on_chat_input_submitted(app, ChatInput.Submitted("/clear"))
        assert dispatched == [], "/clear must be refused during streaming"
        assert shown, "user must be told why"

        await CodeYXApp.on_chat_input_submitted(app, ChatInput.Submitted("/help"))
        assert dispatched == ["/help"], "safe commands still dispatch"


# ---------------------------------------------------------------------------
# Shutdown cancels the notification poller
# ---------------------------------------------------------------------------


class TestShutdownCancelsPoller:
    @pytest.mark.asyncio
    async def test_shutdown_cancels_background_tasks(self):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        calls: list[str] = []

        class _Agent:
            memory_manager = None

        class _FakeTask:
            def __init__(self, name: str) -> None:
                self.name = name
                self.cancelled = False

            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                self.cancelled = True
                calls.append(f"cancel:{self.name}")

        app.agent = _Agent()
        app.conversation = None
        app.hook_engine = None
        app._stale_cleanup_task = None
        app.worktree_manager = None
        app.session = None
        # NOTE: team_manager intentionally left unset (hasattr-guarded).
        app._notification_check_task = _FakeTask("poll")
        app._boot_prompt_task = _FakeTask("boot")
        exits: list[bool] = []
        app.exit = lambda: exits.append(True)  # type: ignore[method-assign]
        app._shutdown_mcp = lambda: asyncio.sleep(0)  # type: ignore[method-assign]

        await CodeYXApp._shutdown(app)

        assert calls == ["cancel:poll", "cancel:boot"], (
            "notification poller / boot prompt left alive past shutdown"
        )
        assert exits


# ---------------------------------------------------------------------------
# Teammate spawn failure rolls back registration; delete keeps dirty trees
# ---------------------------------------------------------------------------


class TestTeamLifecycle:
    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        return tmp_path

    def test_remove_member_undo_registration(self, isolated_home):
        from codeyx.teams.manager import TeamManager
        from codeyx.teams.models import TeammateInfo

        mgr = TeamManager()
        team = mgr.create_team("rollback", lead_agent_id="lead")
        member = TeammateInfo(
            name="worker",
            agent_id="agent-1",
            agent_type="general",
            model="",
            worktree_path="/unused",
            backend_type="in-process",
            is_active=True,
        )
        mgr.register_member(team.name, member)
        assert mgr.get_team(team.name).members

        mgr.remove_member(team.name, member)

        assert mgr.get_team(team.name).members == []
        assert mgr.get_team_for_teammate("agent-1") is None

        # The whole point: delete_team must work again after rollback.
        mgr.delete_team(team.name)

    def test_cleanup_worktree_preserves_dirty_tree(self, isolated_home, tmp_path):
        import subprocess

        from codeyx.teams.manager import TeamManager

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "f.txt").write_text("base")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

        wt = repo.parent / "wt-dirty"
        subprocess.run(
            ["git", "worktree", "add", "-q", str(wt), "-b", "wt-dirty"],
            cwd=repo,
            check=True,
        )
        (wt / "uncommitted.txt").write_text("precious work")

        mgr = TeamManager()
        mgr._cleanup_worktree(str(wt))

        assert wt.exists(), "--force removal destroyed uncommitted work"
        assert (wt / "uncommitted.txt").read_text() == "precious work"


# ---------------------------------------------------------------------------
# Turn-start mutex
# ---------------------------------------------------------------------------


class TestTurnClaimMutex:
    def test_claim_excludes_second_scheduler(self):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        app._streaming = False
        app._turn_starting = False

        assert app._try_claim_turn() is True
        assert app._try_claim_turn() is False, "second scheduler must lose"

    def test_streaming_also_blocks_claim(self):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        app._streaming = True
        app._turn_starting = False
        assert app._try_claim_turn() is False
