"""Regression tests for 2026-08 audit Wave 1: session JSONL poisoning on
crash-mid-tool-call, and the Ctrl+Q shutdown bypass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codeyx.conversation import ConversationManager
from codeyx.memory.session import (
    RecordType,
    SessionManager,
    SessionMeta,
    SessionRecord,
    validate_message_chain,
)

# ---------------------------------------------------------------------------
# JSONL record helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _user_record(text: str) -> dict:
    return {"type": "user", "content": text, "timestamp": _now()}


def _assistant_record(content) -> dict:
    return {"type": "assistant", "content": content, "timestamp": _now()}


def _tool_result_record(tool_use_id: str, output: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": output,
        "timestamp": _now(),
    }


def _dangling_tool_use_record(tool_id: str = "tu1") -> dict:
    """Assistant message carrying a tool_use block — the exact state a crash
    mid-tool-call leaves on disk."""
    return _assistant_record([
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {}},
    ])


def _make_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(work_dir=str(tmp_path))


def _write_meta(sessions_dir: Path, session_id: str) -> None:
    meta_dir = sessions_dir
    meta_dir.mkdir(parents=True, exist_ok=True)
    SessionMeta(id=session_id).save(meta_dir / f"{session_id}.meta")


def _read_records(jsonl: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# JSONL poisoning repair
# ---------------------------------------------------------------------------

SESSIONS_SUBDIR = ".codeyx/sessions"


class TestResumePoisonedJsonl:
    def test_resume_repairs_poisoned_tail(self, tmp_path):
        """Crash between assistant tool_use and its tool_result used to make
        every future append unrecoverable: resume re-discarded the poisoned
        tail each time while new records kept piling up behind it. Resume
        must rewrite the file to exactly the valid prefix."""
        manager = _make_manager(tmp_path)
        sessions_dir = tmp_path / SESSIONS_SUBDIR
        jsonl = sessions_dir / "s1.jsonl"
        _write_meta(sessions_dir, "s1")
        # Crash happened BEFORE the tool_result was persisted: the dangling
        # tool_use freezes validation here, so everything after — including
        # later complete turns — is discarded on every read.
        _write_jsonl(jsonl, [
            _user_record("hi"),
            _assistant_record("let me check"),
            _dangling_tool_use_record("tu1"),  # crash point
            _assistant_record("all finished"),  # unreachable forever after
            _user_record("next question"),
        ])

        result = manager.resume("s1")
        assert result is not None

        surviving = _read_records(jsonl)
        # The assistant record carrying the unanswered tool_use is itself
        # unplayable, so the valid prefix is just the first two records.
        assert len(surviving) == 2, (
            f"poisoned tail survived on disk: {len(surviving)} records"
        )

    def test_new_appends_survive_subsequent_resume(self, tmp_path):
        """The full audit scenario: crash → resume → continue conversation →
        resume again. Records appended after the repair must not be thrown
        away by the next validation pass."""
        manager = _make_manager(tmp_path)
        sessions_dir = tmp_path / SESSIONS_SUBDIR
        jsonl = sessions_dir / "s2.jsonl"
        _write_meta(sessions_dir, "s2")
        _write_jsonl(jsonl, [
            _user_record("q1"),
            _dangling_tool_use_record("bad"),  # crash: result never written
            _assistant_record("a1"),  # poisoned tail
        ])

        first = manager.resume("s2")
        assert first is not None

        conv = ConversationManager()
        for m in first.messages:
            conv.history.append(m)
        conv.add_user_message("q2")
        first.session.append(conv.history[-1])
        first.session.close()

        second = manager.resume("s2")
        assert second is not None
        assert any(m.content == "q2" for m in second.messages), (
            "post-repair appends were discarded by the next resume"
        )

    def test_clean_file_is_not_rewritten(self, tmp_path):
        manager = _make_manager(tmp_path)
        sessions_dir = tmp_path / SESSIONS_SUBDIR
        jsonl = sessions_dir / "s3.jsonl"
        _write_meta(sessions_dir, "s3")
        _write_jsonl(jsonl, [_user_record("hi"), _assistant_record("hello")])
        before = jsonl.read_bytes()

        result = manager.resume("s3")
        assert result is not None
        assert jsonl.read_bytes() == before, "clean file must be left untouched"

    def test_validate_message_chain_counts(self):
        records = [
            _user_record("u"),
            _dangling_tool_use_record("a"),
            _tool_result_record("a", "ok"),
            _assistant_record("done"),
        ]
        parsed = [SessionRecord.from_jsonl(json.dumps(r)) for r in records]
        assert all(p is not None for p in parsed)
        assert validate_message_chain(parsed) == 4

        dangling = [SessionRecord.from_jsonl(json.dumps(r)) for r in records[:2]]
        assert validate_message_chain(dangling) == 1


# ---------------------------------------------------------------------------
# Ctrl+Q routes through the shared shutdown path
# ---------------------------------------------------------------------------


class TestQuitShutdownRouting:
    @pytest.mark.asyncio
    async def test_action_quit_delegates_to_shutdown(self):
        """Ctrl+Q resolves to Textual's stock quit action; CodeYXApp.action_quit
        must delegate to the full teardown instead of exit()-ing directly."""
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        calls: list[str] = []

        async def fake_shutdown():
            calls.append("shutdown")

        app._shutdown = fake_shutdown  # type: ignore[method-assign]
        await CodeYXApp.action_quit(app)  # type: ignore[arg-type]
        assert calls == ["shutdown"]

    @pytest.mark.asyncio
    async def test_action_handle_ctrl_c_idle_delegates_to_shutdown(self):
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        app._streaming = False
        calls: list[str] = []

        async def fake_shutdown():
            calls.append("shutdown")

        app._shutdown = fake_shutdown  # type: ignore[method-assign]
        await CodeYXApp.action_handle_ctrl_c(app)  # type: ignore[arg-type]
        assert calls == ["shutdown"]

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self):
        """Quit racing ctrl+c must not run cleanup (memory extraction, MCP
        shutdown, …) twice."""
        from codeyx.app import CodeYXApp

        app = object.__new__(CodeYXApp)
        calls: list[str] = []

        class _Agent:
            memory_manager = object()

            async def _extract_memories(self, conversation):
                calls.append("extract")

        app.agent = _Agent()
        app.conversation = ConversationManager()
        app.hook_engine = None
        app._stale_cleanup_task = None
        app.worktree_manager = None
        app.session = None
        exits: list[bool] = []
        app.exit = lambda: exits.append(True)  # type: ignore[method-assign]

        async def fake_shutdown_mcp():
            calls.append("mcp")

        app._shutdown_mcp = fake_shutdown_mcp  # type: ignore[method-assign]

        await CodeYXApp._shutdown(app)
        await CodeYXApp._shutdown(app)

        assert calls.count("extract") == 1, "memory extraction ran twice"
        assert calls.count("mcp") == 1, "MCP shutdown ran twice"
        assert exits

    def test_team_member_record_type_exists(self):
        # Sanity: RecordType members used by validate_message_chain.
        assert RecordType.ASSISTANT.value == "assistant"
        assert RecordType.TOOL_RESULT.value == "tool_result"
