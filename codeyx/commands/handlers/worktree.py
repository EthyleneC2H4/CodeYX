from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from codeyx.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from codeyx.worktree.manager import WorktreeManager, WorktreeSession

# Host re-rooting callbacks (chdir + agent.work_dir + sandbox rebase), shared
# with the EnterWorktree/ExitWorktree tools so the slash-command path cannot
# drift from the tool path again — it did once already.
RootApplier = Callable[["WorktreeSession"], Awaitable[None]]


def create_worktree_command(
    manager: WorktreeManager,
    apply_root: RootApplier | None = None,
    restore_root: RootApplier | None = None,
) -> Command:


    async def handle_worktree(ctx: CommandContext) -> None:
        args = ctx.args.strip()
        if not args:
            ctx.ui.add_system_message(
                "用法:\n"
                "  /worktree create <name> [base-branch]\n"
                "  /worktree list\n"
                "  /worktree enter <name>\n"
                "  /worktree exit [--remove] [--discard]\n"
                "  /worktree status"
            )
            return

        parts = args.split()
        sub = parts[0]
        rest = parts[1:]

        if sub == "create":
            await _handle_create(ctx, manager, rest, apply_root)
        elif sub == "list":
            _handle_list(ctx, manager)
        elif sub == "enter":
            await _handle_enter(ctx, manager, rest, apply_root)
        elif sub == "exit":
            await _handle_exit(ctx, manager, rest, restore_root)
        elif sub == "status":
            _handle_status(ctx, manager)
        else:
            ctx.ui.add_system_message(f"未知子命令: {sub}")

    return Command(
        name="worktree",
        aliases=["wt"],
        description="管理 Git Worktree",
        usage="/worktree <create|list|enter|exit|status>",
        type=CommandType.LOCAL,
        handler=handle_worktree,
    )


async def _handle_create(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
    apply_root: RootApplier | None,
) -> None:
    if not args:
        ctx.ui.add_system_message("用法: /worktree create <name> [base-branch]")
        return

    # Same guard as _handle_enter / EnterWorktreeTool: one live session per
    # process. enter() overwrites current_session unconditionally, so an
    # unguarded create would strand the old worktree with no session record
    # and no way back to it.
    if manager.get_current_session() is not None:
        ctx.ui.add_system_message(
            "已处于 worktree 会话中；请先 /worktree exit 再创建新的 worktree"
        )
        return

    name = args[0]
    base_branch = args[1] if len(args) > 1 else "HEAD"

    try:
        wt = await manager.create(name, base_branch)
    except Exception as e:
        ctx.ui.add_system_message(f"创建 worktree 失败: {e}")
        return

    try:
        session = await manager.enter(name)
        await _apply_root_or_work_dir(ctx, session, apply_root)
    except Exception as e:
        ctx.ui.add_system_message(
            f"Worktree 已创建但进入失败: {e}\n路径: {wt.path}"
        )
        return

    ctx.ui.add_system_message(
        f"已创建并进入 worktree: {name}\n"
        f"路径: {wt.path}\n"
        f"分支: {wt.branch}\n"
        f"基于: {base_branch}"
    )


async def _apply_root_or_work_dir(
    ctx: CommandContext,
    session: WorktreeSession,
    apply_root: RootApplier | None,
) -> None:
    """Prefer the host re-rooting callback (chdir + sandbox rebase); fall
    back to the historical work_dir-only behavior in embedded/headless use."""
    if apply_root is not None:
        await apply_root(session)
    elif ctx.agent:
        ctx.agent.work_dir = session.worktree_path


