"""Regression tests for the 终审 (final adversarial review) fixes over the
three-wave diff: noninteractive path normalization, iTerm2 spawn quoting,
Layer-1 allowlist write-capable entries, persisted-id length/collision,
FileCache stat-before-read, overflow signatures, gitignore parent-exclusion
and segment-confined wildcards, sub-agent worktree-tool exclusion, /worktree
command re-rooting hooks, and SharedTaskStore structural quarantine."""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeyx.client import LLMClient
from codeyx.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from codeyx.tools import create_default_registry
from codeyx.tools.base import StreamEnd, TextDelta, ToolCallComplete


class MockLLMClient(LLMClient):
    def __init__(self, responses: list[list]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    async def stream(self, conversation, system="", tools=None):
        if self._call_index >= len(self._responses):
            yield TextDelta(text="No more responses")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
            return
        for e in self._responses[self._call_index]:
            yield e
        self._call_index += 1


# ---------------------------------------------------------------------------
# High: run_to_completion path never normalized relative path arguments
# ---------------------------------------------------------------------------


class TestNoninteractivePathNormalization:
    @pytest.mark.asyncio
    async def test_relative_write_lands_in_sandbox_root(
        self, tmp_path, monkeypatch
    ):
        """Sub-agents/teammates execute via run_to_completion; a relative
        file_path must be checked AND written against the derived sandbox
        root, not the unchanged process CWD."""
        from codeyx.agent import Agent

        repo = tmp_path / "repo"
        repo.mkdir()
        cwd_dir = tmp_path / "launch-dir"
        cwd_dir.mkdir()
        monkeypatch.chdir(cwd_dir)

        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(str(repo)),
            rule_engine=RuleEngine(),
            mode=PermissionMode.BYPASS,
        )
        client = MockLLMClient([
            [
                ToolCallComplete(
                    "t1", "WriteFile", {"file_path": "out.txt", "content": "hi"}
                ),
                StreamEnd("end_turn", input_tokens=10, output_tokens=20),
            ],
        ])
        agent = Agent(
            client,
            create_default_registry(),
            "anthropic",
            work_dir=str(repo),
            permission_checker=checker,
        )

        result = await agent.run_to_completion("write out.txt")

        assert not result.startswith("Error"), result
        assert (repo / "out.txt").read_text(encoding="utf-8") == "hi"
        assert not (cwd_dir / "out.txt").exists(), (
            "noninteractive execution wrote outside the sandbox root"
        )


# ---------------------------------------------------------------------------
# High: iTerm2 spawn quoting (CHANGELOG claimed this fix; now it exists)
# ---------------------------------------------------------------------------


class TestIterm2SpawnQuoting:
    def test_command_argument_survives_shell_parsing(self, monkeypatch):
        from codeyx.teams import spawn_iterm2

        captured: list[list[str]] = []

        def fake_run_it2(*args: str) -> str:
            captured.append(list(args))
            return "sess-1"

        monkeypatch.setattr(spawn_iterm2, "_run_it2", fake_run_it2)

        malicious_prompt = "x'; rm -rf / #"
        spawn_iterm2.spawn_iterm2_teammate(
            team_name="t",
            teammate_name="w",
            worktree_path="/tmp/wt",
            prompt=malicious_prompt,
        )

        assert captured, "it2 must be invoked"
        assert captured[0][0] == "split-pane"
        command = captured[0][2]
        tokens = shlex.split(command)
        # zsh -c receives the cli command as ONE intact string.
        assert tokens[:2] == ["/bin/zsh", "-c"]
        inner_tokens = shlex.split(tokens[2])
        assert "x'; rm -rf / #" in inner_tokens, (
            "payload must survive as a single quoted argument"
        )
        assert "rm" not in inner_tokens and ";" not in inner_tokens, (
            "injection payload escaped to top-level command position"
        )


# ---------------------------------------------------------------------------
# Medium: Layer-1 allowlist must not auto-allow writes or ref mutations
# ---------------------------------------------------------------------------


