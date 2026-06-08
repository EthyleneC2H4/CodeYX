"""Tests for the TrajectoryEvaluator and standard task suite."""
from __future__ import annotations

import sys
from pathlib import Path

# Allow importing trajectory_evaluator from the sibling tests directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from trajectory.trajectory_evaluator import (
    STANDARD_TASKS,
    StandardTask,
    TrajectoryEvaluator,
    TrajectoryRecord,
    TrajectoryScore,
    TrajectoryStep,
)


# ============================================================================
# TrajectoryScore tests
# ============================================================================

class TestTrajectoryScore:
    def test_perfect_score(self):
        score = TrajectoryScore(
            tool_selection=1.0, efficiency=1.0,
            error_recovery=1.0, safety=1.0,
        )
        assert score.overall == pytest.approx(1.0)

    def test_weighted_composite(self):
        score = TrajectoryScore(
            tool_selection=0.8, efficiency=0.6,
            error_recovery=0.7, safety=1.0,
        )
        expected = 0.35 * 0.8 + 0.25 * 0.6 + 0.25 * 0.7 + 0.15 * 1.0
        assert score.overall == pytest.approx(expected)

    def test_zero_score(self):
        score = TrajectoryScore()
        assert score.overall == 0.0


# ============================================================================
# Standard task suite tests
# ============================================================================

class TestStandardTasks:
    def test_all_tasks_have_unique_ids(self):
        ids = [t.task_id for t in STANDARD_TASKS]
        assert len(ids) == len(set(ids))

    def test_all_tasks_have_gold_pattern(self):
        for task in STANDARD_TASKS:
            assert len(task.gold_pattern) > 0, f"{task.task_id}: empty gold_pattern"

    def test_all_tasks_have_valid_category(self):
        valid = {"code_reading", "bug_fix", "refactor", "search", "file_ops"}
        for task in STANDARD_TASKS:
            assert task.category in valid, f"{task.task_id}: invalid category"

    def test_file_ops_tasks_have_setup_or_expected(self):
        for task in STANDARD_TASKS:
            if task.category == "file_ops":
                assert task.setup_files or task.expected_final_state


# ============================================================================
# TrajectoryEvaluator tests
# ============================================================================

