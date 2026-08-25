
from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from codeyx.tools.base import Tool, ToolResult
from codeyx.worktree.slug import validate_slug

if TYPE_CHECKING:
    from codeyx.worktree.manager import WorktreeManager, WorktreeSession


class EnterWorktreeParams(BaseModel):
    name: str | None = Field(
        default=None,
        description=(
            'Optional name for the worktree. Each "/"-separated segment may '
            "contain only letters, digits, dots, underscores, and dashes; "
            "max 64 chars total. A random name is generated if not provided."
        ),
    )


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = (
        "Creates an isolated worktree (via git) and switches the session into it"
    )
    params_model = EnterWorktreeParams
    category = "command"
    should_defer = True


    def __init__(
        self,
        worktree_manager: WorktreeManager,
        on_enter: Callable[[WorktreeSession], Awaitable[None]] | None = None,
    ) -> None:
        self._manager = worktree_manager
        # Host callback that actually re-roots the live session (chdir,
        # agent.work_dir, sandbox root). Without it the tool only records
        # bookkeeping and later edits land in the original tree.
        self._on_enter = on_enter


    async def execute(self, params: EnterWorktreeParams) -> ToolResult:
        if self._manager.get_current_session() is not None:
            return ToolResult(
                output="Already in a worktree session", is_error=True
            )

        slug = params.name or f"wt-{secrets.token_hex(4)}"

        err = validate_slug(slug)
        if err:
            return ToolResult(output=f"Invalid worktree name: {err}", is_error=True)

        try:
            wt = await self._manager.create(slug)
            session = await self._manager.enter(slug)
        except Exception as e:
            return ToolResult(
                output=f"Error creating worktree: {e}", is_error=True
            )

        branch_info = f" on branch {wt.branch}" if wt.branch else ""

        switch_error = ""
        if self._on_enter is not None:
            try:
                await self._on_enter(session)
            except Exception as e:
                switch_error = (
                    f" WARNING: the session could not be switched into the "
                    f"worktree ({e}); subsequent relative paths still resolve "
                    f"to the original directory."
                )

        return ToolResult(
            output=(
                f"Created worktree at {session.worktree_path}{branch_info}. "
                "The session is now working in the worktree. "
                "Use ExitWorktree to leave mid-session, or exit the session to be prompted."
                f"{switch_error}"
            ),
            is_error=bool(switch_error),
        )
