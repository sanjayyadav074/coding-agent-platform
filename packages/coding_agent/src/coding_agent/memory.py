"""Per-user conversation memory backed by DynamoDB.

Privacy & isolation:
- Items are keyed solely by Slack ``user_id`` so a query can never return
  another user's data.
- TTL is set on every write so transcripts auto-expire (30 days by default).
- Server-side encryption + point-in-time-recovery are enabled at the table
  level (configured in Terraform).

The history is stored as a JSON-serialised list of ``{role, content}`` dicts
(role ∈ {"user", "assistant"}). We deliberately do NOT serialise pydantic-ai's
internal ``ModelMessage`` objects directly — keeping the wire format provider-
agnostic means we can swap LLMs without invalidating stored history.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

from .logging import get_logger

log = get_logger("memory")

# Hard cap on stored turns to keep prompt size bounded and DynamoDB items
# under the 400 KB limit. Older turns are evicted FIFO.
MAX_TURNS = 20

# 30-day TTL by default. DynamoDB TTL attribute must be a number (epoch sec).
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 30


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Turn:
        return cls(role=str(d["role"]), content=str(d["content"]))


class ConversationStore:
    """Thin async wrapper around a DynamoDB table.

    The table schema is::

        PK  user_id (String)
        --  history (String, JSON list of turns)
        --  updated_at (Number, epoch seconds)
        --  ttl (Number, epoch seconds — DynamoDB TTL attribute)
    """

    def __init__(self, table_name: str, region: str | None = None) -> None:
        self._table_name = table_name
        self._region = region
        self._session = aioboto3.Session()
        self._boto_config = BotoConfig(
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=3,
            read_timeout=5,
        )

    async def load(self, user_id: str) -> list[Turn]:
        if not user_id:
            return []
        async with self._session.resource(
            "dynamodb", region_name=self._region, config=self._boto_config
        ) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            resp = await table.get_item(
                Key={"user_id": user_id},
                ConsistentRead=False,
                ProjectionExpression="history",
            )
        item = resp.get("Item")
        if not item:
            return []
        try:
            raw = json.loads(item["history"])
        except (KeyError, ValueError, TypeError):
            log.warning("memory.load.corrupt", user_id=user_id)
            return []
        return [Turn.from_dict(t) for t in raw if isinstance(t, dict)]

    async def append(
        self,
        user_id: str,
        user_msg: str,
        assistant_msg: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if not user_id:
            return
        existing = await self.load(user_id)
        new_history = existing + [
            Turn("user", user_msg),
            Turn("assistant", assistant_msg),
        ]
        # FIFO trim to the most recent MAX_TURNS turns
        if len(new_history) > MAX_TURNS:
            new_history = new_history[-MAX_TURNS:]
        now = int(time.time())
        async with self._session.resource(
            "dynamodb", region_name=self._region, config=self._boto_config
        ) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            await table.put_item(
                Item={
                    "user_id": user_id,
                    "history": json.dumps([t.to_dict() for t in new_history]),
                    "updated_at": now,
                    "ttl": now + ttl_seconds,
                }
            )
        log.info("memory.append", user_id=user_id, turns=len(new_history))

    async def reset(self, user_id: str) -> None:
        if not user_id:
            return
        async with self._session.resource(
            "dynamodb", region_name=self._region, config=self._boto_config
        ) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            await table.delete_item(Key={"user_id": user_id})
        log.info("memory.reset", user_id=user_id)


def render_history_as_prompt_prefix(turns: list[Turn]) -> str:
    """Render prior turns as a plain-text prefix to inject into the next prompt.

    We use a simple, model-agnostic rendering instead of relying on the
    LLM provider's native chat history. This keeps storage portable.
    """
    if not turns:
        return ""
    lines: list[str] = ["Previous conversation with this user (most recent last):"]
    for t in turns:
        prefix = "User" if t.role == "user" else "Assistant"
        lines.append(f"{prefix}: {t.content}")
    lines.append("---")
    lines.append("Now respond to the new user message that follows.")
    return "\n".join(lines)
