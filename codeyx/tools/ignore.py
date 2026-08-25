"""Minimal .gitignore support for Glob/Grep traversal.

Implements just enough of the gitignore spec to keep search tools out of
build artifacts, vendored trees and hidden directories: blank lines,
comments, trailing-/ directory-only patterns, leading-/ root anchoring,
basename patterns at any depth, last-match-wins negation, the
"excluded parent dir cannot be re-included" rule, and per-segment wildcard
semantics (`*` never crosses `/`). Nested .gitignore files are deliberately
out of scope — this is a relevance filter, not a VCS implementation.
"""

from __future__ import annotations

import re
from pathlib import Path

from codeyx.tools.base import SKIP_DIRS


class IgnoreRule:
    __slots__ = ("pattern", "negated", "dir_only", "regex")

    def __init__(self, pattern: str, negated: bool, dir_only: bool) -> None:
        self.pattern = pattern
        self.negated = negated
        self.dir_only = dir_only
        self.regex = re.compile(_gitignore_to_regex(pattern))

    def matches(self, rel_str: str) -> bool:
        return self.regex.match(rel_str) is not None


def _translate_segment(seg: str) -> str:
    """Translate one non-`**` path segment to a single-segment regex.

    `*` and `?` stay within the segment; `[...]` character classes are
    honored like fnmatch did (`[!...]` negates, an unmatched `[` stays
    literal); everything else is escaped verbatim.
    """
    buf: list[str] = []
    i = 0
    n = len(seg)
    while i < n:
        ch = seg[i]
        if ch == "*":
            buf.append(r"[^/]*")
            i += 1
        elif ch == "?":
            buf.append(r"[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            if j < n and seg[j] == "!":
                j += 1
            if j < n and seg[j] == "]":
                j += 1  # a ']' right after '[' or '[!' is literal (fnmatch)
            end = seg.find("]", j)
            if end == -1:
                buf.append(re.escape(ch))
                i += 1
            else:
                inner = seg[i + 1 : end].replace("\\", "\\\\")
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                elif inner.startswith("^"):
                    inner = "\\" + inner
                buf.append(f"[{inner}]")
                i = end + 1
        else:
            buf.append(re.escape(ch))
            i += 1
    return "".join(buf)


def _gitignore_to_regex(pattern: str) -> str:
    """Translate one gitignore pattern to an anchored regex.

    `*` and `?` stay within a single path segment (fnmatch's `*` would
    cross `/` and over-hide nested files); a literal `**` segment spans
    directories — leading/middle `**/` matches zero or more segments,
    so `**/gen.py` also hits a root-level gen.py exactly like git, and
    a trailing `**` matches everything beneath (`build/**` never matches
    paths outside build/). Patterns containing `/` anchor at the root;
    bare patterns match the basename at any depth.
    """
    segments = pattern.split("/")
    # Trailing '**': drop it and require a non-empty remainder instead —
    # emitting "(?:[^/]+/)*$" would demand the path END in '/', which no
    # real path does, silently disabling the most common ignore idiom.
    trailing_globstar = segments[-1] == "**"
    if trailing_globstar:
        segments = segments[:-1]

    parts: list[str] = []
    for seg in segments:
        if seg == "**":
            # Whole-segment wildcard emitted as zero-or-more "dir/"
            # groups so concatenation keeps the separators balanced.
            parts.append(r"(?:[^/]+/)*")
        else:
            parts.append(_translate_segment(seg) + "/")
    body = "".join(parts)

    if trailing_globstar:
        if "/" in pattern.strip("/"):
            return rf"^{body}.+$"
        # Bare '**': match every path.
        return r"(?:^|.*/).+$"
    if body.endswith("/"):
        body = body[:-1]
    if "/" in pattern.strip("/"):
        return rf"^{body}$"
    # Basename pattern: match as the last segment at any depth.
    return rf"(?:^|.*/){body}$"


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

        Ancestor directory prefixes are resolved first (last match wins
        per prefix): git never descends into an excluded directory, so an
        excluded ancestor makes everything beneath it ignored — no later
        negation, even one naming the file itself, can re-include from
        below.
        """
        last_depth = len(rel_parts)
        for depth in range(1, last_depth):
            rel_str = "/".join(rel_parts[:depth])
            excluded = False
            for rule in self._rules:
                # Ancestors are directories, so dir-only rules apply too.
                if rule.matches(rel_str):
                    excluded = not rule.negated
            if excluded:
                return True

        rel_str = "/".join(rel_parts)
        ignored = False
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.matches(rel_str):
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
