from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeyx.conversation import ConversationManager, Message
from codeyx.skills.parser import parse_frontmatter

if TYPE_CHECKING:
    from codeyx.client import LLMClient

USER_MEMORIES_RELPATH = ".codeyx/memories.md"
PROJECT_MEMORIES_RELPATH = ".codeyx/memories.md"
USER_MEMORY_DIR_RELPATH = ".codeyx/memory"
PROJECT_MEMORY_DIR_RELPATH = ".codeyx/memory"
MEMORY_INDEX_FILENAME = "MEMORY.md"
MEMORY_INDEX_MAX_LINES = 200
MEMORY_INDEX_MAX_BYTES = 16_384
MEMORY_ENTRY_MAX_BYTES = 24_576

MEMORY_EXTRACTION_PROMPT = """\
你是一个记忆提取助手。分析下面的对话，提取值得长期记忆的信息，更新 memories.md。

分类规则：
- **用户偏好**：用户的编码习惯和风格要求（如缩进、命名规范、语言偏好）
- **纠正反馈**：用户明确指出的错误和正确做法
- **项目知识**：当前项目的具体技术信息（技术栈、目录结构、部署方式）
- **参考资料**：外部链接和文档地址

规则：
1. 已有相同含义的条目不要重复添加
2. 没有值得记忆的内容，该分类下留空（不要写任何条目，不要写占位符）
3. 每条记忆用一行 `- ` 开头，必须是具体内容，不要用 `...` 占位
4. 输出完整的 memories.md 内容，包含所有四个分类标题

输出格式（严格遵守，没有内容的分类下不写任何条目）：
### 用户偏好
- 用户偏好简洁代码风格

### 纠正反馈

### 项目知识
- 项目使用 PostgreSQL 15

### 参考资料

不要输出任何其他内容，不要调用任何工具。"""

_USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
_PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}

_SECTION_FILES = {
    "用户偏好": ("user_preferences.md", "user", "用户编码习惯、交互偏好和输出风格偏好"),
    "纠正反馈": ("correction_feedback.md", "feedback", "用户明确纠正过的错误和正确做法"),
    "项目知识": ("project_knowledge.md", "project", "当前项目长期有效的工程事实"),
    "参考资料": ("references.md", "reference", "用户提供或项目相关的外部资料"),
}


@dataclass
class MemoryEntry:
    path: Path
    name: str
    type: str
    description: str
    updated_at: str
    confidence: str
    body: str


@dataclass(frozen=True)
class MemorySearchResult:
    name: str
    type: str
    description: str
    path: Path
    score: int
    excerpt: str


