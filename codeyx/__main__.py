
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from codeyx.config import ConfigError, load_config
from codeyx.hooks import HookConfigError, HookEngine, load_hooks
from codeyx.permissions import PermissionMode


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        filename=".codeyx/debug.log",
        filemode="w",
    )

    parser = argparse.ArgumentParser(prog="codeyx", description="CodeYX AI coding assistant")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in PermissionMode],
        default=None,
        help="Permission mode (overrides config.yaml)",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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
    )
    app.run(inline=True, inline_no_clear=True)


if __name__ == "__main__":
    main()

