from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from codeyx.tools import ToolRegistry
from codeyx.tools.base import ToolCallComplete, ToolResult


@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


@dataclass
class ToolExecutionResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float
    is_unknown: bool


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = (
            tool is not None
            and tool.is_concurrency_safe
            and registry.is_enabled(tc.tool_name)
        )

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


class ToolExecutionScheduler:
    """Partitions and executes tool calls while preserving model call order."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def partition(self, calls: list[ToolCallComplete]) -> list[ToolBatch]:
        return partition_tool_calls(calls, self.registry)

    async def run_parallel(
        self,
        calls: list[ToolCallComplete],
        executor: Callable[[ToolCallComplete], Awaitable[ToolExecutionResult]],
    ) -> list[ToolExecutionResult]:
        if not calls:
            return []
        results = await asyncio.gather(
            *(executor(tc) for tc in calls),
            return_exceptions=True,
        )
        out: list[ToolExecutionResult] = []
        for tc, result in zip(calls, results, strict=False):
            if isinstance(result, Exception):
                out.append(
                    ToolExecutionResult(
                        tool_id=tc.tool_id,
                        tool_name=tc.tool_name,
                        result=ToolResult(
                            output=f"Tool execution error: {result}",
                            is_error=True,
                        ),
                        elapsed=0.0,
                        is_unknown=False,
                    )
                )
            else:
                out.append(result)
        return out
