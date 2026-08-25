"""Minimal .gitignore support for Glob/Grep traversal.

Implements just enough of the gitignore spec to keep search tools out of
build artifacts, vendored trees and hidden directories: blank lines,
comments, trailing-/ directory-only patterns, leading-/ root anchoring,
basename patterns at any depth, and last-match-wins negation. Nested
.gitignore files and advanced syntax (** bridging, character-class
anchors) are deliberately out of scope — this is a relevance filter,
not a VCS implementation.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from codeyx.tools.base import SKIP_DIRS


class IgnoreRule:
    __slots__ = ("pattern", "negated", "dir_only")

    def __init__(self, pattern: str, negated: bool, dir_only: bool) -> None:
        self.pattern = pattern
        self.negated = negated
        self.dir_only = dir_only


class IgnoreSpec:
    def __init__(self, rules: list[IgnoreRule]) -> None:
        self._rules = rules

    @classmethod
    def load(cls, base: Path) -> IgnoreSpec:
        rules: list[IgnoreRule] = []
        gitignore = base / ".gitignore"
        try:
            if gitignore.is_file():
                for raw in gitignore.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines():
                    rule = cls._parse_line(raw)
                    if rule is not None:
                        rules.append(rule)
        except OSError:
            pass
        return cls(rules)

    @staticmethod
    def _parse_line(raw: str) -> IgnoreRule | None:
        line = raw.strip()
        if not line or line.startswith("#"):
            return None
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        dir_only = line.endswith("/")
        if dir_only:
            line = line.rstrip("/")
        if line.startswith("/"):
            line = line.lstrip("/")
        if not line:
            return None
        return IgnoreRule(line, negated, dir_only)

    def is_ignored(self, rel_parts: tuple[str, ...], is_dir: bool = False) -> bool:
        """True when the given base-relative path should be skipped.

        Ancestor directories are checked too, so one matching `build/`
        rule ignores everything beneath build/ regardless of its own name.
        """
        ignored = False
        for depth in range(1, len(rel_parts) + 1):
            prefix = rel_parts[:depth]
            target_is_dir = depth < len(rel_parts) or is_dir
            rel_str = "/".join(prefix)
            for rule in self._rules:
                if rule.dir_only and not target_is_dir:
                    continue
                if "/" in rule.pattern:
                    hit = fnmatch.fnmatch(rel_str, rule.pattern)
                else:
                    hit = fnmatch.fnmatch(prefix[-1], rule.pattern)
                if hit:
                    ignored = not rule.negated
        return ignored


def build_path_filter(base: Path, pattern: str):
    """Return a predicate over absolute Paths: True = include.

    Filters out SKIP_DIRS, hidden directories below the base (unless the
    glob pattern names such a directory literally), and .gitignore hits.
    """
    spec = IgnoreSpec.load(base)
    # Literal (non-wildcard) segments of the glob pattern; a dot-segment
    # listed here was requested explicitly, e.g. '.github/**/*.yml'.
    literal_segments = {
        seg
        for seg in pattern.split("/")
        if seg and not any(ch in seg for ch in "*?[")
    }

    def include(p: Path) -> bool:
        try:
            rel = p.relative_to(base)
        except ValueError:
            return False
        parts = rel.parts
        if not parts:
            return False
        if any(part in SKIP_DIRS for part in parts):
            return False
        for part in parts[:-1]:
            if part.startswith(".") and part not in literal_segments:
                return False
        return not spec.is_ignored(parts, is_dir=False)

    return include
