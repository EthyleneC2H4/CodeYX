
from __future__ import annotations

from codeyx.commands.registry import Command, CommandContext, CommandType


async def handle_memory(ctx: CommandContext) -> None:
    mm = ctx.memory_manager
    if mm is None:
        ctx.ui.add_system_message("记忆管理器未初始化")
        return


    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "list":
        display = mm.get_display_text()
        ctx.ui.add_system_message(display)

    elif sub == "clear":
        mm.clear()
        ctx.ui.add_system_message("所有自动记忆已清空。")

    elif sub == "search":
        query = parts[1] if len(parts) > 1 else ""
        if not query:
            ctx.ui.add_system_message("用法: /memory search <query>")
            return
        matches = mm.search(query)
        if not matches:
            ctx.ui.add_system_message(f"没有匹配的记忆：{query}")
            return
        lines = [f"记忆搜索结果：{query}"]
        for match in matches:
            lines.append(
                f"  {match.name:<24} score={match.score:<3} "
                f"[{match.type}] {match.description}\n"
                f"    {match.excerpt}\n"
                f"    {match.path}"
            )
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "catalog":
        entries = mm.catalog()
        if not entries:
            ctx.ui.add_system_message("当前没有目录型记忆。")
            return
        lines = ["目录型记忆："]
        for entry in entries:
            lines.append(
                f"  {entry.name:<24} [{entry.type}] "
                f"{entry.description or '(no description)'}  {entry.path}"
            )
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "edit":
        ctx.ui.add_system_message(
            f"编辑记忆文件：\n"
            f"  用户级: {mm.user_path}\n"
            f"  项目级: {mm.project_path}\n"
            f"  用户级目录: {mm.user_dir}\n"
            f"  项目级目录: {mm.project_dir}"
        )

    else:
        ctx.ui.add_system_message(
            "用法: /memory [list | catalog | search <query> | clear | edit]"
        )


MEMORY_COMMAND = Command(
    name="memory",
    description="记忆管理",
    usage="/memory [list | catalog | search <query> | clear | edit]",
    type=CommandType.LOCAL,
    handler=handle_memory,
)
