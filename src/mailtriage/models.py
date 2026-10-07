"""Data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Email:
    source_id: str          # stable id: hash of the Message-ID (or of the file when there is none)
    message_id: str
    from_name: str
    from_addr: str
    reply_to: str
    subject: str
    date: str               # ISO 8601 UTC, '' if missing
    in_reply_to: str
    references: List[str]
    body: str
    language: str           # 'de' or 'en'
    path: str = ""


@dataclass
class Result:
    email: Email
    category: str
    confidence: float
    action: str             # draft | draft_flagged | escalate | ignore
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    reply: Optional[str] = None
    draft_status: str = ""  # created | exists | '' (no draft)
    drafter: str = "templates"  # who wrote the reply text: templates | claude
    context: Dict[str, Any] = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.action in ("draft_flagged", "escalate")