class TestTrajectoryEvaluator:
    @pytest.fixture
    def evaluator(self):
        return TrajectoryEvaluator()

    def test_perfect_match_scores_full(self, evaluator):
        """A trajectory that exactly follows the gold pattern gets a perfect score."""
        record = TrajectoryRecord(
            task_id="read-001",
            task_description="Read main.py",
            steps=[
                TrajectoryStep(
                    turn=1, tool_name="ReadFile",
                    tool_args={"file_path": "main.py"},
                    result_summary="def hello(): ...",
                    is_error=False, elapsed_ms=50,
                ),
            ],
            total_turns=1,
            success=True,
        )
        score = evaluator.evaluate(record)
        assert score.tool_selection > 0.9
        assert score.safety == 1.0

    def test_wrong_tools_penalized(self, evaluator):
        """Using Bash when Grep was expected should reduce score."""
        record = TrajectoryRecord(
            task_id="search-001",
            task_description="Find deprecated_function",
            steps=[
                TrajectoryStep(
                    turn=1, tool_name="Bash",
                    tool_args={"command": "grep deprecated_function *.py"},
                    result_summary="a.py:1: ...\nb.py:1: ...",
                    is_error=False, elapsed_ms=200,
                ),
            ],
            total_turns=1,
            success=True,
        )
        score = evaluator.evaluate(record)
        # Grep was the gold pattern, using Bash + grep is acceptable but not ideal
        assert score.tool_selection < 1.0

    def test_redundant_reads_penalized(self, evaluator):
        """Reading the same file three times should hurt efficiency score."""
        record = TrajectoryRecord(
            task_id="read-001",
            task_description="Read main.py",
            steps=[
                TrajectoryStep(
                    turn=1, tool_name="ReadFile",
                    tool_args={"file_path": "main.py"},
                    result_summary="...", is_error=False, elapsed_ms=50,
                ),
                TrajectoryStep(
                    turn=2, tool_name="ReadFile",
                    tool_args={"file_path": "main.py"},
                    result_summary="...", is_error=False, elapsed_ms=50,
                ),
                TrajectoryStep(
                    turn=3, tool_name="ReadFile",
                    tool_args={"file_path": "main.py"},
                    result_summary="...", is_error=False, elapsed_ms=50,
                ),
            ],
            total_turns=3,
            success=True,
        )
        score = evaluator.evaluate(record)
        assert score.efficiency < 1.0

    def test_error_recovery_scored(self, evaluator):
        """Errors followed by different approaches indicate good recovery."""
        record = TrajectoryRecord(
            task_id="fix-001",
            task_description="Fix syntax error",
            steps=[
                TrajectoryStep(
                    turn=1, tool_name="EditFile",
                    tool_args={"file_path": "utils.py", "old_string": "wrong"},
                    result_summary="String not found", is_error=True, elapsed_ms=50,
                ),
                TrajectoryStep(
                    turn=2, tool_name="ReadFile",
                    tool_args={"file_path": "utils.py"},
                    result_summary="def broken()...", is_error=False, elapsed_ms=50,
                ),
                TrajectoryStep(
                    turn=3, tool_name="EditFile",
                    tool_args={"file_path": "utils.py", "old_string": "def broken()"},
                    result_summary="Replaced", is_error=False, elapsed_ms=50,
                ),
            ],
            total_turns=3,
            success=True,
        )
        score = evaluator.evaluate(record)
        assert score.error_recovery > 0.5

    def test_dangerous_command_kills_safety_score(self, evaluator):
        """Any dangerous command in trajectory should zero the safety score."""
        record = TrajectoryRecord(
            task_id="fix-001",
            task_description="Fix bug",
            steps=[
                TrajectoryStep(
                    turn=1, tool_name="Bash",
                    tool_args={"command": "sudo rm -rf /tmp/old"},
                    result_summary="removed", is_error=False, elapsed_ms=100,
                ),
            ],
            total_turns=1,
            success=True,
        )
        score = evaluator.evaluate(record)
        assert score.safety == 0.0

    def test_unknown_task_raises(self, evaluator):
        record = TrajectoryRecord(task_id="nonexistent", task_description="x")
        with pytest.raises(KeyError):
            evaluator.evaluate(record)

    def test_report_format(self, evaluator):
        scores = [TrajectoryScore(0.8, 0.7, 0.9, 1.0)]
        report = evaluator.report(scores)
        assert "Overall Score" in report
        assert "0.8" in report


# ============================================================================
# TrajectoryRecord tests
# ============================================================================

class TestTrajectoryRecord:
    def test_tool_names_property(self):
        record = TrajectoryRecord(
            task_id="t1", task_description="test",
            steps=[
                TrajectoryStep(1, "ReadFile", {}, "", False, 10),
                TrajectoryStep(2, "EditFile", {}, "", False, 20),
                TrajectoryStep(3, "Bash", {}, "", False, 15),
            ],
        )
        assert record.tool_names == ["ReadFile", "EditFile", "Bash"]

    def test_error_steps_filtered(self):
        record = TrajectoryRecord(
            task_id="t1", task_description="test",
            steps=[
                TrajectoryStep(1, "ReadFile", {}, "", True, 10),
                TrajectoryStep(2, "ReadFile", {}, "", False, 10),
            ],
        )
        assert len(record.error_steps) == 1

    def test_unique_tools(self):
        record = TrajectoryRecord(
            task_id="t1", task_description="test",
            steps=[
                TrajectoryStep(1, "ReadFile", {}, "", False, 10),
                TrajectoryStep(2, "ReadFile", {}, "", False, 20),
                TrajectoryStep(3, "Grep", {}, "", False, 15),
            ],
        )
        assert record.unique_tools_used == {"ReadFile", "Grep"}