def _handle_list(ctx: CommandContext, manager: WorktreeManager) -> None:
    worktrees = manager.list_worktrees()
    if not worktrees:
        ctx.ui.add_system_message("当前没有活跃的 worktree")
        return

    current = manager.current_session
    lines = ["活跃的 Worktrees:", "─────────────────"]
    for wt in worktrees:
        marker = " ← 当前" if current and current.worktree_name == wt.name else ""
        lines.append(
            f"  {wt.name}{marker}\n"
            f"    路径: {wt.path}\n"
            f"    分支: {wt.branch}\n"
            f"    创建: {wt.created.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    ctx.ui.add_system_message("\n".join(lines))


async def _handle_enter(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
    apply_root: RootApplier | None,
) -> None:
    if not args:
        ctx.ui.add_system_message("用法: /worktree enter <name>")
        return
    # Same guard as EnterWorktreeTool: one live session per process.
    if manager.get_current_session() is not None:
        ctx.ui.add_system_message(
            "已处于 worktree 会话中；请先 /worktree exit 再进入其他 worktree"
        )
        return

    name = args[0]
    try:
        session = await manager.enter(name)
        await _apply_root_or_work_dir(ctx, session, apply_root)
        ctx.ui.add_system_message(f"已进入 worktree: {name}\n路径: {session.worktree_path}")
    except Exception as e:
        ctx.ui.add_system_message(f"进入 worktree 失败: {e}")


async def _handle_exit(
    ctx: CommandContext,
    manager: WorktreeManager,
    args: list[str],
    restore_root: RootApplier | None,
) -> None:
    session = manager.get_current_session()
    if session is None:
        ctx.ui.add_system_message("当前不在任何 worktree 中")
        return

    remove = "--remove" in args
    discard = "--discard" in args
    action = "remove" if remove else "keep"

    if remove and not discard:
        # Same dirty-tree pre-check as ExitWorktreeTool, mirrored for the
        # same reason: this handler re-roots the host BEFORE exit. If exit
        # then refused on a dirty tree, the session would stay active while
        # every tool path already resolves against the main repo — so the
        # refusal must happen before any state changes.
        from codeyx.worktree.changes import count_worktree_changes

        try:
            changes = await asyncio.to_thread(
                count_worktree_changes,
                session.worktree_path,
                session.original_head_commit,
            )
        except Exception:
            changes = None  # cannot count here — let manager.exit decide below
        if changes is not None and (
            changes.uncommitted > 0 or changes.new_commits > 0
        ):
            ctx.ui.add_system_message(
                f"worktree 有未保存的工作（未提交文件 {changes.uncommitted} 个、"
                f"新提交 {changes.new_commits} 个）。删除将永久丢弃这些内容；"
                "确认后请加 --discard 重试，或去掉 --remove 以保留 worktree。"
            )
            return

    # Restore the host root BEFORE removal (same order as ExitWorktreeTool):
    # a failed switch-back must never be followed by deleting the only
    # copy of the worktree.
    if restore_root is not None:
        try:
            await restore_root(session)
        except Exception as e:
            if remove:
                ctx.ui.add_system_message(
                    f"无法删除 worktree：会话切回 {session.original_cwd} 失败（{e}）。"
                    "worktree 已保留，请排查后重试。"
                )
                return
            ctx.ui.add_system_message(f"警告：切回原目录失败：{e}")
    elif ctx.agent:
        ctx.agent.work_dir = session.original_cwd

    try:
        await manager.exit(session.worktree_name, action=action, discard_changes=discard)
        msg = f"已退出 worktree: {session.worktree_name}"
        if remove:
            msg += "（已删除）"
        ctx.ui.add_system_message(msg)
    except Exception as e:
        ctx.ui.add_system_message(f"退出 worktree 失败: {e}")


def _handle_status(ctx: CommandContext, manager: WorktreeManager) -> None:
    session = manager.get_current_session()
    if session is None:
        ctx.ui.add_system_message("当前不在任何 worktree 中")
        return

    lines = [
        "Worktree 会话状态:",
        "──────────────────",
        f"  名称: {session.worktree_name}",
        f"  路径: {session.worktree_path}",
        f"  原始目录: {session.original_cwd}",
        f"  原始分支: {session.original_branch}",
    ]
    ctx.ui.add_system_message("\n".join(lines))