class TestSafeCommandAllowlist:
    def test_write_capable_entries_removed(self):
        from codeyx.permissions.dangerous import is_safe_command

        assert not is_safe_command("sort -o ~/.bashrc payload.txt")
        assert not is_safe_command("uniq in.txt ~/.bashrc")
        assert not is_safe_command("git branch injected-branch")
        assert not is_safe_command("git tag v1 -m x")
        assert not is_safe_command("git remote add origin evil")

    def test_read_only_entries_still_allowed(self):
        from codeyx.permissions.dangerous import is_safe_command

        assert is_safe_command("git status")
        assert is_safe_command("git log --oneline")
        assert is_safe_command("cat README.md")


# ---------------------------------------------------------------------------
# Medium: persisted-output filenames — bounded length, no collision aliasing
# ---------------------------------------------------------------------------


class TestPersistToolResultId:
    def test_oversized_tool_use_id_does_not_crash(self, tmp_path):
        from codeyx.context.manager import persist_tool_result

        huge_id = "toolu_" + "/".join(["a" * 40] * 20)  # > NAME_MAX raw
        path = persist_tool_result(huge_id, "payload", tmp_path)

        assert path.parent == tmp_path
        assert len(path.name) < 120
        assert path.read_text(encoding="utf-8") == "payload"

    def test_colliding_sanitized_ids_get_distinct_files(self, tmp_path):
        from codeyx.context.manager import persist_tool_result

        p1 = persist_tool_result("toolu_01/a", "content A", tmp_path)
        p2 = persist_tool_result("toolu_01.a", "content B", tmp_path)

        assert p1 != p2, "distinct ids must not alias one persisted file"
        assert p1.read_text(encoding="utf-8") == "content A"
        assert p2.read_text(encoding="utf-8") == "content B"

    def test_same_id_is_idempotent(self, tmp_path):
        from codeyx.context.manager import persist_tool_result

        p1 = persist_tool_result("same-id", "first", tmp_path)
        p2 = persist_tool_result("same-id", "second-write-ignored", tmp_path)
        assert p1 == p2
        assert p1.read_text(encoding="utf-8") == "first"


# ---------------------------------------------------------------------------
# Medium: FileCache metadata must be captured before the read
# ---------------------------------------------------------------------------


class TestFileCachePutWithMeta:
    def test_put_with_meta_roundtrip_and_invalidation(self, tmp_path):
        import os

        from codeyx.cache import FileCache

        f = tmp_path / "f.txt"
        f.write_text("v1", encoding="utf-8")
        st_before = f.stat()

        cache = FileCache()
        cache.put_with_meta(str(f), "v1", st_before.st_mtime_ns, st_before.st_size)
        assert cache.get_fresh(str(f)) == "v1"

        f.write_text("v2", encoding="utf-8")
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns + 2_000_000, st.st_mtime_ns + 2_000_000))
        assert cache.get_fresh(str(f)) is None

    def test_put_with_meta_rejects_mismatched_meta(self, tmp_path):
        from codeyx.cache import FileCache

        f = tmp_path / "g.txt"
        f.write_text("x", encoding="utf-8")
        cache = FileCache()
        cache.put_with_meta(str(f), "x", mtime_ns=1, size=999)
        assert cache.get_fresh(str(f)) is None, (
            "stale meta must not validate against the real file"
        )


# ---------------------------------------------------------------------------
# Overflow classifier: additional openai-compat families
# ---------------------------------------------------------------------------


class TestOverflowSignaturesExtended:
    def test_gemini_llamacpp_bedrock_signatures(self):
        from codeyx.context.manager import looks_like_context_overflow

        assert looks_like_context_overflow(
            "The input token count (250000) exceeds the maximum number of "
            "tokens allowed (200000)."
        )
        assert looks_like_context_overflow(
            "error: requested tokens exceed context window"
        )
        assert looks_like_context_overflow("Input is too long for requested model")

    def test_rate_limit_still_not_overflow(self):
        from codeyx.context.manager import looks_like_context_overflow

        assert not looks_like_context_overflow("Error 429: too many requests")


