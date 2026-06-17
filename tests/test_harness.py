from __future__ import annotations

import pytest

from tests.harness.assertions import assert_expected_outcome
from tests.harness.scenario_runner import ExpectedOutcome, HarnessScenario, ScenarioRunner


@pytest.mark.asyncio
async def test_declarative_scenario_runner_tracks_tools_and_files(tmp_path):
    scenario = HarnessScenario(
        task="Fix calculator.py and run tests",
        files={
            "calculator.py": "def add(a, b):\n    return a - b\n",
            "test_calculator.py": "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
        llm_script=[
            [
                {"type": "tool", "id": "t1", "name": "ReadFile", "args": {"file_path": "calculator.py"}},
                {"type": "end", "stop_reason": "tool_use"},
            ],
            [
                {
                    "type": "tool",
                    "id": "t2",
                    "name": "EditFile",
                    "args": {
                        "file_path": "calculator.py",
                        "old_string": "return a - b",
                        "new_string": "return a + b",
                    },
                },
                {"type": "tool", "id": "t3", "name": "Bash", "args": {"command": "pytest -q"}},
                {"type": "end", "stop_reason": "tool_use"},
            ],
            [
                {"type": "text", "text": "Fixed."},
                {"type": "end", "stop_reason": "end_turn"},
            ],
        ],
        expected=ExpectedOutcome(
            files_changed=["calculator.py"],
            commands=["pytest"],
            no_dangerous_commands=True,
        ),
    )

    result = await ScenarioRunner(tmp_path).run(scenario)

    assert [call.tool_name for call in result.tool_calls] == ["ReadFile", "EditFile", "Bash"]
    assert "calculator.py" in result.changed_files
    assert "pytest -q" in result.commands
    assert_expected_outcome(result, scenario)
