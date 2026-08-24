# CodeYX

A lightweight terminal-native AI Coding Agent built from scratch in Python. It gives LLMs the ability to read, write, and execute code autonomously through a structured tool-calling loop.

## Architecture

CodeYX is organized into five layers, each responsible for a distinct concern:

```
  Presentation  ──  Textual TUI + Slash Commands
  Engine        ──  ReAct Agent Loop + Conversation Management
  Tooling       ──  File I/O / Bash / Search / MCP / Sub-agents
  Memory        ──  Auto-memory Extraction + Session Persistence
  Security      ──  Five-tier Permission + Sandbox + Rule Engine
```

The Agent loop follows the ReAct pattern: LLM generates text or tool calls → Agent executes tools → results feed back → iterate. A Plan Mode variant (`/plan`) lets the Agent design an implementation strategy before writing any code; on plan approval the user can jump straight into auto-approved execution.

## Features

- **Multi-protocol LLM support** — Anthropic Messages API, OpenAI Responses API, OpenAI-compatible Chat Completions (vLLM, Ollama, etc.), and DeepSeek -- all unified behind a single `stream()` interface
- **30+ built-in tools** — ReadFile, WriteFile, EditFile, Bash, Glob, Grep, Agent (sub-agent spawning), TaskCreate/Get/List/Update, TeamCreate/Delete, and more
- **Five-tier permission model** — Plan Mode exceptions → safe command whitelist → dangerous command blacklist → path sandbox → rule engine → permission mode matrix → human confirmation. Any tier can deny; rejection short-circuits. Serial and parallel tool execution share one authorization pipeline, so batching cannot bypass checks.
- **Two-layer context compression** — Layer 1 applies per-turn tool-result budget controls with disk persistence. Layer 2 triggers LLM-based summarisation when approaching the context window, with Recovery State to re-attach snapped file reads after compaction.
- **Cross-session memory** — An LLM-driven extractor runs every 5 turns to classify memories into four types (user preferences, feedback, project knowledge, references) and persist them to disk. New sessions automatically inherit accumulated context.
- **MCP integration** — Connect to external tool servers via the Model Context Protocol (stdio and Streamable HTTP). All MCP tools are deferred -- only loaded on demand -- keeping the initial context window lean. Connects and tool calls are bounded by explicit timeouts so a hung server cannot stall the loop.
- **Skill system** — Reusable prompt templates packaged as Markdown files with YAML frontmatter, invokable via `/skill`. Fork-mode skills run in an isolated agent with an inherited, auto-approving permission checker.
- **Sub-agents & Teams** — Spawn isolated agents with filtered tool sets, custom models, and optional Git worktree isolation (`isolation: worktree`, ephemeral worktrees auto-named and swept when stale; dirty worktrees are preserved at shutdown). Team mode supports parallel multi-agent collaboration with file-system mailbox communication (atomic claim semantics) and Coordinator pattern orchestration. Teammates can also be launched as real terminal processes (`codeyx --work-dir … --team … --agent-name … --prompt …`) inside tmux/iTerm2 panes.
- **Hooks engine** — Inject shell commands, HTTP calls, or prompt text at 15 lifecycle events (`pre_tool_use`, `post_tool_use`, `session_start`, etc.) with conditional execution (`==`, `!=`, regex, fnmatch) and optional tool rejection. Template interpolation is shell-quoted by default and every hook action runs under an enforced timeout.

## Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| TUI Framework | [Textual](https://textual.textualize.io/) |
| Data Validation | Pydantic |
| LLM SDKs | Anthropic SDK, OpenAI SDK |
| MCP | `mcp` (Model Context Protocol SDK) |
| Async | `asyncio` (native) |
| Testing | pytest + pytest-asyncio, security test suite, perf harness |

## Quick Start

### Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (or pip)

### 1. Configuration

Copy [`config.example.yaml`](config.example.yaml) to `.codeyx/config.yaml` in your project directory (or `~/.codeyx/config.yaml` for user-wide settings):

```yaml
providers:
  - name: anthropic
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}   # env interpolation; or omit to use the protocol default env var
    model: claude-sonnet-4-5
    thinking: true

  # OpenAI example
  # - name: openai
  #   protocol: openai
  #   base_url: https://api.openai.com/v1
  #   model: gpt-4o

  # DeepSeek example
  # - name: deepseek
  #   protocol: deepseek
  #   base_url: https://api.deepseek.com
  #   model: deepseek-chat

  # OpenAI-compatible example (vLLM, Ollama, etc.)
  # - name: local
  #   protocol: openai-compat
  #   base_url: http://localhost:11434/v1
  #   api_key: "ollama"
  #   model: llama3

# Optional: MCP servers
# mcp_servers:
#   - name: context7
#     command: npx
#     args: ["-y", "@upstash/context7-mcp"]
```

