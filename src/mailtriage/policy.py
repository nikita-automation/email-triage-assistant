"""What happens to a mail after it has been classified. The rules here are the safety net: they apply no
matter which backend produced the category, so a model can never talk its way into drafting a reply to a
legal threat or a data-protection request.

Actions
  draft          a reply draft is created, ready for a quick review
  draft_flagged  a draft is created but marked: a human must read it carefully (commitments, money, anger)
  escalate       no draft - a human writes the answer; the triage note says why and what is due
  ignore         no draft, nothing to do (spam)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Tuple

MIN_CONFIDENCE = 0.45

BASE_ACTION = {
    "status_inquiry": "draft",
    "scheduling": "draft",
    "general_question": "draft",
    "availability_request": "draft_flagged",   # promises capacity to a customer
    "complaint": "draft_flagged",               # tone matters
    "invoice_billing": "draft_flagged",         # money
    "data_request": "escalate",                 # legal deadline, a person must handle it
    "other": "escalate",
    "spam": "ignore",
}


def decide(category: str, confidence: float, legal: bool, reply_flags: List[str],
           data_protection: bool = False) -> Tuple[str, List[str]]:
    """Return (action, flags). `legal` and `data_protection` are keyword safety nets: they escalate no matter
    which category the classifier chose."""
    flags = list(reply_flags)
    if legal:
        flags.append("legal")
    if data_protection:
        flags.append("data_protection")
    if legal or data_protection:
        return "escalate", flags
    action = BASE_ACTION[category]
    if action in ("draft", "draft_flagged") and confidence < MIN_CONFIDENCE:
        return "escalate", flags + ["low_confidence"]
    if action == "draft" and flags:
        action = "draft_flagged"
    if category == "availability_request" and "commitment" not in flags:
        flags.append("commitment")              # a capacity statement is a promise to a customer
    return action, flags


def add_one_month(day: date) -> date:
    """Same day next month, clamped to the last day of a shorter month."""
    month = day.month % 12 + 1
    year = day.year + (day.month == 12)
    for offset in range(4):                       # 31 -> 30 -> 29 -> 28
        try:
            return day.replace(year=year, month=month, day=day.day - offset)
        except ValueError:
            continue
    return day + timedelta(days=30)               # pragma: no cover


def notes_for(category: str, legal: bool, received: str, data_protection: bool = False) -> List[str]:
    notes = []
    if legal:
        notes.append("Legal wording detected - do not answer without review by a responsible person.")
    if category == "data_request" or data_protection:
        deadline = ""
        if received:
            deadline = " (received %s, due %s)" % (received[:10], add_one_month(date.fromisoformat(received[:10])))
        notes.append("Possible data-protection request: answer without undue delay, at the latest within one month "
                     "(Art. 12(3) GDPR)%s." % deadline)
    return notes
