from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class MailboxMessage:
    id: str
    from_agent: str
    to_agent: str
    content: str
    summary: str = ""
    message_type: str = "text"  # text | shutdown_request | shutdown_response
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MailboxMessage:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Mailbox:
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _agent_dir(self, agent_id: str) -> Path:
        return self._base_dir / agent_id


    def write(self, agent_id: str, message: MailboxMessage) -> None:
        d = self._agent_dir(agent_id)
        d.mkdir(parents=True, exist_ok=True)
        filename = f"{message.timestamp:.6f}_{message.id}.json"
        final = d / filename
        payload = json.dumps(message.to_dict(), ensure_ascii=False)
        # Publish atomically: a consumer draining the mailbox while we write
        # must never observe a torn file. (In-place writes used to combine
        # with consumption into silent message destruction.)
        tmp = final.with_name(f".{final.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)

    def read(self, agent_id: str) -> list[MailboxMessage]:
        d = self._agent_dir(agent_id)
        if not d.exists():
            return []
        messages: list[MailboxMessage] = []
        for f in sorted(d.iterdir()):
            if f.suffix != ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                messages.append(MailboxMessage.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return messages

    def consume(self, agent_id: str) -> list[MailboxMessage]:
        d = self._agent_dir(agent_id)
        if not d.exists():
            return []

        # Phase 1: recover messages a crashed consumer left in the claimed
        # state. They were renamed away before parsing, so without this a
        # crash between claim and delete would lose them permanently.
        for stale in sorted(d.glob("*.json.consuming")):
            original = stale.with_name(stale.name[: -len(".consuming")])
            try:
                stale.rename(original)
            except OSError:
                pass  # another consumer restored it first; skip

        messages: list[MailboxMessage] = []
        for f in sorted(d.iterdir()):
            if f.suffix != ".json":
                continue
            # Phase 2: claim atomically so two consumers cannot both
            # read+delete the same file and double-deliver it.
            claimed = f.with_suffix(f.suffix + ".consuming")
            try:
                f.rename(claimed)
            except OSError:
                continue  # another consumer won the race
            try:
                raw = claimed.read_text(encoding="utf-8")
            except OSError:
                # Transient filesystem error: restore the original name so
                # the message is retried on the next drain instead of lost.
                try:
                    claimed.rename(f)
                except OSError:
                    pass
                continue
            try:
                data = json.loads(raw)
                messages.append(MailboxMessage.from_dict(data))
            except (json.JSONDecodeError, KeyError) as e:
                # Malformed content (writes are atomic now, so this is real
                # corruption): quarantine for inspection, never destroy.
                log.warning(
                    "Quarantining malformed mailbox message %s: %s", claimed.name, e
                )
                try:
                    claimed.rename(claimed.with_suffix(".corrupt"))
                except OSError:
                    pass
                continue
            # Delete only after a successful parse: crash-safety prefers
            # losing zero messages over the narrow double-delivery window.
            claimed.unlink(missing_ok=True)
        return messages

    def broadcast(
        self,
        team_members: list[str],
        message: MailboxMessage,
        exclude: str = "",
    ) -> None:
        for agent_id in team_members:
            if agent_id == exclude:
                continue
            self.write(agent_id, message)


    def cleanup(self, agent_id: str) -> None:
        d = self._agent_dir(agent_id)
        if d.exists():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()

    def cleanup_all(self) -> None:
        if not self._base_dir.exists():
            return
        for d in self._base_dir.iterdir():
            if d.is_dir():
                for f in d.iterdir():
                    f.unlink(missing_ok=True)
                d.rmdir()


def create_message(
    from_agent: str,
    to_agent: str,
    content: str,
    summary: str = "",
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        id=uuid.uuid4().hex[:12],
        from_agent=from_agent,
        to_agent=to_agent,
        content=content,
        summary=summary,
        message_type=message_type,
        timestamp=time.time(),
        metadata=metadata or {},
    )