**Protocol options:** `anthropic` | `openai` | `openai-compat` | `deepseek`

### 2. Install & Run

```bash
# Install dependencies
uv sync

# Launch the TUI
uv run codeyx

# Headless one-shot prompt (also used by team pane spawning)
uv run codeyx -p "summarize this repo" --work-dir /path/to/project

# Boot directly into a team as a named agent
uv run codeyx --team my-team --agent-name worker-1 --prompt "start task"
```

### 3. Running Tests

```bash
uv run pytest                      # full suite (600+ tests)
uv run ruff check codeyx tests    # lint gate (CI-blocking subset clean)
```

## Project Structure

```
codeyx/
  agent.py          Core ReAct loop (async generator, event-driven)
  client.py         LLM abstraction (Anthropic / OpenAI / DeepSeek / OpenAI-Compat)
  app.py            Textual TUI application
  prompts.py        System prompt assembly (priority-ordered sections)
  conversation.py   Message model + multi-protocol serialisation
  config.py         YAML config loading (user → project → local merging)
  validator.py      Config schema validation

  tools/            All built-in tools (base.py + 20+ implementations)
  permissions/      Five-tier permission checker + dangerous command detector + sandbox
  runtime/          Tool execution scheduler (serial + bounded-concurrency parallel)
  context/          Two-layer context compression + ContentReplacementState
  memory/           Auto-memory extraction + session persistence + instruction loading
  agents/           Sub-agent definitions, loading, tool filtering, task management
  teams/            Multi-agent teams (mailbox, coordinator, tmux/iterm2/in-process backends)
  hooks/            Lifecycle hooks (15 events, 4 action types, conditional execution)
  mcp/              MCP client, manager, tool wrapper
  skills/           Skill loader, parser, executor
  commands/         Slash-command registry and handlers
  worktree/         Git worktree management for agent isolation
```

## Key Design Decisions

**AsyncIterator event stream.** `Agent.run()` yields typed `AgentEvent` dataclasses consumed by the TUI. The Agent and UI are fully decoupled -- the same core can drive a TUI, an SDK, or a headless mode.

**One authorization pipeline for serial and parallel tools.** Both execution paths funnel through the same pre-hook → permission-check → post-hook helpers. In concurrent batches, actions requiring interactive confirmation are rejected with an actionable error instead of silently approved or deadlocked; only explicitly non-interactive contexts (e.g. sub-agents running under DONT_ASK) auto-approve.

**PermissionChecker.derive().** Sub-agents, skill forks, and teammates inherit the parent's detectors, rule engine, and extra sandbox roots while overriding just what differs (mode, project root). A fork gets `derive(mode=DONT_ASK)` so it can never surface a dialog it has no UI for -- deny rules still bind.

**ContentReplacementState (decision freezing).** Tool-result budget decisions are recorded once and frozen across turns. This keeps Anthropic prompt-cache prefixes byte-identical between requests, maintaining cache-hit rates.

**Deferred MCP tools.** MCP tools register with `should_defer=True` -- only names are advertised. The LLM discovers full schemas on demand via `ToolSearch`, reducing initial token overhead by ~85% in multi-server scenarios.

**Human-in-the-loop via asyncio.Future.** Permission requests suspend the Agent loop with a `Future` that the TUI resolves when the user responds. Cancelling the agent resolves any pending dialog futures (deny/empty answers) and unmounts the widgets, so the input box can never get stuck behind an orphaned prompt.

**Five-tier security.** Each layer (Plan Mode → safe commands → dangerous patterns → sandbox → rules → mode → ask) can independently deny; the first denial short-circuits. Dangerous command detection uses case-insensitive regex patterns covering `rm -rf /` (including long-form `--recursive`/`--force`/`--no-preserve-root` variants), `mkfs`, `dd`, fork bombs, piped scripts, `sudo`, and system-path redirection. Path traversal must resolve strictly inside the project root; macOS `/var` → `/private/var` symlink aliases are handled at resolution time.

## License

MIT