# ---------------------------------------------------------------------------
# gitignore: excluded parents cannot be re-included; '*' stays in-segment
# ---------------------------------------------------------------------------


class TestGitignoreSemantics:
    def _spec(self, tmp_path, content: str):
        from codeyx.tools.ignore import IgnoreSpec

        (tmp_path / ".gitignore").write_text(content, encoding="utf-8")
        return IgnoreSpec.load(tmp_path)

    def test_negation_cannot_reinclude_under_excluded_dir(self, tmp_path):
        spec = self._spec(tmp_path, "logs/\n!keep.log\n")
        assert spec.is_ignored(("logs", "keep.log")), (
            "git forbids re-including files under an excluded directory"
        )
        assert not spec.is_ignored(("keep.log",)), "top-level negation still works"

    def test_star_does_not_cross_slash(self, tmp_path):
        spec = self._spec(tmp_path, "src/*.py\n")
        assert spec.is_ignored(("src", "x.py"))
        assert not spec.is_ignored(("src", "sub", "y.py")), (
            "fnmatch-style '*' crossed directories and over-hid nested sources"
        )

    def test_doublestar_segment_spans_directories(self, tmp_path):
        spec = self._spec(tmp_path, "**/gen.py\n")
        assert spec.is_ignored(("a", "gen.py"))
        assert spec.is_ignored(("gen.py",))

    def test_question_mark_confined_to_segment(self, tmp_path):
        spec = self._spec(tmp_path, "a?b.txt\n")
        assert spec.is_ignored(("axb.txt",)), "'?' matches one in-segment char"
        assert not spec.is_ignored(("a", "b.txt")), (
            "'?' must not consume the '/' separator"
        )
        # Bare patterns still match at any depth (git basename semantics).
        deep = self._spec(tmp_path, "?.py\n")
        assert deep.is_ignored(("sub", "z.py"))


# ---------------------------------------------------------------------------
# Sub-agents and teammates never receive session-root tools
# ---------------------------------------------------------------------------


class TestSessionRootToolsExcluded:
    def _registry_with_worktree_tools(self):
        from codeyx.agents.parser import AgentDef
        from codeyx.agents.tool_filter import resolve_agent_tools
        from codeyx.tools.enter_worktree import EnterWorktreeTool
        from codeyx.tools.exit_worktree import ExitWorktreeTool
        from codeyx.worktree.manager import WorktreeManager

        registry = create_default_registry()
        mgr = WorktreeManager(str(Path.cwd()))
        registry.register(EnterWorktreeTool(worktree_manager=mgr))
        registry.register(ExitWorktreeTool(worktree_manager=mgr))
        definition = AgentDef(
            agent_type="general",
            when_to_use="t",
            system_prompt="",
            disallowed_tools=[],
            model="inherit",
            max_turns=5,
            permission_mode="dontAsk",
            source="builtin",
        )
        return resolve_agent_tools(registry, definition)

    def test_foreground_subagent_registry_has_no_worktree_tools(self):
        names = {t.name for t in self._registry_with_worktree_tools().list_tools()}
        assert "EnterWorktree" not in names
        assert "ExitWorktree" not in names
        # Sanity: ordinary tools survive.
        assert "ReadFile" in names

    def test_background_whitelist_no_longer_lists_them(self):
        from codeyx.agents.tool_filter import ASYNC_AGENT_ALLOWED_TOOLS

        assert "EnterWorktree" not in ASYNC_AGENT_ALLOWED_TOOLS
        assert "ExitWorktree" not in ASYNC_AGENT_ALLOWED_TOOLS

    def test_teammate_registries_have_no_worktree_tools(self):
        from unittest.mock import MagicMock

        from codeyx.agents.tool_filter import build_teammate_tools

        parent = self._registry_with_worktree_tools()
        team_manager = MagicMock()

        for backend in ("in-process", "tmux"):
            reg = build_teammate_tools(
                parent, team_manager, "team", "aid", "aname", backend
            )
            names = {t.name for t in reg.list_tools()}
            assert "EnterWorktree" not in names, backend
            assert "ExitWorktree" not in names, backend


