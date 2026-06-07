
from __future__ import annotations

import re

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$", re.IGNORECASE), "递归强制删除根目录"),
    (re.compile(r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/\s*$", re.IGNORECASE), "递归强制删除根目录"),
    (re.compile(r"mkfs\.", re.IGNORECASE), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/", re.IGNORECASE), "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/", re.IGNORECASE), "递归修改根目录权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh", re.IGNORECASE), "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh", re.IGNORECASE), "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
    # 提权命令
    (re.compile(r"\bsudo\s+rm\s", re.IGNORECASE), "sudo 删除操作"),
    (re.compile(r"\bsu\s+-c\s", re.IGNORECASE), "su 切换用户执行"),
    # 间接执行
    (re.compile(r"\beval\s+", re.IGNORECASE), "eval 间接执行"),
    # 系统关键路径重定向
    (re.compile(r">\s*/etc/"), "覆盖系统配置文件"),
    (re.compile(r">\s*/boot/"), "覆盖启动文件"),
]


_SAFE_COMMANDS = frozenset({
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "wc",
    "find", "which", "whereis", "whoami", "hostname", "uname",
    "date", "cal", "uptime", "df", "du", "free", "env", "printenv",
    "file", "stat", "readlink", "realpath", "basename", "dirname",
    "sort", "uniq", "tr", "cut", "awk", "sed", "grep", "egrep", "fgrep",
    "diff", "comm", "tee", "xargs", "true", "false", "test",
    "git status", "git log", "git diff", "git show", "git branch",
    "git tag", "git remote", "git rev-parse", "git ls-files",
    "git blame", "git stash list", "go version", "go env",
    "node -v", "npm -v", "npx", "python --version", "pip list",
    "cargo --version", "rustc --version", "java -version", "java --version",
})


def is_safe_command(command: str) -> bool:
    trimmed = command.strip()
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
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
