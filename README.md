<div align="center">

# ⚡ CodeYX

**A terminal-native AI coding agent — built from scratch in Python.**

Reads, writes, and executes code autonomously through a structured tool-calling loop,
with a five-tier permission model keeping every action accountable.

[![CI](https://github.com/EthyleneC2H4/CodeYX/actions/workflows/test.yml/badge.svg)](https://github.com/EthyleneC2H4/CodeYX/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-647%20passing-brightgreen.svg)](#running-tests)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#-license)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](pyproject.toml)

**English** · [简体中文](README.zh-CN.md)

</div>

---

## Why CodeYX?

Most AI coding assistants are hosted services or thin wrappers around proprietary stacks.
CodeYX is a **from-scratch, fully local agent runtime**: the ReAct loop, tool scheduler,
permission engine, context compressor, memory system, and multi-agent orchestration are
all implemented directly on `asyncio` — no framework lock-in, every layer inspectable.

```text
❯ /plan Add JWT auth to the API
  ✻ Planning… reads routes.py, auth.py, tests/
  ── Plan written to .codeyx/plans/bold-spark-0825-1432.md
  ── [1] YOLO  [2] Manual  [3] Feedback
❯ 1
  ✻ Writing src/auth/jwt.py … ✓
  ✻ Editing src/api/routes.py … ✓
  ✻ Bash: pytest tests/auth -q … 24 passed
```

## ✨ Features

| | |
|---|---|
| 🌐 **Multi-protocol LLM support** | Anthropic Messages, OpenAI Responses, OpenAI-compatible Chat Completions (vLLM, Ollama, …), and DeepSeek — unified behind one `stream()` interface. |
| 🧰 **30+ built-in tools** | ReadFile / WriteFile / EditFile / Bash / Glob / Grep / Agent (sub-agents) / Task* / Team* and more. |
| 🛡️ **Five-tier permission model** | Plan-mode exceptions → safe-command whitelist → dangerous-command blacklist → path sandbox → rule engine → mode matrix → human confirmation. Any tier can deny; first denial short-circuits. |
| 🗜️ **Two-layer context compression** | Per-turn tool-result budgets with disk persistence, then LLM summarisation near the window limit — with recovery snapshots that re-attach file reads after compaction. Decisions are frozen across turns to keep prompt-cache prefixes byte-identical. |
| 🧠 **Cross-session memory** | An LLM extractor runs every 5 turns to classify memories (preferences / feedback / project knowledge / references) to disk; new sessions inherit them automatically. |
| 🔌 **MCP integration** | stdio + Streamable HTTP servers via the Model Context Protocol. Tools are deferred — only names advertised, schemas discovered on demand (~85% initial-token saving in multi-server setups). Explicit connect/call timeouts so a hung server never stalls the loop. |
| 🧩 **Skill system** | Reusable prompt templates as Markdown + YAML frontmatter, invokable via `/skill`. Fork-mode skills run in an isolated agent with inherited permissions. |
| 👥 **Sub-agents & Teams** | Isolated agents with filtered toolsets, custom models, optional git-worktree isolation. Teams collaborate through atomic filesystem mailboxes under Coordinator orchestration — in-process, tmux, or iTerm2 panes running real `codeyx` processes. |
| 🪝 **Hooks engine** | Shell / HTTP / prompt actions at 15 lifecycle events with conditional matching (`==`, `!=`, regex, fnmatch), shell-quoted interpolation, enforced timeouts, and pre-tool rejection. |

## 🚀 Quick Start

### Prerequisites

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/) *(recommended)* or pip
- An API key for at least one provider

### Install & launch

```bash
git clone https://github.com/EthyleneC2H4/CodeYX.git
cd CodeYX
uv sync

cp config.example.yaml .codeyx/config.yaml   # then fill in your key
uv run codeyx
```

Headless one-shot (also how teammates boot inside panes):

```bash
uv run codeyx -p "summarize this repo" --work-dir /path/to/project

# join a team as a named teammate
uv run codeyx --team my-team --agent-name worker-1 --prompt "start task"
```

<details>
<summary><b>Configuration</b> (click to expand)</summary>

Config merges from user → project → local scope (`~/.codeyx/config.yaml` overrides nothing;
`.codeyx/config.yaml` is project-local). `${VAR}` env interpolation is supported everywhere.

```yaml
providers:
  - name: anthropic
    protocol: anthropic          # anthropic | openai | openai-compat | deepseek
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-5
    api_key: ${ANTHROPIC_API_KEY}
    thinking: false              # extended thinking + larger output budget
    context_window: 200000

# permission_mode: default       # default | acceptEdits | plan | bypassPermissions | dontAsk

mcp_servers: []
  # - name: filesystem
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

worktree:
  symlink_directories: ["node_modules", ".venv", "vendor"]
  stale_cleanup_interval: 3600
```

Full schema: [`config.example.yaml`](config.example.yaml)

</details>

### Running tests

```bash
uv run pytest                    # full suite — 647 tests
uv run ruff check codeyx tests   # lint gate
```

## 🏗️ Architecture

Five layers, each independently inspectable:

```text
┌─────────────────────────────────────────────────────┐
│  Presentation   Textual TUI · slash commands        │
├─────────────────────────────────────────────────────┤
│  Engine         ReAct loop · conversation manager   │
├─────────────────────────────────────────────────────┤
│  Tooling        File I/O · Bash · search · MCP      │
│                 sub-agents · teams                  │
├─────────────────────────────────────────────────────┤
│  Memory         Auto-memory · session persistence   │
├─────────────────────────────────────────────────────┤
│  Security       5-tier permissions · sandbox ·      │
│                 rules · hooks                       │
└─────────────────────────────────────────────────────┘
```

```text
codeyx/
├── agent.py          # core ReAct loop (async generator, typed AgentEvents)
├── app.py            # Textual TUI application
├── client.py         # LLM abstraction (4 protocols)
├── conversation.py   # message model + multi-protocol serialisation
├── prompts.py        # priority-ordered system-prompt assembly
├── tools/            # built-in tools (base + 20+ implementations)
├── runtime/          # serial + bounded-concurrency tool scheduler
├── permissions/      # checker · dangerous detector · sandbox · rules
├── context/          # 2-layer compression + ContentReplacementState
├── memory/           # auto-memory extraction + session persistence
├── agents/           # sub-agent defs, loading, tool filtering, tasks
├── teams/            # mailbox · coordinator · tmux/iterm2/in-process
├── hooks/            # lifecycle hooks (15 events, 4 action types)
├── mcp/              # MCP client · manager · deferred tool wrapper
├── skills/           # skill loader · parser · executor
├── commands/         # slash-command registry + handlers
└── worktree/         # git worktree isolation + lifecycle cleanup
```

## 🔒 The security model, concretely

Every tool call — serial **or** parallel — flows through the same pipeline:

```text
tool call ─► pre_tool_use hooks ─► permission pipeline ─► execute ─► post_tool_use hooks
                                        │
  ① plan-mode exceptions                │  any tier may DENY;
  ② safe-command whitelist              │  first denial short-circuits.
  ③ dangerous-command blacklist         │
  ④ path sandbox                        │  "ask" suspends the loop on an
  ⑤ project/local rule engine           │  asyncio.Future resolved by the TUI —
  ⑥ mode matrix                         │  cancelling always resolves it too,
  ⑦ human confirmation                  │  so the input can never stick.
```

Highlights:

- **Parallel batches can't bypass checks.** Concurrent execution shares the authorization
  path; ask-requiring actions are rejected with guidance instead of silently approved.
- **Derived checkers, not fresh ones.** Sub-agents/forks/teammates inherit detectors, rules,
  and sandbox roots via `PermissionChecker.derive()` — forks auto-approve *prompts* but deny
  rules and hooks still bind.
- **Token-level dangerous-command detection.** `rm -rf /` is caught in any flag order
  (`--force --recursive`, mixed short/long, chained after `&&`), plus mkfs/dd/fork bombs/
  piped remote scripts/sudo/system-path redirection.
- **Hook injection is defended twice:** interpolated values are `shlex.quote`d into shell
  commands, and reserved context keys (`$MESSAGE`, `$TOOL_NAME`, …) can't be shadowed by
  tool arguments.
- **"Always allow" can't be amplified.** Persisted prefix rules reject shell metacharacters,
  so approving `echo hi` never whitelists `echo hi; rm -rf ~`.

## 🤖 Multi-agent & teams

```text
                 ┌────────────┐
                 │ Coordinator │  filtered toolset: spawn/delegate only
                 └─────┬──────┘
        ┌──────────────┼──────────────┐
   ┌────▼────┐    ┌────▼────┐    ┌────▼────┐
   │ worker-1 │    │ worker-2 │    │ worker-3 │   worktree-isolated
   └────┬────┘    └────┬────┘    └────┬────┘
        └──────────────┴──────────────┘
             atomic fs mailboxes (.json claim/recovery)
             structured TaskSpec → WorkerState machine
```

- **Isolation levels:** in-process filtered agents → ephemeral git worktrees → full panes
  (`tmux`/iTerm2) running real `codeyx` processes with boot parameters.
- **Mailboxes are crash-safe:** atomic publish, claim-with-recovery semantics, quarantine
  instead of destruction on malformed messages.
- **Worktrees are lifecycle-managed:** stale sweeps by naming pattern; dirty worktrees are
  kept at shutdown with a warning, clean ones removed.

## ⌨️ Slash commands

| Command | Purpose |
|---|---|
| `/plan` `/do` | Enter plan mode / return to execution |
| `/review` | Structured code review pass |
| `/compact [focus]` | Compress conversation history |
| `/session list/resume` | Session persistence |
| `/memory list/catalog/search/clear` | Inspect the memory store |
| `/skill list/search` | Discover and invoke skills |
| `/permission` | Switch permission mode |
| `/status` | Runtime status overview |
| `/help` | All commands + aliases |

## 📖 Key design decisions

- **AsyncIterator event stream.** `Agent.run()` yields typed `AgentEvent` dataclasses; the
  agent core and UI are fully decoupled — the same loop drives the TUI, headless prompts,
  and teammate panes.
- **ContentReplacementState.** Budget decisions are frozen once and replayed identically,
  keeping Anthropic prompt-cache prefixes byte-stable between requests.
- **Deferred MCP tools.** Only names are advertised up front; full schemas arrive on demand
  via `ToolSearch`.
- **Human-in-the-loop as dataflow.** Permission prompts suspend the loop on a `Future`; the
  TUI resolves it. Cancellation resolves every pending dialog deterministically.

## 🗺️ Roadmap

- [ ] Streamed tool-call rendering refinements
- [ ] Memory aggregate budget controls
- [ ] Sandbox TOCTOU hardening notes & audit trail
- [ ] Trajectory evaluation harness GA

See [docs/known-issues.md](docs/known-issues.md) for the live issue ledger.

## 🤝 Contributing

Issues and PRs are welcome. Before opening a PR:

```bash
uv run pytest                     # all green
uv run ruff check codeyx tests    # lint clean
```

Please keep commit messages in English.

## 📄 License

[MIT](LICENSE) © c2h4
