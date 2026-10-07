"""The pipeline for one mail: classify -> legal check -> facts -> reply -> policy.

The classifier is pluggable (`rules` or the language model); everything after it is deterministic and the
same for both, so the safety rules in `policy.py` cannot be argued away by a model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from . import classify as rules_classify
from .models import Email, Result
from .policy import decide, notes_for
from .replies import compose, gather_facts

# A classifier returns (category, confidence 0..1, legal_threat). `legal_threat` is only ever an addition to
# the keyword check, never a replacement for it.
Classifier = Callable[[Email], Tuple[str, float, bool]]


def rules_classifier(email: Email) -> Tuple[str, float, bool]:
    category, confidence = rules_classify.classify(email.subject, email.body)
    return category, confidence, False


def triage(email: Email, kb: Dict[str, Any], classifier: Optional[Classifier] = None) -> Result:
    category, confidence, model_legal = (classifier or rules_classifier)(email)
    legal = model_legal or rules_classify.is_legal(email.subject, email.body)
    protection = rules_classify.is_data_protection(email.subject, email.body)
    facts = gather_facts(email, kb)
    reply, reply_flags = compose(email, category, facts)
    action, flags = decide(category, confidence, legal, reply_flags, protection)
    if action in ("escalate", "ignore"):
        reply = None                      # a human answers, or nobody does
    return Result(email=email, category=category, confidence=confidence, action=action, flags=flags,
                  notes=notes_for(category, legal, email.date, protection), reply=reply)
