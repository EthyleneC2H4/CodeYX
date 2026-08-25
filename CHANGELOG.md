# Changelog

All notable changes to CodeYX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [0.2.0] - 2026-08-25

Security and reliability hardening release: a full-project audit plus two
adversarial final-review passes surfaced ~84 confirmed defects; all are fixed
with regression tests (suite grew from ~608 to 792 tests).

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

Post-audit adversarial final review (two fresh-perspective passes over the
whole cumulative diff, plus an adversarial verification pass over the review
fixes themselves — ~42 additional confirmed defects fixed with 65 new
regression tests). The verification pass caught 8 real defects in the first
two rounds' own fixes; all were repaired:

### Security

- Bare `&` is treated as a command separator in both the Layer-1 safe-command
  allowlist and the dangerous-command segmenter — `cat README.md & rm -rf ~`
  can no longer smuggle a backgrounded payload past either check.
- Tool-call arguments that fail JSON parsing are flagged (`parse_error`) and
  quarantined like ID-less calls across all three protocols: never executed,
  reported to the model as a synthetic error instead.
- Paths containing NUL bytes are denied by the sandbox (both in the final
  filename and in not-yet-existing ancestors) instead of crashing mid-check.
- Foreground sub-agents can no longer reach session-root tools
  (`SessionSpawn`, notification polling) via tool resolution.
- Teammate spawn-cancel rollback now waits (bounded) for the un-cancellable
  tmux/iTerm2 spawner thread, kills any pane it already created, and only
  then removes registration and worktree — previously cancellation could
  delete the worktree under a live spawn thread and orphan a token-consuming
  pane that `delete_team` could never see.

### Fixed

- The OpenAI Responses client now handles the real terminal events:
  `response.incomplete` maps `max_output_tokens` to the max_tokens recovery
  path and other reasons pass through; `response.failed` terminates the
  stream with usage. Truncation is no longer silently undetectable.
- Compat/DeepSeek clients surface unknown finish reasons (e.g.
  `content_filter`) verbatim instead of masquerading as `end_turn`.
- A cancelled turn whose task dies before its first step releases the turn
  latch via a done-callback watcher; an older cancelled task cannot clear a
  newer turn's claim (ownership guard).
- Ctrl+Q during a streaming answer cancels and joins the live agent turn
  before memory extraction / session close / manager teardown.
- TaskManager drains completion notifications before reaping aged entries,
  so a completion that waited out its retention window is still delivered.
- Cancellation during teammate spawn fully rolls back: trace marked,
  registration and name-registry entries removed, worktree cleaned up
  (explicit CancelledError branches — it is a BaseException).
- ExitWorktree restores the host root BEFORE removing the worktree; if the
  switch-back fails on `remove`, removal is refused and the tree is kept.
- `git worktree remove` runs with `cwd=worktree_path`, so teammate-worktree
  cleanup works regardless of the process working directory.
- SharedTaskStore mirrors an externally deleted store file instead of
  resurrecting stale tasks from memory.
- `run_to_completion` preserves signed thinking blocks on every assistant
  message (extended-thinking replay invariant) and never executes tool calls
  truncated by the max_tokens limit — including after escalation is
  exhausted, where it now stops with an explicit error.
- Auto-compaction resets the stale pre-compact token reading, preventing an
  immediate second summarization of the fresh summary.
- Session resume resets the history cursor on CompactNotification, so replay
  slices stay consistent (no empty/half turns, no orphan tool_result).
- The gitignore engine was rewritten chunk-based: `**` matches root-level
  paths, ancestor rules resolve first with last-match-wins so final-segment
  negations win, matching real git semantics. The verification pass repaired
  two regressions in that rewrite: trailing `**` patterns (`build/**`, the
  most common ignore idiom) match everything beneath again instead of never
  matching, and `[...]` character classes (`*.py[cod]`) work like fnmatch.
- Compat/DeepSeek folded-usage handling only terminates the stream on a
  chunk with a terminal finish reason — gateways reporting cumulative usage
  on every chunk can no longer end the stream at chunk 1 and mask a later
  `length` truncation.
- `run_to_completion` reports the max_tokens truncation note even when every
  tool call in the truncated response was quarantined (the most-truncated
  case previously returned bare cut-off prose).
- `/worktree exit --remove` refuses on a dirty tree BEFORE re-rooting the
  host (the tool path already did); `/worktree create` is now covered by the
  same one-live-session guard as `enter`, so it cannot strand an active
  session's bookkeeping.

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
