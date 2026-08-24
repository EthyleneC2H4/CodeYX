from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from codeyx.hooks.executors import execute_action
from codeyx.hooks.models import ActionResult, Hook, HookContext, ToolRejectedError

log = logging.getLogger(__name__)


@dataclass
class HookNotification:
    hook_id: str
    event: str
    output: str
    success: bool


class HookEngine:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks: list[Hook] = hooks or []
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []
        self._background_tasks: set[asyncio.Task[None]] = set()


    def find_matching_hooks(self, event: str, ctx: HookContext) -> list[Hook]:
        matched: list[Hook] = []
        for hook in self.hooks:
            if hook.event != event:
                continue
            if not hook.should_run():
                continue
            if hook.condition is not None and not hook.condition.evaluate(ctx):
                continue
            matched.append(hook)
        return matched


    async def run_hooks(self, event: str, ctx: HookContext) -> None:
        matched = self.find_matching_hooks(event, ctx)
        for hook in matched:
            hook.mark_executed()
            if hook.async_exec:
                task = asyncio.create_task(self._run_single(hook, ctx))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            else:
                await self._run_single(hook, ctx)


    async def _execute_hook(self, hook: Hook, ctx: HookContext) -> ActionResult:
        """Run one hook action under its configured timeout. Never raises —
        failures (including timeouts) come back as unsuccessful results so a
        hung hook cannot stall the agent loop."""
        try:
            return await asyncio.wait_for(
                execute_action(hook.action, ctx), timeout=hook.action.timeout
            )
        except TimeoutError:
            return ActionResult(
                output=f"Hook '{hook.id}' timed out after {hook.action.timeout}s",
                success=False,
            )
        except Exception as e:
            log.warning("Hook '%s' execution error: %s", hook.id, e)
            return ActionResult(output=str(e), success=False)

    async def _run_single(self, hook: Hook, ctx: HookContext) -> None:
        result = await self._execute_hook(hook, ctx)
        if hook.action.type == "prompt" and result.success:
            self._prompt_messages.append(result.output)
        self._notifications.append(
            HookNotification(
                hook_id=hook.id,
                event=hook.event,
                output=result.output,
                success=result.success,
            )
        )
        if not result.success:
            log.warning("Hook '%s' action failed: %s", hook.id, result.output)


    async def run_pre_tool_hooks(
        self, ctx: HookContext
    ) -> ToolRejectedError | None:
        matched = self.find_matching_hooks("pre_tool_use", ctx)
        for hook in matched:
            hook.mark_executed()
            result = await self._execute_hook(hook, ctx)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event="pre_tool_use",
                    output=result.output,
                    success=result.success,
                )
            )
            if hook.reject:
                return ToolRejectedError(
                    tool=ctx.tool_name,
                    reason=result.output,
                    hook_id=hook.id,
                )
        return None

    def get_prompt_messages(self) -> list[str]:
        messages = list(self._prompt_messages)
        self._prompt_messages.clear()
        return messages


    def drain_notifications(self) -> list[HookNotification]:
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications


    async def cancel_background_hooks(self) -> None:
        tasks = list(self._background_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
