"""Regression tests for 2026-08 audit Wave 1: sandbox-root vs CWD divergence.

Worktree-isolated agents run with a sandbox derived from their worktree while
the process CWD never follows. Relative path arguments must resolve against
the session's actual root, and EnterWorktree/ExitWorktree must re-root CWD,
agent.work_dir and the sandbox together."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeyx.agent import Agent, PermissionRequest, ToolResultEvent
from codeyx.client import LLMClient
from codeyx.conversation import ConversationManager
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
# PathSandbox.rebase
# ---------------------------------------------------------------------------


class TestSandboxRebase:
    def test_rebase_moves_project_root(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        wt = tmp_path / "wt"
        wt.mkdir()

        sandbox = PathSandbox(str(repo))
        assert sandbox.project_root == repo.resolve()

        sandbox.rebase(str(wt))
        assert sandbox.project_root == wt.resolve()
        assert repo.resolve() not in sandbox.allowed_roots

    def test_rebased_root_resolves_relative_paths(self, tmp_path):
        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        (wt / "src").mkdir(parents=True)

        sandbox = PathSandbox(str(repo))
        sandbox.rebase(str(wt))

        ok, err = sandbox.check("src/main.py")
        assert ok, err

        ok, err = sandbox.check("../outside.txt")
        assert not ok


# ---------------------------------------------------------------------------
# Agent._normalize_path_arguments
# ---------------------------------------------------------------------------


class TestNormalizePathArguments:
    def _agent_at(self, root: Path) -> Agent:
        agent = Agent.__new__(Agent)
        agent.permission_checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(str(root)),
            rule_engine=RuleEngine(),
            mode=PermissionMode.BYPASS,
        )
        return agent

    def test_relative_file_path_resolves_to_sandbox_root(self, tmp_path):
        agent = self._agent_at(tmp_path)
        tc = ToolCallComplete("t1", "WriteFile", {"file_path": "out.txt", "content": "x"})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["file_path"] == str(tmp_path / "out.txt")

    def test_absolute_file_path_untouched(self, tmp_path):
        agent = self._agent_at(tmp_path)
        absolute = str(tmp_path.parent / "elsewhere.txt")
        tc = ToolCallComplete("t1", "ReadFile", {"file_path": absolute})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["file_path"] == absolute

    def test_generic_path_argument_resolved(self, tmp_path):
        agent = self._agent_at(tmp_path)
        tc = ToolCallComplete("t1", "Glob", {"pattern": "*.py", "path": "src"})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["path"] == str(tmp_path / "src")

    def test_bash_gets_sandbox_root_as_cwd(self, tmp_path):
        agent = self._agent_at(tmp_path)
        tc = ToolCallComplete("t1", "Bash", {"command": "ls"})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["cwd"] == str(tmp_path)

    def test_bash_relative_cwd_resolves_to_root(self, tmp_path):
        agent = self._agent_at(tmp_path)
        tc = ToolCallComplete("t1", "Bash", {"command": "ls", "cwd": "sub"})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["cwd"] == str(tmp_path / "sub")

    def test_bash_absolute_cwd_preserved(self, tmp_path):
        agent = self._agent_at(tmp_path)
        absolute = str(tmp_path.parent)
        tc = ToolCallComplete("t1", "Bash", {"command": "ls", "cwd": absolute})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["cwd"] == absolute

    def test_no_checker_is_noop(self):
        agent = Agent.__new__(Agent)
        agent.permission_checker = None
        tc = ToolCallComplete("t1", "WriteFile", {"file_path": "rel.txt"})
        agent._normalize_path_arguments(tc)
        assert tc.arguments["file_path"] == "rel.txt"


# ---------------------------------------------------------------------------
# End-to-end: worktree-rooted agent writes into the worktree, not CWD
# ---------------------------------------------------------------------------


class TestWorktreeRootedAgentWritesToWorktree:
    @pytest.mark.asyncio
    async def test_relative_write_lands_in_worktree(self, tmp_path, monkeypatch):
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
            [
                TextDelta("Done."),
                StreamEnd("end_turn", input_tokens=30, output_tokens=15),
            ],
        ])
        agent = Agent(
            client,
            create_default_registry(),
            "anthropic",
            work_dir=str(repo),
            permission_checker=checker,
        )

        conv = ConversationManager()
        conv.add_user_message("write out.txt")
        results = []
        async for event in agent.run(conv):
            results.append(event)

        tool_results = [e for e in results if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert not tool_results[0].is_error
        assert [e for e in results if isinstance(e, PermissionRequest)] == []

        # The whole point: the file lands inside the worktree root even though
        # the process CWD is elsewhere.
        assert (repo / "out.txt").read_text(encoding="utf-8") == "hi"
        assert not (cwd_dir / "out.txt").exists()

    @pytest.mark.asyncio
    async def test_bash_runs_against_worktree_root(self, tmp_path, monkeypatch):
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
                ToolCallComplete("t1", "Bash", {"command": "pwd"}),
                StreamEnd("end_turn", input_tokens=10, output_tokens=20),
            ],
            [
                TextDelta("Done."),
                StreamEnd("end_turn", input_tokens=30, output_tokens=15),
            ],
        ])
        agent = Agent(
            client,
            create_default_registry(),
            "anthropic",
            work_dir=str(repo),
            permission_checker=checker,
        )

        conv = ConversationManager()
        conv.add_user_message("where am I")
        async for _ in agent.run(conv):
            pass

        last_tool_output = [
            m for m in conv.history if getattr(m, "tool_results", None)
        ][-1]
        stdout = "".join(
            block.content or ""
            for block in last_tool_output.tool_results
        )
        stdout = stdout.removeprefix("STDOUT:\n").strip()
        assert Path(stdout).resolve() == repo.resolve()


# ---------------------------------------------------------------------------
# App host callbacks keep CWD / agent.work_dir / sandbox in sync
# ---------------------------------------------------------------------------


class TestSwitchSessionRoot:
    @pytest.mark.asyncio
    async def test_switch_updates_all_three(self, tmp_path, monkeypatch):
        from codeyx.app import CodeYXApp

        repo = tmp_path / "repo"
        wt = tmp_path / "wt"
        wt.mkdir()

        sandbox = PathSandbox(str(repo))
        agent = SimpleNamespace(
            work_dir=str(repo), permission_checker=SimpleNamespace(sandbox=sandbox)
        )
        app = object.__new__(CodeYXApp)
        app.agent = agent

        monkeypatch.chdir(tmp_path)
        CodeYXApp._switch_session_root(app, str(wt))

        assert Path(os.getcwd()).resolve() == wt.resolve()
        assert agent.work_dir == str(wt)
        assert sandbox.project_root == wt.resolve()

    @pytest.mark.asyncio
    async def test_switch_without_agent_only_chdirs(self, tmp_path, monkeypatch):
        from codeyx.app import CodeYXApp

        wt = tmp_path / "wt"
        wt.mkdir()
        app = object.__new__(CodeYXApp)
        app.agent = None

        monkeypatch.chdir(tmp_path)
        CodeYXApp._switch_session_root(app, str(wt))
        assert Path(os.getcwd()).resolve() == wt.resolve()
