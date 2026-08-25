from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .validator import (
    DEFAULT_CONTEXT_WINDOW,
    ConfigError,
    validate_config_structure,
)

log = logging.getLogger(__name__)

_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-compat": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass
class ProviderConfig:
    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str = ""
    thinking: bool = False
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = 0

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_var = _ENV_KEY_MAP.get(self.protocol, "")
        return os.environ.get(env_var, "")


    def get_max_output_tokens(self) -> int:
        if self.max_output_tokens > 0:
            return self.max_output_tokens
        if self.thinking:
            return 64000
        return 8192


def resolve_env_vars(value: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        result = os.environ.get(var_name)
        if result is None:
            log.warning("环境变量 ${%s} 未设置，使用原始值", var_name)
            return m.group(0)
        return result
    return _ENV_VAR_RE.sub(_replace, value)


def build_child_env(declared_env: dict[str, str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    path = os.environ.get("PATH", "")
    if path:
        env["PATH"] = path
    for key, value in (declared_env or {}).items():
        env[key] = resolve_env_vars(value)
    return env


@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


    @property
    def is_stdio(self) -> bool:
        return self.command is not None


@dataclass
class WorktreeConfig:
    symlink_directories: list[str] = field(default_factory=lambda: ["node_modules", ".venv", "vendor"])
    stale_cleanup_interval: int = 3600
    stale_cutoff_hours: int = 24


@dataclass
class AppConfig:
    providers: list[ProviderConfig]
    permission_mode: str = "default"
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    raw_hooks: list[dict] = field(default_factory=list)
    enable_fork: bool = False
    enable_verification_agent: bool = False
    worktree: WorktreeConfig = field(default_factory=WorktreeConfig)
    teammate_mode: str = ""
    enable_coordinator_mode: bool = False
    # Top-level (and worktree-level) keys actually present in the source
    # file. Replaces the old "differs from default" sentinel so an upper
    # layer can explicitly set a field BACK to its default value — e.g. a
    # project overriding a user-level `permission_mode: acceptEdits` with
    # `permission_mode: default` used to be silently ignored.
    explicit: set[str] = field(default_factory=set)
    worktree_explicit: set[str] = field(default_factory=set)


def _load_single_file(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse config {path}: {e}") from e

    # Layers may override only a few keys; the merged result must still
    # carry providers (checked in load_config).
    validated = validate_config_structure(raw, require_providers=False)

    providers = [
        ProviderConfig(
            name=p["name"],
            protocol=p["protocol"],
            base_url=resolve_env_vars(p["base_url"]),
            model=p["model"],
            # Support `api_key: "${ANTHROPIC_API_KEY}"` alongside the implicit
            # per-protocol env lookup in resolve_api_key().
            api_key=resolve_env_vars(p["api_key"]),
            thinking=p["thinking"],
            context_window=p["context_window"],
            max_output_tokens=p["max_output_tokens"],
        )
        for p in validated["providers"]
    ]

    mcp_servers = [
        MCPServerConfig(
            name=s["name"],
            command=s["command"],
            args=s["args"],
            url=s["url"],
            headers=s["headers"],
            env=s["env"],
        )
        for s in validated["mcp_servers"]
    ]

    wt = validated["worktree"]
    worktree_cfg = WorktreeConfig(
        symlink_directories=wt["symlink_directories"],
        stale_cleanup_interval=wt["stale_cleanup_interval"],
        stale_cutoff_hours=wt["stale_cutoff_hours"],
    )

    cfg = AppConfig(
        providers=providers,
        permission_mode=validated["permission_mode"],
        mcp_servers=mcp_servers,
        raw_hooks=validated["hooks"],
        enable_fork=validated["enable_fork"],
        enable_verification_agent=validated["enable_verification_agent"],
        worktree=worktree_cfg,
        teammate_mode=validated["teammate_mode"],
        enable_coordinator_mode=validated["enable_coordinator_mode"],
    )
    raw_keys = set(raw.keys()) if isinstance(raw, dict) else set()
    wt_raw = raw.get("worktree") if isinstance(raw, dict) else None
    cfg.explicit = raw_keys
    cfg.worktree_explicit = set(wt_raw.keys()) if isinstance(wt_raw, dict) else set()
    return cfg


def _merge_config(base: AppConfig, override: AppConfig) -> AppConfig:
    # Presence-based merge: a key counts only when the overriding file
    # actually wrote it, so an upper layer can reset a field to its default.
    if "providers" in override.explicit and override.providers:
        base.providers = override.providers
    if "permission_mode" in override.explicit:
        base.permission_mode = override.permission_mode

    if override.mcp_servers:
        by_name = {s.name: i for i, s in enumerate(base.mcp_servers)}
        for s in override.mcp_servers:
            if s.name in by_name:
                base.mcp_servers[by_name[s.name]] = s
            else:
                base.mcp_servers.append(s)
                by_name[s.name] = len(base.mcp_servers) - 1

    base.raw_hooks.extend(override.raw_hooks)
    if "enable_fork" in override.explicit:
        base.enable_fork = override.enable_fork
    if "enable_verification_agent" in override.explicit:
        base.enable_verification_agent = override.enable_verification_agent
    if "teammate_mode" in override.explicit:
        base.teammate_mode = override.teammate_mode
    if "enable_coordinator_mode" in override.explicit:
        base.enable_coordinator_mode = override.enable_coordinator_mode

    for key in (
        "symlink_directories",
        "stale_cleanup_interval",
        "stale_cutoff_hours",
    ):
        if key in override.worktree_explicit:
            setattr(base.worktree, key, getattr(override.worktree, key))
    return base


def load_config(path: Path | None = None) -> AppConfig:
    if path is not None:
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        return _load_single_file(path)

    cwd = Path.cwd()
    home = Path.home()
    candidates = [
        home / ".codeyx" / "config.yaml",
        cwd / ".codeyx" / "config.yaml",
        cwd / ".codeyx" / "config.local.yaml",
    ]

    merged: AppConfig | None = None
    for p in candidates:
        if not p.exists():
            continue
        layer = _load_single_file(p)
        if merged is None:
            merged = layer
        else:
            merged = _merge_config(merged, layer)

    if merged is None:
        raise ConfigError(
            "No config file found. Expected .codeyx/config.yaml "
            "in project or ~/.codeyx/config.yaml"
        )
    if not merged.providers:
        raise ConfigError("Config must contain a 'providers' list")
    return merged
