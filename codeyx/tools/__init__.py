from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from codeyx.tools.base import Tool

if TYPE_CHECKING:
    from codeyx.cache import FileCache


class ToolRegistry:
    def __init__(
        self,
        tool_search_mode: str = "always",
        tool_search_threshold_percent: int = 10,
        context_window: int = 200_000,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
        self.tool_search_mode = tool_search_mode
        self.tool_search_threshold_percent = tool_search_threshold_percent
        self.context_window = context_window

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        self._disabled.discard(name)


    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()


    def mark_discovered(self, name: str) -> None:
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered


    def get_deferred_tool_names(self) -> list[str]:
        return [
            name
            for name, tool in self._tools.items()
            if self._should_defer_tool(tool)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def _should_defer_tool(self, tool: Tool) -> bool:
        if not getattr(tool, "should_defer", False):
            return False
        if self.tool_search_mode == "disabled":
            return False
        if self.tool_search_mode == "always":
            return True
        if self.tool_search_mode == "auto":
            return self.should_use_tool_search_auto()
        return True

    def estimate_deferred_schema_chars(self) -> int:
        total = 0
        for name, tool in self._tools.items():
            if name in self._disabled or not getattr(tool, "should_defer", False):
                continue
            total += len(json.dumps(tool.get_schema(), ensure_ascii=False))
        return total

    def should_use_tool_search_auto(self) -> bool:
        if self.tool_search_mode == "always":
            return True
        if self.tool_search_mode == "disabled":
            return False
        # Rough approximation used only for local routing. The API-facing token
        # accounting still happens in the LLM provider.
        estimated_tokens = max(1, self.estimate_deferred_schema_chars() // 4)
        threshold = max(
            1,
            int(self.context_window * (self.tool_search_threshold_percent / 100)),
        )
        return estimated_tokens >= threshold

    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not self._should_defer_tool(tool):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            metadata = tool.get_metadata()
            tags_lower = " ".join(metadata.tags).lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            if query_lower in tags_lower:
                score += 4
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
                if word in tags_lower:
                    score += 2
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not self._should_defer_tool(tool):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())


    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if self._should_defer_tool(tool) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                schemas.append(base)
        return schemas


def create_default_registry(file_cache: FileCache | None = None) -> ToolRegistry:
    from codeyx.tools.bash import Bash
    from codeyx.tools.edit_file import EditFile
    from codeyx.tools.glob import Glob
    from codeyx.tools.grep import Grep
    from codeyx.tools.read_file import ReadFile
    from codeyx.tools.write_file import WriteFile

    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache))
    registry.register(WriteFile(file_cache=file_cache))
    registry.register(EditFile(file_cache=file_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
