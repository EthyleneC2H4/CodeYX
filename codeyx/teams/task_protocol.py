from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class WorkerState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MERGED = "merged"


@dataclass
class TaskSpec:
    task_id: str
    objective: str
    constraints: list[str] = field(default_factory=list)
    files_scope: list[str] = field(default_factory=list)
    expected_output: str = ""
    verification: list[str] = field(default_factory=list)
    assigned_agent: str = ""
    deadline_turns: int | None = None

    def to_worker_prompt(self) -> str:
        lines = [
            f"Task ID: {self.task_id}",
            "",
            "Objective:",
            self.objective,
        ]
        if self.constraints:
            lines.extend(["", "Constraints:", *[f"- {c}" for c in self.constraints]])
        if self.files_scope:
            lines.extend(["", "Files scope:", *[f"- {p}" for p in self.files_scope]])
        if self.expected_output:
            lines.extend(["", "Expected output:", self.expected_output])
        if self.verification:
            lines.extend(["", "Verification:", *[f"- {v}" for v in self.verification]])
        if self.deadline_turns is not None:
            lines.extend(["", f"Deadline turns: {self.deadline_turns}"])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskSpec:
        return cls(
            task_id=str(data["task_id"]),
            objective=str(data["objective"]),
            constraints=list(data.get("constraints", [])),
            files_scope=list(data.get("files_scope", [])),
            expected_output=str(data.get("expected_output", "")),
            verification=list(data.get("verification", [])),
            assigned_agent=str(data.get("assigned_agent", "")),
            deadline_turns=data.get("deadline_turns"),
        )


@dataclass
class WorkerStatus:
    task_id: str
    worker_id: str
    state: WorkerState = WorkerState.PENDING
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    last_message: str = ""
    current_file_scope: list[str] = field(default_factory=list)
    result_summary: str = ""
    error: str = ""

    def transition(
        self,
        state: WorkerState,
        *,
        message: str = "",
        result_summary: str = "",
        error: str = "",
        file_scope: list[str] | None = None,
        now: float | None = None,
    ) -> None:
        ts = time.time() if now is None else now
        if self.started_at is None and state == WorkerState.RUNNING:
            self.started_at = ts
        self.state = state
        self.updated_at = ts
        if message:
            self.last_message = message
        if result_summary:
            self.result_summary = result_summary
        if error:
            self.error = error
        if file_scope is not None:
            self.current_file_scope = list(file_scope)

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            WorkerState.COMPLETED,
            WorkerState.FAILED,
            WorkerState.CANCELLED,
            WorkerState.MERGED,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerStatus:
        return cls(
            task_id=str(data["task_id"]),
            worker_id=str(data["worker_id"]),
            state=WorkerState(data.get("state", WorkerState.PENDING)),
            started_at=data.get("started_at"),
            updated_at=float(data.get("updated_at", time.time())),
            last_message=str(data.get("last_message", "")),
            current_file_scope=list(data.get("current_file_scope", [])),
            result_summary=str(data.get("result_summary", "")),
            error=str(data.get("error", "")),
        )
