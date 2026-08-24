from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from codeyx.tools.base import Tool, ToolResult


class QuestionItem(BaseModel):
    type: str = Field(description="Question type: text, radio, select, checkbox")
    name: str = Field(description="Question identifier")
    message: str = Field(description="Question text to display")
    options: list[str] = Field(
        default_factory=list,
        description="Options for radio/select/checkbox types",
    )


class AskUserParams(BaseModel):
    questions: list[QuestionItem] = Field(
        description="List of questions to ask the user"
    )


class AskUserEvent:


    def __init__(
        self,
        questions: list[dict[str, Any]],
        future: asyncio.Future[dict[str, str]],
    ) -> None:
        self.questions = questions
        self.future = future


class AskUserTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user one or more questions when you need information "
        "that cannot be determined from code or context alone. Supports "
        "text input, radio (single select), select, and checkbox (multi select) "
        "question types."
    )
    params_model = AskUserParams
    category: str = "read"
    is_system_tool = True
    should_defer = True


    def __init__(self) -> None:
        self._pending_event: AskUserEvent | None = None
        # Set by the UI layer: invoked the moment a question becomes pending,
        # because the agent loop is blocked awaiting the future and cannot
        # surface the dialog through the normal event stream.
        self.on_pending: Callable[[AskUserEvent], Awaitable[None]] | None = None
        # Invoked when the question resolves WITHOUT a user answer (i.e. on
        # timeout): without it the dialog stays mounted with input disabled.
        self.on_expired: Callable[[AskUserEvent], Awaitable[None]] | None = None

    async def execute(self, params: AskUserParams) -> ToolResult:
        questions_data = [q.model_dump() for q in params.questions]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, str]] = loop.create_future()

        event = AskUserEvent(questions=questions_data, future=future)
        self._pending_event = event
        if self.on_pending is not None:
            try:
                await self.on_pending(event)
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "AskUserQuestion dialog failed to open: %s", e
                )

        try:
            answers = await asyncio.wait_for(future, timeout=300)
        except TimeoutError:
            if self.on_expired is not None:
                try:
                    await self.on_expired(event)
                except Exception as e:
                    logging.getLogger(__name__).warning(
                        "AskUserQuestion dialog cleanup failed: %s", e
                    )
            return ToolResult(
                output="User did not respond within 5 minutes", is_error=True
            )
        finally:
            self._pending_event = None

        lines = []
        for q in params.questions:
            answer = answers.get(q.name, "(no answer)")
            lines.append(f"{q.name}: {answer}")

        return ToolResult(output="\n".join(lines))
