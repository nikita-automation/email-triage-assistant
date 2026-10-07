"""The pipeline for one mail: classify -> legal check -> facts -> reply -> policy.

The classifier is pluggable (`rules` or the language model); everything after it is deterministic and the
same for both, so the safety rules in `policy.py` cannot be argued away by a model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from . import classify as rules_classify
from .drafting import DraftingError
from .models import Email, Result
from .policy import decide, notes_for
from .replies import compose, gather_facts

# A classifier returns (category, confidence 0..1, legal_threat). `legal_threat` is only ever an addition to
# the keyword check, never a replacement for it.
Classifier = Callable[[Email], Tuple[str, float, bool]]


def rules_classifier(email: Email) -> Tuple[str, float, bool]:
    category, confidence = rules_classify.classify(email.subject, email.body)
    return category, confidence, False


def triage(email: Email, kb: Dict[str, Any], classifier: Optional[Classifier] = None,
           drafter: Optional[Any] = None) -> Result:
    """Classify, apply the safety nets, decide the action, and write the reply.

    The reply comes from templates by default. With a `drafter` (the language model) the template text is still
    built first - its flags describe the facts situation and it is the fallback - and the model's text replaces it
    only for mails that get a draft at all. A failing drafter never stops the batch: the template draft is used and
    flagged `template_fallback`."""
    category, confidence, model_legal = (classifier or rules_classifier)(email)
    legal = model_legal or rules_classify.is_legal(email.subject, email.body)
    protection = rules_classify.is_data_protection(email.subject, email.body)
    facts = gather_facts(email, kb)
    reply, reply_flags = compose(email, category, facts)
    action, flags = decide(category, confidence, legal, reply_flags, protection)
    notes = notes_for(category, legal, email.date, protection)
    written_by = "templates"
    flags_before = len(flags)
    if drafter is not None and action in ("draft", "draft_flagged"):
        try:
            reply, extra_flags, extra_notes = drafter.draft(email, category, facts)
            written_by = "claude"
            flags += [f for f in extra_flags if f not in flags]
            notes += extra_notes
        except DraftingError as exc:
            flags.append("template_fallback")
            notes.append("Model draft unavailable (%s) - this is the template reply." % exc)
        if len(flags) > flags_before and action == "draft":
            action = "draft_flagged"            # anything the checks found: a human looks closer
    if action in ("escalate", "ignore"):
        reply = None                            # a human answers, or nobody does
    return Result(email=email, category=category, confidence=confidence, action=action, flags=flags,
                  notes=notes, reply=reply, drafter=written_by if reply else "templates")
