
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from codeyx.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from codeyx.cache import FileCache


class Params(BaseModel):
    file_path: str = Field(description="Absolute or relative path to the file to read")
    offset: int = Field(default=0, description="Line offset to start reading from (0-based)")
    limit: int = Field(default=2000, description="Maximum number of lines to read")


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a file and return its contents with line numbers."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    def __init__(self, file_cache: FileCache | None = None) -> None:
        self._cache = file_cache


    async def execute(self, params: Params) -> ToolResult:
        path = Path(params.file_path)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(output=f"Error: not a file: {params.file_path}", is_error=True)

        resolved = str(path.resolve())

        try:
            # get_fresh stat-checks so Bash-side edits (sed -i, git checkout,
            # …) that never call invalidate() still show up on the next read.
            text = self._cache.get_fresh(resolved) if self._cache else None
            if text is None:
                # Stat BEFORE reading: pairing the content with a post-read
                # stat would let a concurrent external write poison the
                # freshness check (old content, new mtime → fresh forever).
                st = path.stat()
                text = path.read_text(encoding="utf-8")
                if self._cache:
                    self._cache.put_with_meta(
                        resolved, text, st.st_mtime_ns, st.st_size
                    )
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = text.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        numbered = [f"{i + params.offset + 1}\t{line}" for i, line in enumerate(selected)]
        return ToolResult(output="\n".join(numbered))
