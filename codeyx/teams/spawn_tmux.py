from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class TmuxPaneInfo:
    pane_id: str
    session: str


class TmuxSpawnError(Exception):
    pass


def _run_tmux(*args: str) -> str:
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise TmuxSpawnError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def build_cli_command(
    team_name: str,
    teammate_name: str,
    worktree_path: str,
    prompt: str,
    agent_type: str = "",
    model: str = "",
    mailbox_dir: str = "",
) -> str:
    """Build the shell command for a teammate pane. Only uses flags that
    actually exist in codeyx/__main__.py; team identity travels via --team /
    --agent-name so the spawned session wires its mailbox."""
    del agent_type, model, mailbox_dir  # accepted for API compat; unused
    parts = [
        "codeyx",
        "--work-dir", _shquote(worktree_path),
        "--team", _shquote(team_name),
        "--agent-name", _shquote(teammate_name),
        "--prompt", _shquote(prompt),
    ]
    return " ".join(parts)


def _shquote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def spawn_tmux_teammate(
    team_name: str,
    teammate_name: str,
    worktree_path: str,
    prompt: str,
    agent_type: str = "",
    model: str = "",
    mailbox_dir: str = "",
) -> TmuxPaneInfo:
    window_name = f"{team_name}-{teammate_name}"

    try:
        pane_id = _run_tmux(
            "split-window",
            "-h",
            "-P",
            "-F", "#{pane_id}",
            "-t", f"{team_name}",
        )
    except TmuxSpawnError:
        try:
            _run_tmux("new-window", "-t", f"{team_name}", "-n", window_name, "-P", "-F", "#{pane_id}")
            pane_id = _run_tmux(
                "split-window",
                "-h",
                "-P",
                "-F", "#{pane_id}",
                "-t", f"{team_name}:{window_name}",
            )
        except TmuxSpawnError:
            _run_tmux("new-session", "-d", "-s", team_name, "-n", window_name)
            pane_id = _run_tmux(
                "list-panes",
                "-t", f"{team_name}:{window_name}",
                "-F", "#{pane_id}",
            ).split("\n")[0]

    cli_cmd = build_cli_command(
        team_name=team_name,
        teammate_name=teammate_name,
        worktree_path=worktree_path,
        prompt=prompt,
        agent_type=agent_type,
        model=model,
        mailbox_dir=mailbox_dir,
    )
    _run_tmux("send-keys", "-t", pane_id, cli_cmd, "Enter")

    log.info("Spawned tmux teammate %s in pane %s", teammate_name, pane_id)
    return TmuxPaneInfo(pane_id=pane_id, session=team_name)


def send_keys_to_pane(pane_id: str, keys: str = "") -> None:
    try:
        _run_tmux("send-keys", "-t", pane_id, keys, "Enter")
    except TmuxSpawnError:
        log.warning("Failed to send keys to tmux pane %s", pane_id)


def kill_pane(pane_id: str) -> None:
    try:
        _run_tmux("kill-pane", "-t", pane_id)
    except TmuxSpawnError:
        pass