class MemoryManager:
    def __init__(self, project_root: str) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = Path(project_root) / PROJECT_MEMORIES_RELPATH
        self._user_dir = Path.home() / USER_MEMORY_DIR_RELPATH
        self._project_dir = Path(project_root) / PROJECT_MEMORY_DIR_RELPATH
        self._last_extraction_msg_count = 0


    @property
    def user_path(self) -> Path:
        return self._user_path


    @property
    def project_path(self) -> Path:
        return self._project_path


    @property
    def user_dir(self) -> Path:
        return self._user_dir


    @property
    def project_dir(self) -> Path:
        return self._project_dir

    def load(self) -> str:
        sections: list[str] = []

        user_dir_content = self._load_memory_directory(self._user_dir, "用户级")
        if user_dir_content:
            sections.append(user_dir_content)
        elif self._user_path.exists():
            content = self._read_limited_text(self._user_path, MEMORY_ENTRY_MAX_BYTES).strip()
            if content:
                sections.append(content)

        project_dir_content = self._load_memory_directory(self._project_dir, "项目级")
        if project_dir_content:
            sections.append(project_dir_content)
        elif self._project_path.exists():
            content = self._read_limited_text(self._project_path, MEMORY_ENTRY_MAX_BYTES).strip()
            if content:
                sections.append(content)

        return "\n\n".join(sections)

    async def extract(
        self,
        client: LLMClient,
        conversation: ConversationManager,
        protocol: str,
    ) -> None:
        from codeyx.tools.base import StreamEnd, TextDelta

        current_memories = self.load()

        recent = conversation.history[self._last_extraction_msg_count :]
        if not recent:
            return

        conv_lines: list[str] = []
        for msg in recent:
            if msg.role == "user" and msg.content:
                conv_lines.append(f"用户: {msg.content}")
            elif msg.role == "assistant" and msg.content:
                conv_lines.append(f"助手: {msg.content}")

        if not conv_lines:
            return

        prompt = (
            f"{MEMORY_EXTRACTION_PROMPT}\n\n"
            f"## 当前 memories.md\n"
            f"{current_memories if current_memories else '(空)'}\n\n"
            f"## 最近对话\n"
            f"{chr(10).join(conv_lines)}\n\n"
            f"请输出更新后的完整 memories.md 内容。"
        )

        extract_conv = ConversationManager()
        extract_conv.history = [Message(role="user", content=prompt)]

        collected = ""
        try:
            async for event in client.stream(
                extract_conv, system="你是一个记忆提取助手。"
            ):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
        except Exception:
            return

        self._last_extraction_msg_count = len(conversation.history)

        collected = collected.strip()
        if not collected:
            return

        self._write_memories(collected)

    def _write_memories(self, content: str) -> None:
        user_sections: list[str] = []
        project_sections: list[str] = []

        current_header = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if line.startswith("### "):
                if current_header:
                    self._assign_section(
                        current_header, current_lines, user_sections, project_sections
                    )
                current_header = line
                current_lines = []
            else:
                current_lines.append(line)

        if current_header:
            self._assign_section(
                current_header, current_lines, user_sections, project_sections
            )

        if user_sections:
            self._user_path.parent.mkdir(parents=True, exist_ok=True)
            self._user_path.write_text(
                "\n".join(user_sections).strip() + "\n", encoding="utf-8"
            )
            self._write_memory_directory(self._user_dir, user_sections)

        if project_sections:
            self._project_path.parent.mkdir(parents=True, exist_ok=True)
            self._project_path.write_text(
                "\n".join(project_sections).strip() + "\n", encoding="utf-8"
            )
            self._write_memory_directory(self._project_dir, project_sections)


    @staticmethod
    def _read_limited_text(path: Path, max_bytes: int) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        truncated = len(data) > max_bytes
        raw = data[:max_bytes].decode("utf-8", errors="replace")
        if truncated:
            raw += "\n\n[Memory truncated: file exceeds configured byte limit]\n"
        return raw


    @staticmethod
    def _limit_index_text(text: str) -> str:
        encoded = text.encode("utf-8")
        truncated_by_bytes = len(encoded) > MEMORY_INDEX_MAX_BYTES
        if truncated_by_bytes:
            text = encoded[:MEMORY_INDEX_MAX_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()
        truncated_by_lines = len(lines) > MEMORY_INDEX_MAX_LINES
        if truncated_by_lines:
            lines = lines[:MEMORY_INDEX_MAX_LINES]
            text = "\n".join(lines)
        if truncated_by_bytes or truncated_by_lines:
            text += "\n\n[Memory index truncated: load limit reached]"
        return text.strip()


    @staticmethod
    def _parse_memory_entry(path: Path) -> MemoryEntry | None:
        raw = MemoryManager._read_limited_text(path, MEMORY_ENTRY_MAX_BYTES).strip()
        if not raw:
            return None
        try:
            meta, body = parse_frontmatter(raw)
        except Exception:
            meta, body = {}, raw
        return MemoryEntry(
            path=path,
            name=str(meta.get("name") or path.stem),
            type=str(meta.get("type") or "reference"),
            description=str(meta.get("description") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            confidence=str(meta.get("confidence") or "medium"),
            body=body.strip(),
        )


    def _load_memory_directory(self, path: Path, label: str) -> str:
        if not path.is_dir():
            return ""

        chunks: list[str] = []
        index = path / MEMORY_INDEX_FILENAME
        if index.is_file():
            content = self._limit_index_text(
                self._read_limited_text(index, MEMORY_INDEX_MAX_BYTES * 2)
            )
            if content:
                chunks.append(f"[{label} Memory Index] {index}\n{content}")

        entries: list[MemoryEntry] = []
        for entry_path in sorted(path.glob("*.md")):
            if entry_path.name == MEMORY_INDEX_FILENAME:
                continue
            entry = self._parse_memory_entry(entry_path)
            if entry is not None and entry.body:
                entries.append(entry)

        for entry in entries:
            header = (
                f"[{label} Memory Entry] {entry.name}"
                f" ({entry.type}, confidence={entry.confidence})"
            )
            details = []
            if entry.description:
                details.append(f"description: {entry.description}")
            if entry.updated_at:
                details.append(f"updated_at: {entry.updated_at}")
            detail_text = "\n".join(details)
            if detail_text:
                chunks.append(f"{header}\n{detail_text}\n{entry.body}")
            else:
                chunks.append(f"{header}\n{entry.body}")

        return "\n\n".join(chunks)


    def catalog(self) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for directory in (self._user_dir, self._project_dir):
            if not directory.is_dir():
                continue
            for entry_path in sorted(directory.glob("*.md")):
                if entry_path.name == MEMORY_INDEX_FILENAME:
                    continue
                entry = self._parse_memory_entry(entry_path)
                if entry is not None:
                    entries.append(entry)
        return entries


    def search(self, query: str, limit: int = 5) -> list[MemorySearchResult]:
        normalized = query.strip().lower()
        if not normalized:
            return []
        terms = [t for t in normalized.replace("_", "-").split() if t]
        results: list[MemorySearchResult] = []
        for entry in self.catalog():
            haystack = "\n".join([
                entry.name,
                entry.type,
                entry.description,
                entry.body,
            ]).lower()
            score = 0
            if normalized == entry.name.lower():
                score += 100
            elif normalized in entry.name.lower():
                score += 50
            if normalized in entry.description.lower():
                score += 35
            if normalized in entry.body.lower():
                score += 25
            score += sum(8 for term in terms if term in haystack)
            if score <= 0:
                continue
            excerpt = self._make_excerpt(entry.body, terms or [normalized])
            results.append(MemorySearchResult(
                name=entry.name,
                type=entry.type,
                description=entry.description,
                path=entry.path,
                score=score,
                excerpt=excerpt,
            ))
        results.sort(key=lambda r: (-r.score, r.name))
        return results[:max(0, limit)]


    @staticmethod
    def _make_excerpt(text: str, terms: list[str], max_chars: int = 180) -> str:
        clean = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if not clean:
            return ""
        lower = clean.lower()
        first_hit = -1
        for term in terms:
            idx = lower.find(term.lower())
            if idx != -1 and (first_hit == -1 or idx < first_hit):
                first_hit = idx
        if first_hit == -1:
            return clean[:max_chars]
        start = max(0, first_hit - max_chars // 3)
        excerpt = clean[start:start + max_chars]
        if start > 0:
            excerpt = "..." + excerpt
        if start + max_chars < len(clean):
            excerpt += "..."
        return excerpt


    @staticmethod
    def _write_memory_directory(directory: Path, sections: list[str]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).date().isoformat()
        index_lines = ["# Memory Index", ""]

        for section in sections:
            lines = section.splitlines()
            if not lines:
                continue
            header = lines[0].removeprefix("### ").strip()
            if header not in _SECTION_FILES:
                continue
            filename, memory_type, description = _SECTION_FILES[header]
            body = "\n".join(lines).strip() + "\n"
            file_path = directory / filename
            name = filename.removesuffix(".md").replace("_", "-")
            frontmatter = (
                "---\n"
                f"name: {name}\n"
                f"type: {memory_type}\n"
                f"description: {description}\n"
                f"updated_at: {now}\n"
                "confidence: medium\n"
                "---\n\n"
            )
            file_path.write_text(frontmatter + body, encoding="utf-8")
            index_lines.append(f"- [{header}](./{filename}) - {description}")

        index_text = "\n".join(index_lines).strip() + "\n"
        (directory / MEMORY_INDEX_FILENAME).write_text(index_text, encoding="utf-8")

    @staticmethod
    def _is_placeholder(line: str) -> bool:
        stripped = line.strip().lstrip("- ").strip()
        return stripped in {"", "...", "…", "无", "暂无", "N/A"}


    @staticmethod
    def _assign_section(
        header: str,
        lines: list[str],
        user_sections: list[str],
        project_sections: list[str],
    ) -> None:
        real_lines = [l for l in lines if l.strip().startswith("- ") and not MemoryManager._is_placeholder(l)]
        if not real_lines:
            return

        section_text = header + "\n" + "\n".join(real_lines)

        for keyword in _USER_LEVEL_HEADERS:
            if keyword in header:
                user_sections.append(section_text)
                return

        for keyword in _PROJECT_LEVEL_HEADERS:
            if keyword in header:
                project_sections.append(section_text)
                return


    def clear(self) -> None:
        if self._user_path.exists():
            self._user_path.write_text("", encoding="utf-8")
        if self._project_path.exists():
            self._project_path.write_text("", encoding="utf-8")
        for directory in (self._user_dir, self._project_dir):
            if directory.exists():
                for path in directory.glob("*.md"):
                    path.unlink(missing_ok=True)

    def get_display_text(self) -> str:
        parts: list[str] = []

        user_dir_content = self._load_memory_directory(self._user_dir, "用户级")
        if user_dir_content:
            parts.append(f"[用户级目录] {self._user_dir}\n{user_dir_content}")
        if self._user_path.exists():
            content = self._read_limited_text(self._user_path, MEMORY_ENTRY_MAX_BYTES).strip()
            if content:
                parts.append(f"[用户级] {self._user_path}\n{content}")

        project_dir_content = self._load_memory_directory(self._project_dir, "项目级")
        if project_dir_content:
            parts.append(f"[项目级目录] {self._project_dir}\n{project_dir_content}")
        if self._project_path.exists():
            content = self._read_limited_text(self._project_path, MEMORY_ENTRY_MAX_BYTES).strip()
            if content:
                parts.append(f"[项目级] {self._project_path}\n{content}")

        if not parts:
            return "当前没有任何自动记忆。"

        return "\n\n".join(parts)
