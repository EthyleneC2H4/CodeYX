from __future__ import annotations

import re
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml

Effect = Literal["allow", "deny"]

# Tool names may be MCP-qualified (mcp__server__tool-name.x), so allow dots,
# hyphens and double underscores beyond \w.
_RULE_RE = re.compile(r"^([\w.\-]+)\((.+)\)$", re.DOTALL)

RULES_CACHE_TTL_SECONDS = 5.0

_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}

# Arguments the sandbox must validate, per tool. Kept separate from
# _CONTENT_FIELDS because for search tools (Glob/Grep) the pattern is a
# match expression, not a filesystem path — feeding it to PathSandbox both
# denies legitimate patterns containing "/" and skips the real target.
_PATH_FIELDS: dict[str, tuple[str, ...]] = {
    "ReadFile": ("file_path",),
    "WriteFile": ("file_path",),
    "EditFile": ("file_path",),
    "Glob": ("path",),
    "Grep": ("path",),
}


@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect


    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)


def parse_rule(raw: str, effect: Effect) -> Rule:
    m = _RULE_RE.match(raw.strip())
    if not m:
        raise ValueError(f"无效的规则语法: {raw}")
    return Rule(tool_name=m.group(1), pattern=m.group(2), effect=effect)


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    return str(arguments.get(field, ""))


def extract_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """Return the filesystem paths a tool call touches, for sandbox checks."""
    fields = _PATH_FIELDS.get(tool_name)
    if not fields:
        return []
    paths = []
    for field in fields:
        value = arguments.get(field)
        if isinstance(value, str) and value.strip():
            paths.append(value)
    return paths


def _load_rules_file(path: Path) -> list[Rule]:
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_str = entry.get("rule", "")
        effect = entry.get("effect", "")
        if effect not in ("allow", "deny"):
            continue
        try:
            rules.append(parse_rule(rule_str, effect))
        except ValueError:
            continue
    return rules


class RuleEngine:


    def __init__(
        self,
        user_rules_path: Path | None = None,
        project_rules_path: Path | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path
        # mtime+size keyed cache with a short TTL: evaluate() runs on every
        # tool call, and re-stat'ing + YAML-parsing up to 3 files each time is
        # waste. Entries are re-read when mtime/size changes — or after
        # RULES_CACHE_TTL_SECONDS, because coarse-timestamp filesystems can
        # miss a same-size edit that lands within one timestamp tick.
        self._cache: dict[Path, tuple[float, int, float, list[Rule]]] = {}

    def _load_rules_cached(self, path: Path) -> list[Rule]:
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._cache.pop(path, None)
            return []
        now = time.monotonic()
        cached = self._cache.get(path)
        if (
            cached
            and (cached[0], cached[1]) == key
            and now - cached[2] < RULES_CACHE_TTL_SECONDS
        ):
            return cached[3]
        rules = _load_rules_file(path)
        self._cache[path] = (key[0], key[1], now, rules)
        return rules

    def _load_tiers(self) -> list[list[Rule]]:
        tiers: list[list[Rule]] = []
        for p in (self._user_path, self._project_path, self._local_path):
            tiers.append(self._load_rules_cached(p) if p else [])
        return tiers


    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        """Return the effect of the last matching rule across the tier
        order (user → project → local), or None when nothing matches."""
        matched_rule: Rule | None = None
        for rules in self._load_tiers():
            for rule in reversed(rules):
                if rule.matches(tool_name, content):
                    matched_rule = rule
                    break
            if matched_rule is not None:
                break
        return matched_rule.effect if matched_rule else None


    def match(self, tool_name: str, content: str) -> Rule | None:
        """The winning rule for this call, for diagnostics/audit display."""
        for rules in self._load_tiers():
            for rule in reversed(rules):
                if rule.matches(tool_name, content):
                    return rule
        return None


    def append_local_rule(self, rule: Rule) -> None:
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)
        existing.append(rule)
        entries = [{"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect} for r in existing]
        self._local_path.write_text(yaml.dump(entries, allow_unicode=True), encoding="utf-8")
        self._cache.pop(self._local_path, None)
