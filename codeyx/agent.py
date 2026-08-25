from __future__ import annotations

import asyncio
import datetime
import logging
import os
import random
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codeyx.client import LLMClient
from codeyx.context import (
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    create_replacement_state,
    ensure_session_dir,
)
from codeyx.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from codeyx.conversation import ThinkingBlock as ConvThinkingBlock
from codeyx.hooks import HookContext, HookEngine
from codeyx.memory.auto_memory import MemoryManager
from codeyx.permissions import (
    PermissionChecker,
    PermissionMode,
)
from codeyx.prompts import build_environment_context, build_plan_mode_reminder, build_system_prompt
from codeyx.runtime import (
    AgentRuntimeState,
    ToolExecutionResult,
    ToolExecutionScheduler,
    ToolResultRecovery,
)
from codeyx.tools import ToolRegistry
from codeyx.tools.base import (
    MAX_OUTPUT_CHARS,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

log = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget tasks: retrieve the exception so
    asyncio doesn't log 'Task exception was never retrieved', and record it."""
    if not task.cancelled() and task.exception() is not None:
        log.warning("Background task failed: %s", task.exception())


PLAN_ADJECTIVES = [
    "bold", "bright", "calm", "cool", "deep", "fair", "fast", "fine",
    "glad", "keen", "kind", "lean", "mild", "neat", "pure", "safe",
    "slim", "soft", "tall", "warm", "wise", "grand", "swift", "vivid",
]
PLAN_NOUNS = [
    "sketch", "draft", "spark", "bloom", "trail", "ridge", "creek", "grove",
    "cliff", "cloud", "field", "forge", "frost", "haven", "pearl", "stone",
    "storm", "river", "tower", "delta", "flame", "orbit", "pulse", "shore",
]

_SHELL_METACHARS = frozenset(";|&$`><\n\r")


def _persist_allow_always_rule(
    checker: PermissionChecker, tool_name: str, arguments: dict[str, Any]
) -> None:
    """Persist a user's "always allow" answer as a local prefix rule — but
    only for metacharacter-free content. "Bash(echo hi*)" must not grow into
    a standing allow for "echo hi; <anything>": the rule layer does no shell
    screening of its own."""
    from codeyx.permissions.rules import Rule, extract_content

    content = extract_content(tool_name, arguments)
    if _SHELL_METACHARS.intersection(content):
        return
    pattern = f"{content[:60]}*" if len(content) > 60 else f"{content}*"
    checker.rule_engine.append_local_rule(
        Rule(tool_name=tool_name, pattern=pattern, effect="allow")
    )

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3


def _malformed_call_reason(tc: Any) -> str:
    """Human-readable reason a tool call was quarantined instead of run."""
    if not tc.tool_id:
        return f"Malformed tool call: missing tool_use id for {tc.tool_name}"
    return (
        f"Malformed tool call: arguments failed to parse as JSON for "
        f"{tc.tool_name}; nothing was executed"
    )


# ---------------------------------------------------------------------------
# AgentEvent types
# ---------------------------------------------------------------------------

@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


# ---------------------------------------------------------------------------
# LLM response collector
# ---------------------------------------------------------------------------

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, ToolCallStart):
                pass
            elif isinstance(event, ToolCallDelta):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 50,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        self.context_window = context_window
        # Per-agent scope: compaction rmtrees this dir, so sharing one dir
        # across agents (subagents, teammates) would delete files their
        # conversations still reference.
        self.session_dir = ensure_session_dir(work_dir, scope=uuid.uuid4().hex[:12])
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        # Holds snapshots needed to rebuild working context after Layer 2
        # collapses the conversation: most-recent file reads and skill
        # invocations. Recorded on each ReadFile / skill call; consumed by
        # auto_compact when the threshold trips.
        self.recovery_state: RecoveryState = RecoveryState()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self._loop_count = 0
        self._extracting = False
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._skill_loader: Any = None
        self._last_skill_recommendation_query: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.coordinator_mode: bool = False
        self.team_name: str = ""
        self._team_manager: Any = None
        # Strong ref to the in-flight memory-extraction task; asyncio keeps
        # only weak references, so an unreferenced task can be GC'd mid-run.
        self._memory_task: asyncio.Task | None = None

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    _plan_path_cache: Path | None = None

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        plans_dir = Path(self.work_dir) / ".codeyx" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(PLAN_ADJECTIVES)}-{random.choice(PLAN_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog


    def set_skill_loader(self, loader: Any) -> None:
        self._skill_loader = loader


    def set_agent_catalog(self, catalog: str, catalog_list: list[tuple[str, str]] | None = None) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def _build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def _drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    def _latest_user_query(self, conversation: ConversationManager) -> str:
        for msg in reversed(conversation.history):
            if msg.role != "user" or msg.tool_results:
                continue
            content = msg.content.strip()
            if not content or content.startswith("<system-reminder>"):
                continue
            return content
        return ""

    def _inject_skill_recommendations(self, conversation: ConversationManager) -> None:
        if self._skill_loader is None:
            return
        query = self._latest_user_query(conversation)
        if not query or query == self._last_skill_recommendation_query:
            return
        matches = [
            match for match in self._skill_loader.discover(query, limit=3)
            if match.name not in self.active_skills
        ]
        self._last_skill_recommendation_query = query
        if not matches:
            return
        lines = [
            "The user's request appears to match these Skills. "
            "If one is relevant, call LoadSkill before continuing:",
        ]
        for match in matches:
            lines.append(
                f"- {match.name}: {match.description} "
                f"(score={match.score}, source={match.source}, reason={match.reason})"
            )
        conversation.add_system_reminder("\n".join(lines))

    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        self._current_conversation = conversation
        env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
        conversation.inject_environment(env_context)

        memory_content = self.memory_manager.load() if self.memory_manager else ""
        conversation.inject_long_term_memory(self.instructions_content, memory_content)
        self._inject_skill_recommendations(conversation)

        if self.hook_engine:
            ctx = self._build_hook_context("session_start")
            await self.hook_engine.run_hooks("session_start", ctx)
            for he in self._drain_hook_events():
                yield he

        runtime_state = AgentRuntimeState()
        scheduler = ToolExecutionScheduler(self.registry)

        while True:
            iteration = runtime_state.next_turn()

            if iteration > self.max_iterations:
                yield ErrorEvent(
                    message=f"Agent reached maximum iterations ({self.max_iterations})"
                )
                break

            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)
                for he in self._drain_hook_events():
                    yield he

            self._consume_mailbox(conversation)

            # Layer 2: auto-compact if approaching context limit (operates on raw conv)
            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self.registry.get_all_schemas(self.protocol),
            )
            if isinstance(compact_result, CompactEvent):
                yield CompactNotification(
                    before_tokens=compact_result.before_tokens,
                    message=f"上下文已压缩（压缩前 {compact_result.before_tokens:,} tokens）",
                )
                conversation.inject_environment(env_context)
                mem = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(
                    self.instructions_content, mem
                )
                self._inject_skill_recommendations(conversation)
            elif isinstance(compact_result, str):
                yield ErrorEvent(message=compact_result)

            if self.hook_engine:
                ctx = self._build_hook_context("pre_send")
                await self.hook_engine.run_hooks("pre_send", ctx)
                for he in self._drain_hook_events():
                    yield he

            hook_prompts = (
                self.hook_engine.get_prompt_messages() if self.hook_engine else None
            )
            system = build_system_prompt(
                hook_prompts=hook_prompts,
                coordinator_mode=self.coordinator_mode,
                agent_catalog=self._agent_catalog_list or None,
            )

            if self.plan_mode:
                plan_path = str(self._get_plan_path())
                if self.permission_checker:
                    self.permission_checker.plan_file_path = plan_path
                plan_exists = self._get_plan_path().exists()
                plan_reminder = build_plan_mode_reminder(
                    plan_path, plan_exists, iteration
                )
                conversation.add_system_reminder(plan_reminder)

            if self.hook_engine:
                for note in self.hook_engine.drain_notifications():
                    conversation.add_system_reminder(
                        f"Hook [{note.hook_id}] {note.event}: {note.output}"
                    )

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            tools = self.registry.get_all_schemas(self.protocol)

            # Layer 1: apply tool-result budget right before the LLM call so that the
            # api_conv reflects all writes that happened earlier in this iteration
            # (system reminders, hook notifications, etc.). Original `conversation`
            # is never mutated; replacement decisions live in self.replacement_state.
            api_conv, _new_records = apply_tool_result_budget(
                conversation, self.session_dir, self.replacement_state
            )
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            collector = StreamCollector()
            llm_stream = self.client.stream(api_conv, system=system, tools=tools)
            async for event in collector.consume(llm_stream):
                yield event

            response = collector.response

            if self.hook_engine:
                ctx = self._build_hook_context("post_receive", message=response.text)
                await self.hook_engine.run_hooks("post_receive", ctx)
                for he in self._drain_hook_events():
                    yield he

            conversation.last_input_tokens = response.input_tokens

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            yield UsageEvent(
                input_tokens=self.total_input_tokens,
                output_tokens=self.total_output_tokens,
            )

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens":
                if not runtime_state.max_tokens_escalated:
                    self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    runtime_state.max_tokens_escalated = True
                    if response.text:
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                        conversation.add_user_message(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                        )
                    yield RetryEvent(reason="max_tokens escalation")
                    continue
                elif runtime_state.can_retry_output_tokens(MAX_OUTPUT_TOKENS_RECOVERIES):
                    recovery_count = runtime_state.record_output_token_retry()
                    if response.text or conv_thinking:
                        # Truncated output can be empty text; an empty
                        # assistant message makes the next API call 400.
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces."
                    )
                    yield RetryEvent(
                        reason=f"max_tokens recovery {recovery_count}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                    )
                    continue
                else:
                    # Escalated AND recoveries exhausted: this model keeps
                    # truncating. Falling through would execute truncated
                    # tool calls (arguments={}) — stop and surface instead.
                    if response.text or conv_thinking:
                        conversation.add_assistant_message(
                            response.text, thinking_blocks=conv_thinking
                        )
                    yield ErrorEvent(
                        message=(
                            "Output token limit hit even after escalation and "
                            f"{MAX_OUTPUT_TOKENS_RECOVERIES} recoveries; stopping "
                            "rather than execute a truncated tool call."
                        )
                    )
                    break
            else:
                runtime_state.reset_output_recoveries()

            if not response.tool_calls:
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                self._loop_count += 1
                if (
                    self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0
                    and self.memory_manager
                ):
                    # Keep a reference so the event loop cannot GC the task
                    # mid-flight (asyncio only holds weak refs).
                    self._memory_task = asyncio.ensure_future(
                        self._extract_memories(conversation)
                    )
                    self._memory_task.add_done_callback(_log_task_exception)
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    ctx = self._build_hook_context("session_end")
                    await self.hook_engine.run_hooks("session_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                yield LoopComplete(total_turns=iteration)
                break

            malformed_tool_calls = [
                tc
                for tc in response.tool_calls
                if not tc.tool_id or tc.parse_error
            ]
            valid_tool_calls = [
                tc
                for tc in response.tool_calls
                if tc.tool_id and not tc.parse_error
            ]
            if malformed_tool_calls:
                # Surface synthetic errors but keep going where possible:
                # aborting the whole run here silently discarded any VALID
                # sibling calls in the same response.
                log.warning(
                    "Dropping %d unexecutable tool call(s) (missing id or "
                    "unparsable arguments)",
                    len(malformed_tool_calls),
                )

            if not valid_tool_calls:
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                for tc in malformed_tool_calls:
                    result = ToolResultRecovery.synthetic_result(
                        _malformed_call_reason(tc)
                    )
                    yield ToolResultEvent(
                        tool_id=tc.tool_id,
                        tool_name=tc.tool_name,
                        output=result.output,
                        is_error=result.is_error,
                        elapsed=0.0,
                    )
                yield LoopComplete(total_turns=iteration)
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in valid_tool_calls
            ]
            conversation.add_assistant_message(
                response.text, tool_uses, thinking_blocks=conv_thinking
            )

            # Malformed calls are never executed and never enter history —
            # there is no tool_use block for a result to match. The events
            # below exist purely so the UI can show what was dropped.
            for tc in malformed_tool_calls:
                synthetic = ToolResultRecovery.synthetic_result(
                    _malformed_call_reason(tc)
                )
                yield ToolResultEvent(
                    tool_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    output=synthetic.output,
                    is_error=True,
                    elapsed=0.0,
                )

            tool_results: list[ToolResultBlock] = []
            # set once the results message is durably in history
            appended = False
            runtime_state.pending_tool_calls = list(valid_tool_calls)
            try:
                batches = scheduler.partition(valid_tool_calls)

                for batch in batches:
                    if batch.concurrent and len(batch.calls) > 1:
                        batch_results = await scheduler.run_parallel(
                            batch.calls,
                            self._execute_single_tool_direct,
                        )
                        for br in batch_results:
                            runtime_state.record_tool_result(br.is_unknown)
                            for he in self._drain_hook_events():
                                yield he
                            content = self._maybe_persist_or_truncate(
                                br.tool_id, br.result
                            )
                            tool_results.append(
                                ToolResultBlock(
                                    tool_use_id=br.tool_id,
                                    content=content,
                                    is_error=br.result.is_error,
                                )
                            )
                            yield ToolResultEvent(
                                tool_id=br.tool_id,
                                tool_name=br.tool_name,
                                output=br.result.output,
                                is_error=br.result.is_error,
                                elapsed=br.elapsed,
                            )
                    else:
                        for tc in batch.calls:
                            result: ToolResult | None = None
                            elapsed = 0.0
                            is_unknown = False

                            rejected = await self._run_pre_tool_hooks(tc)
                            for he in self._drain_hook_events():
                                yield he
                            if rejected is not None:
                                result = rejected
                                content = self._maybe_persist_or_truncate(
                                    tc.tool_id, result
                                )
                                tool_results.append(
                                    ToolResultBlock(
                                        tool_use_id=tc.tool_id,
                                        content=content,
                                        is_error=True,
                                    )
                                )
                                yield ToolResultEvent(
                                    tool_id=tc.tool_id,
                                    tool_name=tc.tool_name,
                                    output=result.output,
                                    is_error=True,
                                    elapsed=0.0,
                                )
                                continue

                            async for item in self._execute_tool(tc):
                                if isinstance(item, PermissionRequest):
                                    yield item
                                else:
                                    result, elapsed, is_unknown = item

                            if result is None:
                                result = ToolResultRecovery.synthetic_result(
                                    "Error: no result from tool"
                                )

                            runtime_state.record_tool_result(is_unknown)

                            await self._notify_post_tool_hooks(tc)
                            for he in self._drain_hook_events():
                                yield he

                            content = self._maybe_persist_or_truncate(
                                tc.tool_id, result
                            )
                            tool_results.append(
                                ToolResultBlock(
                                    tool_use_id=tc.tool_id,
                                    content=content,
                                    is_error=result.is_error,
                                )
                            )
                            yield ToolResultEvent(
                                tool_id=tc.tool_id,
                                tool_name=tc.tool_name,
                                output=result.output,
                                is_error=result.is_error,
                                elapsed=elapsed,
                            )

                runtime_state.pending_tool_calls = []

                if runtime_state.consecutive_unknown_tools >= 3:
                    yield ErrorEvent(
                        message="Agent terminated: too many consecutive unknown tool calls"
                    )
                    break

                conversation.add_tool_results_message(tool_results)
                appended = True
                if self.hook_engine:
                    ctx = self._build_hook_context("turn_end")
                    await self.hook_engine.run_hooks("turn_end", ctx)
                    for he in self._drain_hook_events():
                        yield he
                yield TurnComplete(turn=iteration)
            except BaseException:
                # Cancellation landed mid-tool-batch: answer every assistant
                # tool_use with a synthetic tool_result, or the next request
                # fails API validation until /clear.
                if not appended:
                    self._backfill_interrupted_tool_results(
                        conversation, runtime_state.pending_tool_calls, tool_results
                    )
                raise

    @staticmethod
    def _backfill_interrupted_tool_results(
        conversation: ConversationManager,
        pending_calls: list,
        collected: list[ToolResultBlock],
    ) -> None:
        """Repair history when cancellation lands mid-tool-batch: every
        assistant tool_use must be answered by a tool_result, or the next
        LLM request fails validation ("tool_use ids without tool_result
        blocks") until /clear. Wires the previously-dead
        ToolResultRecovery contract into the cancellation path."""
        have = {block.tool_use_id for block in collected}
        missing = [
            ToolResultBlock(
                tool_use_id=tc.tool_id,
                content="Error: execution was interrupted before returning a result.",
                is_error=True,
            )
            for tc in pending_calls
            if tc.tool_id and tc.tool_id not in have
        ]
        if collected or missing:
            conversation.add_tool_results_message(collected + missing)

    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.message_type != "text":
                    prefix = f"[{msg.message_type} from {msg.from_agent}]"
                content = f"{prefix} {msg.content}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    def _build_permission_description(self, tc: ToolCallComplete) -> str:
        if tc.tool_name == "Bash":
            return tc.arguments.get("command", tc.tool_name)
        if tc.tool_name in ("ReadFile", "WriteFile", "EditFile"):
            return tc.arguments.get("file_path", tc.tool_name)
        return str(tc.arguments)

    def _resolve_tool(self, tc: ToolCallComplete) -> tuple[Any | None, ToolResult | None, bool]:
        """Shared tool resolution: check existence and enabled state.

        Returns (tool, error_result, is_unknown). If error_result is not None,
        the caller should return it immediately without executing.
        """
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            return None, ToolResult(output=f"Error: unknown tool '{tc.tool_name}'", is_error=True), True
        if not self.registry.is_enabled(tc.tool_name):
            return None, ToolResult(output=f"Error: tool '{tc.tool_name}' is disabled", is_error=True), False
        return tool, None, False

    def _normalize_path_arguments(self, tc: ToolCallComplete) -> None:
        """Resolve relative path arguments against THIS agent's sandbox root
        rather than the process CWD, and give Bash a matching working
        directory. Worktree-isolated agents run with a sandbox derived from
        their worktree while the process CWD never chdirs — without this,
        permission checks evaluate one location and file tools write to
        another (the parent repo). Mutates tc.arguments in place; hooks and
        permission checks then observe the exact paths that will be used."""
        if self.permission_checker is None:
            return
        root = self.permission_checker.sandbox.project_root
        args = tc.arguments
        for key in ("file_path", "path"):
            value = args.get(key)
            if isinstance(value, str) and value.strip() and not os.path.isabs(value):
                args[key] = str(root / value)
        if tc.tool_name == "Bash":
            cwd = args.get("cwd")
            if isinstance(cwd, str) and cwd.strip() and not os.path.isabs(cwd):
                args["cwd"] = str(root / Path(cwd))
            elif "cwd" not in args or not isinstance(cwd, str) or not cwd.strip():
                args["cwd"] = str(root)

    async def _run_tool(self, tool: Any, tc: ToolCallComplete) -> ToolResult:
        """Shared tool execution with exception handling."""
        try:
            params = tool.params_model.model_validate(tc.arguments)
            result = await tool.execute(params)
        except ValidationError as e:
            result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
        except OSError as e:
            log.warning("Tool '%s' OS error: %s", tc.tool_name, e)
            result = ToolResult(output=f"OS error: {e}", is_error=True)
        except TimeoutError as e:
            log.warning("Tool '%s' timed out: %s", tc.tool_name, e)
            result = ToolResult(output=f"Timeout error: {e}", is_error=True)
        except Exception as e:
            log.exception("Tool '%s' unexpected error", tc.tool_name)
            result = ToolResult(output=f"Tool execution error: {e}", is_error=True)
        self._snapshot_for_recovery(tc, result)
        return result

    async def _run_pre_tool_hooks(self, tc: ToolCallComplete) -> ToolResult | None:
        """Fire pre_tool_use hooks. Returns a rejection ToolResult or None."""
        if not self.hook_engine:
            return None
        file_path = self._infer_file_path(tc.arguments)
        hook_ctx = self._build_hook_context(
            "pre_tool_use",
            tool_name=tc.tool_name,
            tool_args=tc.arguments,
            file_path=file_path,
        )
        rejection = await self.hook_engine.run_pre_tool_hooks(hook_ctx)
        if rejection is not None:
            return ToolResult(output=f"Hook rejected: {rejection.reason}", is_error=True)
        return None

    async def _notify_post_tool_hooks(self, tc: ToolCallComplete) -> None:
        if not self.hook_engine:
            return
        file_path = self._infer_file_path(tc.arguments)
        hook_ctx = self._build_hook_context(
            "post_tool_use",
            tool_name=tc.tool_name,
            tool_args=tc.arguments,
            file_path=file_path,
        )
        await self.hook_engine.run_hooks("post_tool_use", hook_ctx)

    def _noninteractive_permission_denial(
        self, tool: Any, tc: ToolCallComplete, allow_ask: bool
    ) -> ToolResult | None:
        """Permission verdict for contexts that cannot yield a PermissionRequest
        (concurrent batches, sub-agents). Returns a denial ToolResult, or None
        when execution may proceed. `allow_ask` auto-approves `ask` decisions —
        reserved for agents explicitly running in DONT_ASK mode."""
        if self.permission_checker is None:
            return None
        decision = self.permission_checker.check(tool, tc.arguments)
        if decision.effect == "deny":
            return ToolResult(
                output=f"Permission denied: {decision.reason}",
                is_error=True,
            )
        if decision.effect == "ask" and not allow_ask:
            return ToolResult(
                output=(
                    "Permission denied: this action requires user confirmation, "
                    "which is unavailable in this execution context. "
                    "Run it as a standalone (non-parallel) tool call."
                ),
                is_error=True,
            )
        return None

    async def _execute_single_tool_direct(
        self, tc: ToolCallComplete
    ) -> ToolExecutionResult:
        """Executor for concurrent batches. Applies the SAME hook + permission
        gates as the interactive serial path; `ask` outcomes are denied because
        a gather() context cannot surface a permission dialog."""
        start = time.monotonic()
        tool, error, is_unknown = self._resolve_tool(tc)
        if error is not None:
            return ToolExecutionResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=error,
                elapsed=time.monotonic() - start,
                is_unknown=is_unknown,
            )

        self._normalize_path_arguments(tc)

        rejected = await self._run_pre_tool_hooks(tc)
        if rejected is not None:
            return ToolExecutionResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=rejected,
                elapsed=time.monotonic() - start,
                is_unknown=False,
            )

        denied = self._noninteractive_permission_denial(tool, tc, allow_ask=False)
        if denied is not None:
            return ToolExecutionResult(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                result=denied,
                elapsed=time.monotonic() - start,
                is_unknown=False,
            )

        result = await self._run_tool(tool, tc)
        await self._notify_post_tool_hooks(tc)
        return ToolExecutionResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=result,
            elapsed=time.monotonic() - start,
            is_unknown=False,
        )

    async def _execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[tuple[ToolResult, float, bool]]:
        start = time.monotonic()
        tool, error, is_unknown = self._resolve_tool(tc)

        if error is not None:
            yield error, time.monotonic() - start, is_unknown
            return

        self._normalize_path_arguments(tc)

        # Permission check
        if self.permission_checker:
            decision = self.permission_checker.check(tool, tc.arguments)

            if decision.effect == "deny":
                result = ToolResult(
                    output=f"Permission denied: {decision.reason}",
                    is_error=True,
                )
                yield result, time.monotonic() - start, False
                return

            if decision.effect == "ask":
                loop = asyncio.get_running_loop()
                future: asyncio.Future[PermissionResponse] = loop.create_future()
                desc = self._build_permission_description(tc)
                yield PermissionRequest(
                    tool_name=tc.tool_name,
                    description=desc,
                    future=future,
                )
                response = await future

                if response == PermissionResponse.DENY:
                    result = ToolResult(
                        output="Permission denied: 用户拒绝了此操作",
                        is_error=True,
                    )
                    yield result, time.monotonic() - start, False
                    return

                if response == PermissionResponse.ALLOW_ALWAYS:
                    _persist_allow_always_rule(
                        self.permission_checker, tc.tool_name, tc.arguments
                    )

        result = await self._run_tool(tool, tc)
        yield result, time.monotonic() - start, False

    def _snapshot_for_recovery(
        self, tc: ToolCallComplete, result: ToolResult
    ) -> None:
        """Capture what ReadFile just handed to the model so auto_compact
        can re-attach the bytes after Layer 2 collapses the transcript.

        Uses the tool result content directly instead of re-reading from disk,
        avoiding blocking I/O in the async event loop.
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        # Use the tool result directly — it already contains the file content
        # that was just read by the ReadFile tool, avoiding redundant disk I/O.
        self.recovery_state.record_file_read(path, result.output)

    async def _extract_memories(
        self, conversation: ConversationManager
    ) -> None:
        if self._extracting or not self.memory_manager:
            return
        self._extracting = True
        try:
            await self.memory_manager.extract(
                self.client, conversation, self.protocol
            )
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False

    async def manual_compact(
        self, conversation: ConversationManager
    ) -> CompactNotification | ErrorEvent:
        # auto_compact will replace `conversation.history` with the summary, so any
        # tool-result content (raw or replaced) is about to be discarded entirely.
        # Skip apply_tool_result_budget here — its only purpose in the main loop is
        # to produce an api_conv for the LLM call, and we don't issue one in this
        # path that needs to see replacements (the summarization call inside
        # auto_compact operates on the raw conversation).
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            tool_schemas=self.registry.get_all_schemas(self.protocol),
        )
        if isinstance(result, CompactEvent):
            env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
            conversation.inject_environment(env_context)
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )
            self._inject_skill_recommendations(conversation)
            return CompactNotification(
                before_tokens=result.before_tokens,
                message=f"上下文已压缩（压缩前 {result.before_tokens:,} tokens）",
            )
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    async def run_to_completion(
        self, task: str, conversation: ConversationManager | None = None,
    ) -> str:
        if conversation is None:
            conversation = ConversationManager()

        # Built unconditionally: the post-compact path below re-injects
        # env_context even for caller-supplied (fork/sub-agent) conversations.
        # Injection itself is flag-guarded and idempotent.
        env_context = build_environment_context(
            self.work_dir, self.active_skills, self._skill_catalog, self._agent_catalog
        )
        conversation.inject_environment(env_context)

        if self.instructions_content:
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )

        if task:
            conversation.add_user_message(task)
        self._inject_skill_recommendations(conversation)

        hook_prompts = (
            self.hook_engine.get_prompt_messages() if self.hook_engine else None
        )
        system = build_system_prompt(
            hook_prompts=hook_prompts,
            coordinator_mode=self.coordinator_mode,
        )

        tools = self.registry.get_all_schemas(self.protocol)

        log.info(
            "[run_to_completion] agent=%s tools=%d names=%s coordinator=%s",
            self.agent_id,
            len(tools),
            [t["name"] for t in tools][:10],
            self.coordinator_mode,
        )

        last_text = ""

        for iteration in range(1, self.max_iterations + 1):
            if self.hook_engine:
                ctx = self._build_hook_context("turn_start")
                await self.hook_engine.run_hooks("turn_start", ctx)

            self._consume_mailbox(conversation)

            compact_result = await auto_compact(
                conversation,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self.registry.get_all_schemas(self.protocol),
            )
            if isinstance(compact_result, CompactEvent):
                conversation.inject_environment(env_context)
                mem = self.memory_manager.load() if self.memory_manager else ""
                conversation.inject_long_term_memory(
                    self.instructions_content, mem
                )
                self._inject_skill_recommendations(conversation)

            deferred_names = self.registry.get_deferred_tool_names()
            if deferred_names:
                conversation.add_system_reminder(
                    "The following deferred tools are available via ToolSearch. "
                    "Their schemas are NOT loaded - use ToolSearch with "
                    'query "select:<name>[,<name>...]" to load tool schemas before calling them:\n'
                    + "\n".join(deferred_names)
                )

            api_conv, _new_records = apply_tool_result_budget(
                conversation, self.session_dir, self.replacement_state
            )
            if _new_records:
                append_replacement_records(self.session_dir, _new_records)

            collector = StreamCollector()
            llm_stream = self.client.stream(api_conv, system=system, tools=tools)
            async for _event in collector.consume(llm_stream):
                pass

            response = collector.response
            conversation.last_input_tokens = response.input_tokens
            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens

            if response.text:
                last_text = response.text

            log.info(
                "[run_to_completion] agent=%s iter=%d tool_calls=%d text_len=%d stop=%s",
                self.agent_id, iteration, len(response.tool_calls),
                len(response.text), response.stop_reason,
            )

            conv_thinking = [
                ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
                for tb in response.thinking_blocks
            ]

            if response.stop_reason == "max_tokens":
                # Truncated output: tool calls may be partial/corrupt
                # (arguments={} after a failed JSON parse). The interactive
                # loop refuses to execute truncated calls; mirror that here.
                # Checked BEFORE the quarantine split so the all-quarantined
                # case — the most-truncated one — still reports the note
                # instead of returning bare cut-off prose.
                log.warning(
                    "[run_to_completion] agent=%s truncated at max_tokens; "
                    "discarding %d tool call(s)",
                    self.agent_id, len(response.tool_calls),
                )
                if response.text or conv_thinking:
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                note = (
                    "[Truncated by the max_tokens limit mid-tool-call; "
                    "remaining work was discarded.]"
                )
                return f"{last_text}\n\n{note}" if last_text else note

            # Same quarantine rule as the interactive loop: id-less or
            # unparsable calls must never reach a tool.
            bad_calls = [
                tc for tc in response.tool_calls
                if not tc.tool_id or tc.parse_error
            ]
            good_calls = [
                tc for tc in response.tool_calls
                if tc.tool_id and not tc.parse_error
            ]
            if bad_calls:
                log.warning(
                    "[run_to_completion] agent=%s dropping %d unexecutable "
                    "tool call(s)",
                    self.agent_id, len(bad_calls),
                )

            if not good_calls:
                if response.text or conv_thinking:
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                break

            tool_uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_id,
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in good_calls
            ]
            conversation.add_assistant_message(
                response.text, tool_uses, thinking_blocks=conv_thinking
            )

            tool_results: list[ToolResultBlock] = []
            for tc in good_calls:
                result = await self._execute_tool_noninteractive(tc)
                content = self._maybe_persist_or_truncate(tc.tool_id, result)
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=tc.tool_id,
                        content=content,
                        is_error=result.is_error,
                    )
                )

            conversation.add_tool_results_message(tool_results)

            if self.hook_engine:
                ctx = self._build_hook_context("turn_end")
                await self.hook_engine.run_hooks("turn_end", ctx)

        return last_text

    async def _execute_tool_noninteractive(
        self, tc: ToolCallComplete
    ) -> ToolResult:
        """Tool pipeline for sub-agents / non-interactive runs. Same gates as
        the interactive path, except an `ask` decision auto-approves only in
        DONT_ASK mode and is denied otherwise (no user to prompt)."""
        tool, error, _ = self._resolve_tool(tc)
        if error is not None:
            return error

        # Same path-basis normalization as the interactive paths: relative
        # arguments must resolve against this agent's work root, or a
        # worktree-isolated agent gets permission-checked on one path while
        # the tool writes to another.
        self._normalize_path_arguments(tc)

        rejected = await self._run_pre_tool_hooks(tc)
        if rejected is not None:
            return rejected

        denied = self._noninteractive_permission_denial(
            tool, tc, allow_ask=self.permission_mode == PermissionMode.DONT_ASK
        )
        if denied is not None:
            return denied

        result = await self._run_tool(tool, tc)

        await self._notify_post_tool_hooks(tc)

        return result

    def _maybe_persist_or_truncate(self, tool_use_id: str, result: ToolResult | str) -> str:
        from codeyx.context.manager import (
            SINGLE_RESULT_CHAR_LIMIT,
            make_persisted_preview,
            persist_tool_result,
        )

        if isinstance(result, ToolResult):
            text = result.output
        else:
            text = result

        if len(text) > SINGLE_RESULT_CHAR_LIMIT:
            fp = persist_tool_result(tool_use_id, text, self.session_dir)
            if isinstance(result, ToolResult):
                result.persisted_path = str(fp)
                result.display_hint = "persisted_preview"
                result.metadata["original_chars"] = len(text)
            return make_persisted_preview(text, fp)
        if len(text) > MAX_OUTPUT_CHARS:
            if isinstance(result, ToolResult):
                result.display_hint = "truncated"
                result.metadata["original_chars"] = len(text)
            return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
        return text
