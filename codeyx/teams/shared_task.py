from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class SharedTask:
    id: str
    title: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed | blocked
    assignee: str = ""
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    created_by: str = ""


    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SharedTask:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SharedTaskStore:


    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._next_id = 1
        self._tasks: dict[str, SharedTask] = {}
        self._thread_lock = threading.RLock()
        self._load()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize read-modify-write cycles across threads AND processes.

        Teammates run as separate OS processes sharing one tasks.json;
        without an interprocess lock their load→modify→save cycles silently
        overwrite each other's updates."""
        with self._thread_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.parent / (self._path.name + ".lock")
            with open(lock_path, "w") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _load(self) -> None:
        if not self._path.exists():
            # The store was removed underneath us (external rm). Mirror the
            # disk: keeping the old tasks here would resurrect them into
            # the shared file on this instance's next save.
            self._tasks = {}
            self._next_id = 1
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            # Structural damage (right JSON, wrong shape) must land in the
            # same quarantine as unparseable bytes, or every later operation
            # keeps crashing exactly as before the tolerant-load fix.
            if not isinstance(data, dict):
                raise TypeError("task store root is not an object")
            self._next_id = int(data.get("next_id", 1))
            tasks = {}
            for t in data.get("tasks", []):
                task = SharedTask.from_dict(t)
                tasks[task.id] = task
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError, KeyError):
            # _save writes atomically, so a torn file is impossible; reaching
            # here means external damage. Keep the bytes for inspection
            # instead of crashing every later operation.
            backup = self._path.parent / (self._path.name + ".corrupt")
            try:
                os.replace(self._path, backup)
                log.warning("SharedTaskStore file damaged; preserved at %s", backup)
            except OSError:
                log.warning("SharedTaskStore file damaged and could not be backed up")
            self._next_id = 1
            self._tasks = {}
            return
        self._tasks = tasks

    def _save(self) -> None:
        data = {
            "next_id": self._next_id,
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }
        tmp = self._path.parent / (
            f"{self._path.name}.{os.getpid()}.tmp"
        )
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def create(
        self,
        title: str,
        description: str = "",
        assignee: str = "",
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        created_by: str = "",
    ) -> SharedTask:
        with self._locked():
            self._load()
            task_id = str(self._next_id)
            self._next_id += 1
            task = SharedTask(
                id=task_id,
                title=title,
                description=description,
                assignee=assignee,
                blocks=blocks or [],
                blocked_by=blocked_by or [],
                created_by=created_by,
            )
            self._tasks[task_id] = task
            self._save()
            return task

    def get(self, task_id: str) -> SharedTask | None:
        with self._locked():
            self._load()
            return self._tasks.get(task_id)


    def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[SharedTask]:
        with self._locked():
            self._load()
            result = list(self._tasks.values())
            if status:
                result = [t for t in result if t.status == status]
            if assignee:
                result = [t for t in result if t.assignee == assignee]
            return result


    def update(
        self,
        task_id: str,
        status: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
    ) -> SharedTask | None:
        with self._locked():
            self._load()
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if status is not None:
                task.status = status
            if assignee is not None:
                task.assignee = assignee
            if description is not None:
                task.description = description
            if add_blocks:
                for bid in add_blocks:
                    if bid not in task.blocks:
                        task.blocks.append(bid)
            if add_blocked_by:
                for bid in add_blocked_by:
                    if bid not in task.blocked_by:
                        task.blocked_by.append(bid)
            self._save()
            return task

    def init_empty(self) -> None:
        with self._locked():
            self._tasks.clear()
            self._next_id = 1
            self._save()
