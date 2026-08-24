
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from codeyx.config import ConfigError, load_config
from codeyx.hooks import HookConfigError, HookEngine, load_hooks
from codeyx.permissions import PermissionMode


def main() -> None:
    parser = argparse.ArgumentParser(prog="codeyx", description="CodeYX AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Working directory for this session (used when spawning teammates in worktrees)",
    )
    parser.add_argument(
        "--team",
        default=None,
        help="Join the named agent team as a teammate (enables mailbox consumption)",
    )
    parser.add_argument(
        "--agent-name",
        default=None,
        help="Teammate name used with --team",
    )
    parser.add_argument(
        "-p", "--prompt",
        default=None,
        help="Initial prompt to run after startup",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # File logging only after the config dir is known to exist, and never let
    # a logging failure block startup.
    log_path = Path.cwd() / ".codeyx" / "debug.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(message)s",
            filename=str(log_path),
            filemode="w",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    mode_str = args.mode if args.mode else config.permission_mode
    permission_mode = PermissionMode(mode_str)

    try:
        hooks = load_hooks(config.raw_hooks)
    except HookConfigError as e:
        print(f"Hook config error: {e}", file=sys.stderr)
        sys.exit(1)


    hook_engine = HookEngine(hooks) if hooks else None

    from codeyx.app import CodeYXApp


    app = CodeYXApp(
        providers=config.providers,
        permission_mode=permission_mode,
        mcp_servers=config.mcp_servers,
        hook_engine=hook_engine,
        enable_fork=config.enable_fork,
        enable_verification_agent=config.enable_verification_agent,
        worktree_config=config.worktree,
        teammate_mode=config.teammate_mode,
        enable_coordinator_mode=config.enable_coordinator_mode,
        boot={
            "work_dir": args.work_dir,
            "team": args.team,
            "agent_name": args.agent_name,
            "prompt": args.prompt,
        },
    )
    app.run(inline=True, inline_no_clear=True)


if __name__ == "__main__":
    main()

