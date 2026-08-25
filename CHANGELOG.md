# Changelog

All notable changes to CodeYX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [0.2.0] - 2026-08-25

Security and reliability hardening release: a full-project audit surfaced 50
confirmed defects across three severity waves; all are fixed with regression
tests (suite grew from ~608 to 740+ tests).

### Security

- Removed `env` from the safe-command whitelist — it auto-allowed arbitrary
  commands (`env rm -rf /`) ahead of the dangerous-command detector.
- Process substitution `<(...)` no longer bypasses metacharacter defences.
- Newline-chained commands are split before safety checks, closing a
  multi-command injection vector.
- iTerm2 teammate spawn quoting fixed; model-controlled arguments can no
  longer execute as top-level shell commands in new panes.
- `tool_use_id` values are sanitized before use as persisted-output filenames
  (path traversal); NUL-byte paths return a deny verdict instead of crashing
  the turn.
- Worktree-isolated sub-agents now resolve relative paths against the sandbox
  root, not the process CWD; EnterWorktree actually re-roots the session.

### Fixed

- Cancellation mid-turn no longer leaves dangling tool_use blocks that break
  every subsequent API call.
- Auto-compact works under prompt caching (cache tokens fold into effective
  input) and on small context windows (threshold can no longer go negative).
- Every protocol guarantees a StreamEnd event; truncation is detected and
  surfaced instead of silently producing empty tool arguments.
- Session JSONL poisoned by a crash between tool_use/tool_result is repaired
  on resume instead of dropping all later records.
- Thinking blocks survive session persistence and replay before tool_use
  turns (Anthropic ordering requirement).
- SharedTaskStore writes are atomic and cross-process locked; corrupt files
  are quarantined, not fatal.
- Streaming refuses conversation-mutating commands (`/clear`, `/compact`,
  `/session resume`) with user feedback; prompt commands fired mid-stream are
  reported instead of silently dropped.
- Config merging is presence-based, so project-level config can reset keys to
  defaults again.
- Teammate spawn failures roll back member registration and worktrees;
  deleting a team preserves dirty worktrees.
- Aggregate tool-result budget counts the current message itself.
- Context-overflow classifier no longer mistakes rate-limit errors for
  overflow (which silently truncated summary input).
- Glob/Grep respect `.gitignore` and skip hidden directories.
- git/tmux subprocess calls run off the event loop (`asyncio.to_thread`).
- TaskManager/TraceManager retention is bounded; malformed tool calls missing
  IDs no longer abort the run and discard valid sibling calls.

### Changed

- CI runs on push/PR only (weekly schedule removed).
- README claims match reality (15 built-in tools); internal QA report and
  personal interview-prep skill removed from the repository.

### Added

- PyPI packaging metadata (readme, license, classifiers, `py.typed`),
  `__version__`, and this changelog.
- Integration test marker wired to live-API tests (self-skipping without API
  keys).

## [0.1.0]

Initial public release: multi-protocol streaming client (Anthropic Messages,
OpenAI Responses, OpenAI-compatible Chat Completions, DeepSeek), Textual TUI,
five-tier permission pipeline, two-layer context compression, sub-agents and
Agent Teams with worktree isolation, MCP support, skills, memory extraction,
and hooks.
