from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    import os
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@dataclass
class Changes:
    uncommitted: int = 0
    new_commits: int = 0


@dataclass
class MergePreview:
    worker_changed_files: list[str]
    target_changed_files: list[str]
    conflict_files: list[str]
    diff_stat: str
    has_uncommitted_changes: bool

    @property
    def can_merge_cleanly(self) -> bool:
        return not self.has_uncommitted_changes and not self.conflict_files


def count_worktree_changes(wt_path: str, head_commit: str) -> Changes:
    changes = Changes()
    try:
        status = _run_git(["status", "--porcelain"], cwd=wt_path)
        if status.returncode == 0:
            changes.uncommitted = len(
                [line for line in status.stdout.splitlines() if line.strip()]
            )
    except (subprocess.SubprocessError, OSError):
        changes.uncommitted = 1

    try:
        rev_list = _run_git(
            ["rev-list", "--count", f"{head_commit}..HEAD"], cwd=wt_path
        )
        if rev_list.returncode == 0:
            changes.new_commits = int(rev_list.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        changes.new_commits = 1

    return changes


def has_worktree_changes(wt_path: str, head_commit: str) -> bool:
    c = count_worktree_changes(wt_path, head_commit)
    return c.uncommitted > 0 or c.new_commits > 0


@dataclass
class CleanupResult:
    kept: bool
    path: str = ""
    branch: str = ""


def has_unpushed_commits(wt_path: str) -> bool:
    try:
        result = _run_git(
            ["rev-list", "--max-count=1", "HEAD", "--not", "--remotes"],
            cwd=wt_path,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else True
    except (subprocess.SubprocessError, OSError):
        return True


def _changed_files(cwd: str, revision_range: str) -> list[str]:
    result = _run_git(["diff", "--name-only", revision_range], cwd=cwd)
    if result.returncode != 0:
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def build_merge_preview(
    repo_root: str,
    wt_path: str,
    base_commit: str,
    target_ref: str = "HEAD",
) -> MergePreview:
    """Summarize worker changes and likely file-level conflicts before merge."""
    worker_changed = _changed_files(wt_path, f"{base_commit}..HEAD")
    target_changed = _changed_files(repo_root, f"{base_commit}..{target_ref}")
    conflicts = sorted(set(worker_changed) & set(target_changed))

    stat = _run_git(["diff", "--stat", f"{base_commit}..HEAD"], cwd=wt_path)
    status = _run_git(["status", "--porcelain"], cwd=wt_path)
    has_uncommitted = (
        status.returncode != 0
        or any(line.strip() for line in status.stdout.splitlines())
    )

    return MergePreview(
        worker_changed_files=worker_changed,
        target_changed_files=target_changed,
        conflict_files=conflicts,
        diff_stat=stat.stdout.strip() if stat.returncode == 0 else "",
        has_uncommitted_changes=has_uncommitted,
    )
