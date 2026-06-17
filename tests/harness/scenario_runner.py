from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from codeyx.agent import Agent, AgentEvent, ToolUseEvent
from codeyx.conversation import ConversationManager
from codeyx.tools import create_default_registry
from tests.harness.mock_llm import ScriptedLLMClient


@dataclass
class ExpectedOutcome:
    files_changed: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    no_dangerous_commands: bool = True


@dataclass
class HarnessScenario:
    task: str
    files: dict[str, str] = field(default_factory=dict)
    llm_script: list[list[dict[str, Any]]] = field(default_factory=list)
    expected: ExpectedOutcome = field(default_factory=ExpectedOutcome)


@dataclass
class HarnessResult:
    events: list[AgentEvent]
    tool_calls: list[ToolUseEvent]
    changed_files: list[str]
    commands: list[str]


class ScenarioRunner:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir

    async def run(self, scenario: HarnessScenario) -> HarnessResult:
        before: dict[str, str] = {}
        for rel_path, content in scenario.files.items():
            path = self.work_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            before[rel_path] = content

        client = ScriptedLLMClient(scenario.llm_script)
        agent = Agent(
            client=client,
            registry=create_default_registry(),
            protocol="anthropic",
            work_dir=str(self.work_dir),
            max_iterations=max(1, len(scenario.llm_script) + 1),
        )
        conv = ConversationManager()
        conv.add_user_message(scenario.task)

        events: list[AgentEvent] = []
        old_cwd = os.getcwd()
        try:
            os.chdir(self.work_dir)
            async for event in agent.run(conv):
                events.append(event)
        finally:
            os.chdir(old_cwd)

        tool_calls = [e for e in events if isinstance(e, ToolUseEvent)]
        commands = [
            str(call.arguments.get("command", ""))
            for call in tool_calls
            if call.tool_name == "Bash"
        ]
        changed_files: list[str] = []
        candidates = set(before) | set(scenario.expected.files_changed)
        for rel in sorted(candidates):
            path = self.work_dir / rel
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != before.get(rel, ""):
                changed_files.append(rel)
        changed_files.sort()

        return HarnessResult(
            events=events,
            tool_calls=tool_calls,
            changed_files=changed_files,
            commands=commands,
        )
