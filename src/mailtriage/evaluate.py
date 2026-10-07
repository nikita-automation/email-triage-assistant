"""Measure the whole pipeline against hand-labelled mails.

Accuracy alone hides what matters, so the report has two parts:
  quality  category accuracy, language accuracy, recall of the legal flag
  safety   the mistakes that cost something: a legal threat or data request that was *not* escalated,
           a real mail that was *ignored* as spam (lost), spam that got a reply draft
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, List, Tuple

from .classify import is_legal
from .mailparse import parse_dir
from .triage import Classifier, triage


class EvalResult:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.errors: Dict[str, str] = {}
        self.missing: List[str] = []

    @property
    def total(self) -> int:
        return len(self.rows) + len(self.errors)

    def _accuracy(self, key: str) -> float:
        return sum(1 for r in self.rows if r[key + "_ok"]) / float(self.total) if self.total else 0.0

    @property
    def category_accuracy(self) -> float:
        return self._accuracy("category")

    @property
    def language_accuracy(self) -> float:
        return self._accuracy("language")

    @property
    def legal_recall(self) -> float:
        wanted = [r for r in self.rows if r["expected_legal"]]
        return sum(1 for r in wanted if r["legal"]) / float(len(wanted)) if wanted else 1.0

    def safety_problems(self) -> List[Tuple[str, str]]:
        problems = []
        for r in self.rows:
            if r["expected_legal"] and r["action"] != "escalate":
                problems.append((r["file"], "legal threat was not escalated (action: %s)" % r["action"]))
            if r["expected_category"] == "data_request" and r["action"] != "escalate":
                problems.append((r["file"], "data-protection request was not escalated (action: %s)" % r["action"]))
            if r["expected_category"] != "spam" and r["action"] == "ignore":
                problems.append((r["file"], "real mail was ignored as spam"))
            if r["expected_category"] == "spam" and r["action"] in ("draft", "draft_flagged"):
                problems.append((r["file"], "spam received a reply draft"))
        for name, message in self.errors.items():
            problems.append((name, "not classified: " + message))
        return problems

    def action_counts(self) -> Dict[str, int]:
        return dict(Counter(r["action"] for r in self.rows))


def evaluate(classifier: Classifier, inbox: str, labels_path: str, kb: Dict[str, Any]) -> EvalResult:
    with open(labels_path, encoding="utf-8") as handle:
        labels = json.load(handle)
    emails, parse_errors = parse_dir(inbox)
    by_file = {os.path.basename(e.path): e for e in emails}
    result = EvalResult()
    for name in sorted(labels):
        email = by_file.get(name)
        if email is None:
            reasons = [m for m in parse_errors if m.startswith(name)]
            result.errors[name] = reasons[0] if reasons else "mail file not found"
            continue
        try:
            outcome = triage(email, kb, classifier)
        except Exception as exc:                       # one failing mail must not hide the others
            result.errors[name] = "%s: %s" % (type(exc).__name__, exc)
            continue
        expected = labels[name]
        result.rows.append({
            "file": name, "category": outcome.category, "expected_category": expected["category"],
            "category_ok": outcome.category == expected["category"],
            "language": email.language, "language_ok": email.language == expected["language"],
            "expected_legal": expected["legal"], "legal": "legal" in outcome.flags,
            "action": outcome.action, "confidence": outcome.confidence,
        })
    result.missing = sorted(set(by_file) - set(labels))
    return result


def format_result(result: EvalResult, title: str = "") -> str:
    lines = [title, ""] if title else []
    lines.append("category accuracy   %5.1f%%  (%d of %d)" % (
        100 * result.category_accuracy, sum(1 for r in result.rows if r["category_ok"]), result.total))
    lines.append("language accuracy   %5.1f%%" % (100 * result.language_accuracy))
    lines.append("legal-flag recall   %5.1f%%" % (100 * result.legal_recall))
    counts = result.action_counts()
    lines.append("actions             " + ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts)))
    wrong = [r for r in result.rows if not r["category_ok"]]
    if wrong:
        lines += ["", "Wrong category:"]
        for r in wrong:
            lines.append("  %-34s got %-20s expected %-20s (confidence %.2f, action %s)"
                         % (r["file"], r["category"], r["expected_category"], r["confidence"], r["action"]))
    problems = result.safety_problems()
    lines += ["", "Safety: %s" % ("no problems" if not problems else "%d problem(s)" % len(problems))]
    for name, message in problems:
        lines.append("  %-34s %s" % (name, message))
    if result.missing:
        lines += ["", "Mails without a label (not scored): " + ", ".join(result.missing)]
    return "\n".join(lines) + "\n"