# ---------------------------------------------------------------------------
# /worktree slash-command uses the same re-rooting hooks as the tools
# ---------------------------------------------------------------------------


class TestWorktreeCommandRooting:
    def _ctx(self):
        messages: list[str] = []
        ui = SimpleNamespace(add_system_message=messages.append)
        ctx = SimpleNamespace(args="", agent=None, ui=ui)
        return ctx, messages

    @pytest.mark.asyncio
    async def test_enter_invokes_host_apply_hook(self, tmp_path):
        from codeyx.commands.handlers.worktree import create_worktree_command

        entered: list[str] = []
        applied: list[str] = []

        class FakeManager:
            current_session = None

            def get_current_session(self):
                return None

            async def enter(self, name):
                entered.append(name)
                return SimpleNamespace(worktree_path=str(tmp_path / name), worktree_name=name)

        async def apply_root(session) -> None:
            applied.append(session.worktree_path)

        cmd = create_worktree_command(
            FakeManager(), apply_root=apply_root, restore_root=None
        )
        ctx, messages = self._ctx()
        ctx.args = "enter wt-a"

        await cmd.handler(ctx)

        assert entered == ["wt-a"]
        assert applied == [str(tmp_path / "wt-a")], (
            "/worktree enter must re-root through the host callback"
        )

    @pytest.mark.asyncio
    async def test_exit_invokes_host_restore_hook(self):
        from codeyx.commands.handlers.worktree import create_worktree_command

        restored: list[str] = []

        session = SimpleNamespace(
            worktree_name="wt-a", original_cwd="/orig", worktree_path="/wt"
        )

        class FakeManager:
            current_session = session

            def get_current_session(self):
                return session

            async def exit(self, name, action="keep", discard_changes=False):
                pass

        async def restore_root(s) -> None:
            restored.append(s.original_cwd)

        cmd = create_worktree_command(
            FakeManager(), apply_root=None, restore_root=restore_root
        )
        ctx, messages = self._ctx()
        ctx.args = "exit"

        await cmd.handler(ctx)

        assert restored == ["/orig"], "/worktree exit must restore via callback"

    @pytest.mark.asyncio
    async def test_enter_refuses_when_session_active(self):
        from codeyx.commands.handlers.worktree import create_worktree_command

        active = SimpleNamespace(worktree_name="active")

        class FakeManager:
            current_session = active

            def get_current_session(self):
                return active

            async def enter(self, name):  # pragma: no cover - must not run
                raise AssertionError("enter must be refused")

        cmd = create_worktree_command(FakeManager())
        ctx, messages = self._ctx()
        ctx.args = "enter other"

        await cmd.handler(ctx)

        assert any("已处于 worktree 会话" in m for m in messages)


# ---------------------------------------------------------------------------
# SharedTaskStore quarantines structurally-wrong JSON too
# ---------------------------------------------------------------------------


class TestSharedTaskStoreStructuralDamage:
    @pytest.mark.parametrize(
        "payload",
        [
            '{"tasks": ["not-a-dict"], "next_id": 1}',
            '{"tasks": {"1": {}}, "next_id": 1}',
            '{"tasks": [], "next_id": "abc"}',
            '["root", "is", "a", "list"]',
        ],
    )
    def test_wrong_shape_quarantined_and_store_usable(self, tmp_path, payload):
        from codeyx.teams.shared_task import SharedTaskStore

        path = tmp_path / "tasks.json"
        path.write_text(payload, encoding="utf-8")

        store = SharedTaskStore(path)  # must not raise

        task = store.create("after damage")
        backup = tmp_path / "tasks.json.corrupt"
        assert backup.exists(), "structurally damaged bytes must be preserved"
        assert task.id == "1"

    def test_valid_store_still_loads(self, tmp_path):
        from codeyx.teams.shared_task import SharedTaskStore

        path = tmp_path / "tasks.json"
        store = SharedTaskStore(path)
        created = store.create("real work")

        reloaded = SharedTaskStore(path)
        assert reloaded.get(created.id) is not None
