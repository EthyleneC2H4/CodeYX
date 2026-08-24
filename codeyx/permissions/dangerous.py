
from __future__ import annotations

import re
import shlex

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Recursive-force deletion of / is detected by _detect_rm_variants()
    # below at the token level: regex enumeration cannot cover arbitrary
    # flag orders and mixed short/long spellings.
    (re.compile(r"--no-preserve-root", re.IGNORECASE), "绕过根目录保护"),
    (re.compile(r"mkfs\.", re.IGNORECASE), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/", re.IGNORECASE), "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/", re.IGNORECASE), "递归修改根目录权限"),
    (re.compile(r"chmod\s+777\s+/(etc|boot|bin|sbin|usr|var)/(passwd|shadow|sudoers|hosts|grub|.*)", re.IGNORECASE), "放宽系统关键文件权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.IGNORECASE), "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.IGNORECASE), "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
    # 提权命令
    (re.compile(r"\bsudo\s+rm\s", re.IGNORECASE), "sudo 删除操作"),
    (re.compile(r"\bsu\s+-c\s", re.IGNORECASE), "su 切换用户执行"),
    # 间接执行
    (re.compile(r"\beval\s+", re.IGNORECASE), "eval 间接执行"),
    (re.compile(r"\b(python|python3|perl|ruby|node)\s+-(c|e)\s+", re.IGNORECASE), "解释器间接执行"),
    (re.compile(r"\b(ba)?sh\s+-c\s+", re.IGNORECASE), "shell -c 间接执行"),
    # 系统关键路径重定向
    (re.compile(r">\s*/etc/"), "覆盖系统配置文件"),
    (re.compile(r">\s*/boot/"), "覆盖启动文件"),
]


# Strictly read-only commands. Anything that can write, delete, or execute
# (find -delete/-exec, xargs, sed -i, awk system(), tee, npx, …) must NOT be
# listed here — those fall through to the mode matrix / user confirmation.
_SAFE_COMMANDS = frozenset({
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "wc",
    "which", "whereis", "whoami", "hostname", "uname",
    "date", "cal", "uptime", "df", "du", "free", "env", "printenv",
    "file", "stat", "readlink", "realpath", "basename", "dirname",
    "sort", "uniq", "tr", "cut", "grep", "egrep", "fgrep",
    "diff", "comm", "true", "false", "test",
    "git status", "git log", "git diff", "git show", "git branch",
    "git tag", "git remote", "git rev-parse", "git ls-files",
    "git blame", "git stash list", "go version", "go env",
    "node -v", "npm -v", "python --version", "pip list",
    "cargo --version", "rustc --version", "java -version", "java --version",
})


def _normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip())


def _extract_wrapped_payload(command: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) < 3:
        return None

    executable = tokens[0].lower()
    flag = tokens[1].lower()
    if executable in {"bash", "sh"} and flag == "-c":
        return tokens[2]
    if executable in {"python", "python3", "perl", "ruby", "node"} and flag in {"-c", "-e"}:
        return " ".join(tokens[2:])
    if executable == "su":
        lowered = [t.lower() for t in tokens]
        if "-c" in lowered:
            idx = lowered.index("-c")
            if idx + 1 < len(tokens):
                return tokens[idx + 1]
    return None


_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\|")


def _detect_rm_variants(command: str) -> str:
    """Token-level detection of recursive deletion aimed at the filesystem
    root. Flags may appear in any order, combined or separate, short or
    long ("rm -rf /", "rm --force --recursive /", "rm -r -f /*", …).
    Chained commands are checked segment-by-segment."""
    for segment in _SEGMENT_SPLIT_RE.split(command):
        reason = _detect_rm_segment(segment.strip())
        if reason:
            return reason
    return ""


def _detect_rm_segment(segment: str) -> str:
    if not segment:
        return ""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    if not tokens or tokens[0].lower() != "rm":
        return ""

    recursive = False
    force = False
    saw_double_dash = False
    root_target = False
    for tok in tokens[1:]:
        if tok == "--" and not saw_double_dash:
            saw_double_dash = True
            continue
        if not saw_double_dash and tok.startswith("--") and len(tok) > 2:
            name = tok[2:].lower()
            if name in ("recursive", "r"):
                recursive = True
            elif name in ("force", "f"):
                force = True
            # --no-preserve-root is caught by its own pattern above.
            continue
        if not saw_double_dash and tok.startswith("-") and len(tok) > 1:
            chars = set(tok[1:])
            if "r" in chars or "R" in chars:
                recursive = True
            if "f" in chars or "F" in chars:
                force = True
            continue
        # Operand. Root itself plus globs that expand to root's children.
        if tok in ("/", "//", "/*", "/*/*") or tok.startswith("/*/"):
            root_target = True

    if recursive and root_target:
        return "递归强制删除根目录"
    if recursive and force:
        # recursive+force outside root is not auto-denied; it falls through
        # to the mode matrix / user confirmation like other writes.
        return ""
    return ""


def is_safe_command(command: str) -> bool:
    # Newlines are shell command separators. Normalizing them away would let
    # "<safe-cmd>\n<arbitrary payload>" prefix-match an allowlisted entry, so
    # any multi-line command must go through the full pipeline instead.
    if "\n" in command or "\r" in command:
        return False
    trimmed = _normalize_command(command)
    if not trimmed:
        return False
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    for safe in _SAFE_COMMANDS:
        if trimmed == safe or trimmed.startswith(safe + " "):
            return True
    return False


class DangerousCommandDetector:


    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))


    def detect(self, command: str) -> tuple[bool, str]:
        command = _normalize_command(command)
        rm_reason = _detect_rm_variants(command)
        if rm_reason:
            return True, rm_reason
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        payload = _extract_wrapped_payload(command)
        if payload and payload != command:
            nested_hit, nested_reason = self.detect(payload)
            if nested_hit:
                return True, f"间接执行危险命令: {nested_reason}"
        return False, ""
