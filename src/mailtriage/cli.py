"""Command line interface: `python -m mailtriage ...`

`triage` is a dry run unless you say where the drafts go (`--drafts-dir` or `--imap`). Nothing in this project
can send mail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__
from .claude import ClaudeClassifier, ClassificationError
from .drafting import ClaudeDrafter
from .drafts import ImapDraftStore, MaildirDraftStore, build_draft
from .evaluate import evaluate, format_result
from .mailparse import parse_dir, parse_file
from .models import Result
from .replies import gather_facts
from .triage import rules_classifier, triage

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.normpath(os.path.join(HERE, "..", "..", "data", "sample"))
HELDOUT = os.path.normpath(os.path.join(HERE, "..", "..", "data", "heldout"))


def _classifier(backend: str, fallback: Optional[str], model: Optional[str]):
    if backend == "rules":
        return rules_classifier
    claude = ClaudeClassifier(model=model)

    def run(email):
        try:
            return claude.classify(email)
        except ClassificationError as exc:
            if fallback == "rules":
                sys.stderr.write("claude backend failed (%s) - using the rules backend\n" % exc)
                return rules_classifier(email)
            raise
    return run


def _load_kb(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _report_text(results: List[Result], errors: List[str]) -> str:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1
    created = sum(1 for r in results if r.draft_status == "created")
    existed = sum(1 for r in results if r.draft_status == "exists")
    lines = ["%d mails: %s%s" % (len(results), ", ".join("%d %s" % (counts[k], k) for k in sorted(counts)),
                                 "" if not (created or existed) else "  (drafts: %d created, %d already existed)"
                                 % (created, existed)), ""]
    for r in results:
        name = os.path.basename(r.email.path)
        lines.append("  %-34s %-20s %4.2f  %-14s %s" % (name, r.category, r.confidence, r.action,
                                                         ",".join(r.flags)))
    human = [r for r in results if r.needs_human]
    if human:
        lines += ["", "Needs a human:"]
        for r in human:
            lines.append("  %s  [%s] from %s" % (os.path.basename(r.email.path), r.action, r.email.from_addr))
            for note in r.notes:
                lines.append("      - " + note)
            if r.action == "draft_flagged":
                lines.append("      - draft created; check: " + ", ".join(r.flags))
    ignored = [r for r in results if r.action == "ignore"]
    if ignored:
        lines += ["", "Ignored (look over once, a real mail must not hide here): "
                  + ", ".join(os.path.basename(r.email.path) for r in ignored)]
    for error in errors:
        lines.append("  UNREADABLE %s" % error)
    return "\n".join(lines) + "\n"


def _report_json(results: List[Result], errors: List[str]) -> str:
    return json.dumps({
        "mails": [{"file": os.path.basename(r.email.path), "from": r.email.from_addr, "subject": r.email.subject,
                   "language": r.email.language, "category": r.category, "confidence": r.confidence,
                   "action": r.action, "flags": r.flags, "notes": r.notes, "drafter": r.drafter if r.reply else None,
                   "draft": r.draft_status or None}
                  for r in results],
        "unreadable": errors}, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mailtriage", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def backend_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--backend", choices=("rules", "claude"), default="rules")
        p.add_argument("--fallback", choices=("rules",), help="with --backend claude: use the rules on failure")
        p.add_argument("--model", help="model for the claude backend (default: claude-opus-5-5 or $MAILTRIAGE_MODEL)")

    tr = sub.add_parser("triage", help="classify the .eml files of a directory and prepare reply drafts")
    tr.add_argument("inbox")
    tr.add_argument("--kb", default=os.path.join(SAMPLE, "knowledge.json"))
    tr.add_argument("--drafts-dir", help="write drafts into <dir>/Drafts (Maildir)")
    tr.add_argument("--imap", action="store_true",
                    help="append drafts to the IMAP Drafts folder (MAILTRIAGE_IMAP_HOST/_USER/_PASSWORD/_FOLDER)")
    tr.add_argument("--mailbox", default="jobs@example.com", help="From address on the drafts")
    tr.add_argument("--format", choices=("text", "json"), default="text")
    tr.add_argument("--drafter", choices=("templates", "claude"), default="templates",
                    help="who writes the reply text: fixed templates (default) or the language model")
    backend_options(tr)

    ev = sub.add_parser("eval", help="score the pipeline against labelled mails")
    ev.add_argument("--inbox", default=os.path.join(SAMPLE, "inbox"))
    ev.add_argument("--labels", default=os.path.join(SAMPLE, "labels.json"))
    ev.add_argument("--kb", default=os.path.join(SAMPLE, "knowledge.json"))
    ev.add_argument("--min-accuracy", type=float, help="exit 1 below this category accuracy (0-1)")
    ev.add_argument("--strict-safety", action="store_true", help="exit 1 if there is any safety problem")
    backend_options(ev)

    rq = sub.add_parser("request", help="print the exact request the claude backend would send (no network)")
    rq.add_argument("file")
    rq.add_argument("--stage", choices=("classify", "draft"), default="classify",
                    help="classify = category request; draft = reply-writing request (needs --kb)")
    rq.add_argument("--kb", default=os.path.join(SAMPLE, "knowledge.json"))
    rq.add_argument("--model")

    args = parser.parse_args(argv)

    if args.command == "request":
        email = parse_file(args.file)
        if args.stage == "draft":
            category, _, _ = rules_classifier(email)
            request = ClaudeDrafter(model=args.model).build_request(email, category, gather_facts(email, _load_kb(args.kb)))
        else:
            request = ClaudeClassifier(model=args.model).build_request(email)
        print(json.dumps(request, indent=2, ensure_ascii=False))
        return 0

    classifier = _classifier(args.backend, args.fallback, args.model)
    kb = _load_kb(args.kb)

    if args.command == "eval":
        result = evaluate(classifier, args.inbox, args.labels, kb)
        sys.stdout.write(format_result(result, "backend: " + args.backend))
        if args.min_accuracy is not None and result.category_accuracy < args.min_accuracy:
            return 1
        return 1 if args.strict_safety and result.safety_problems() else 0

    # triage
    if args.drafts_dir and args.imap:
        parser.error("choose --drafts-dir or --imap, not both")
    store = None
    if args.drafts_dir:
        store = MaildirDraftStore(args.drafts_dir)
    elif args.imap:
        try:
            store = ImapDraftStore(os.environ["MAILTRIAGE_IMAP_HOST"], os.environ["MAILTRIAGE_IMAP_USER"],
                                   os.environ["MAILTRIAGE_IMAP_PASSWORD"],
                                   os.environ.get("MAILTRIAGE_IMAP_FOLDER", "Drafts"))
        except KeyError as exc:
            parser.error("--imap needs the environment variable %s" % exc.args[0])

    drafter = ClaudeDrafter(model=args.model) if args.drafter == "claude" else None
    emails, errors = parse_dir(args.inbox)
    results: List[Result] = []
    try:
        for email in emails:
            result = triage(email, kb, classifier, drafter)
            if store is not None and result.reply:
                message = build_draft(email, result.reply, result.category, result.confidence, result.flags,
                                      args.mailbox, datetime.now(timezone.utc), result.drafter)
                result.draft_status = store.save(message, email.source_id)
            results.append(result)
    finally:
        if store is not None:
            store.close()
    sys.stdout.write(_report_json(results, errors) if args.format == "json" else _report_text(results, errors))
    if store is None:
        sys.stderr.write("dry run: no drafts written (use --drafts-dir or --imap)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
