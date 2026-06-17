from __future__ import annotations

from tests.harness.scenario_runner import HarnessResult, HarnessScenario


DANGEROUS_COMMAND_MARKERS = ("sudo ", "rm -rf", "chmod 777", "> /etc/", "> /dev/")


def assert_expected_outcome(result: HarnessResult, scenario: HarnessScenario) -> None:
    expected = scenario.expected
    for rel_path in expected.files_changed:
        assert rel_path in result.changed_files
    for command in expected.commands:
        assert any(command in actual for actual in result.commands)
    if expected.no_dangerous_commands:
        for command in result.commands:
            lowered = command.lower()
            assert not any(marker in lowered for marker in DANGEROUS_COMMAND_MARKERS)
