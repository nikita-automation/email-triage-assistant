import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from mailtriage.cli import main
from mailtriage.evaluate import EvalResult, evaluate, format_result
from mailtriage.mailparse import parse_dir
from mailtriage.triage import rules_classifier, triage
from helpers import (HELDOUT_INBOX, HELDOUT_LABELS, SAMPLE_INBOX, SAMPLE_KB, SAMPLE_LABELS, load_kb)

EXPECTED_ACTIONS = {
    "01_status_anna_de.eml": ("status_inquiry", "draft"),
    "02_status_ben_en.eml": ("status_inquiry", "draft"),
    "03_availability_customer_de.eml": ("availability_request", "draft_flagged"),
    "04_scheduling_clara_de.eml": ("scheduling", "draft"),
    "05_complaint_customer_de.eml": ("complaint", "draft_flagged"),
    "06_invoice_de.eml": ("invoice_billing", "draft_flagged"),
    "07_gdpr_de.eml": ("data_request", "escalate"),
    "08_legal_invoice_de.eml": ("invoice_billing", "escalate"),
    "09_spam_en.eml": ("spam", "ignore"),
    "10_faq_parking_de.eml": ("general_question", "draft"),
    "11_status_unknown_de.eml": ("status_inquiry", "draft_flagged"),
    "12_availability_en.eml": ("availability_request", "draft_flagged"),
    "13_scheduling_david_en.eml": ("scheduling", "draft"),
    "14_faq_hours_en.eml": ("general_question", "draft"),
    "15_invoice_payment_en.eml": ("invoice_billing", "draft_flagged"),
    "16_other_de.eml": ("other", "escalate"),
}


class SamplePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_kb()
        emails, _ = parse_dir(SAMPLE_INBOX)
        cls.results = {os.path.basename(e.path): triage(e, cls.kb) for e in emails}

    def test_categories_and_actions_are_exactly_the_expected_ones(self):
        got = {name: (r.category, r.action) for name, r in self.results.items()}
        self.assertEqual(got, EXPECTED_ACTIONS)

    def test_escalated_and_ignored_mails_get_no_reply_text(self):
        for name, result in self.results.items():
            if result.action in ("escalate", "ignore"):
                self.assertIsNone(result.reply, msg=name)
            else:
                self.assertTrue(result.reply, msg=name)

    def test_the_legal_invoice_mail_is_escalated_despite_being_an_invoice_mail(self):
        result = self.results["08_legal_invoice_de.eml"]
        self.assertEqual(result.category, "invoice_billing")
        self.assertEqual(result.flags, ["money", "legal"])

    def test_gdpr_mail_carries_the_one_month_deadline(self):
        notes = self.results["07_gdpr_de.eml"].notes
        self.assertIn("due 2026-11-12", notes[0])

    def test_unknown_sender_and_missing_capacity_are_visible_as_flags(self):
        self.assertEqual(self.results["11_status_unknown_de.eml"].flags, ["unknown_sender"])
        self.assertEqual(self.results["12_availability_en.eml"].flags, ["commitment"])
        self.assertIn("packers: 2", self.results["12_availability_en.eml"].reply)


class EvaluateTests(unittest.TestCase):
    def test_development_set_is_perfect_and_safe(self):
        result = evaluate(rules_classifier, SAMPLE_INBOX, SAMPLE_LABELS, load_kb())
        self.assertEqual((result.category_accuracy, result.language_accuracy, result.legal_recall), (1.0, 1.0, 1.0))
        self.assertEqual(result.safety_problems(), [])
        self.assertEqual(result.action_counts(), {"draft": 6, "draft_flagged": 6, "escalate": 3, "ignore": 1})

    def test_held_out_set_stays_honest_and_stays_safe(self):
        result = evaluate(rules_classifier, HELDOUT_INBOX, HELDOUT_LABELS, load_kb())
        self.assertLess(result.category_accuracy, 0.5)             # if this ever passes 50 %, it is no longer held out
        self.assertGreaterEqual(result.category_accuracy, 0.25)
        self.assertEqual(result.safety_problems(), [])             # wrong category, but never an unsafe action
        self.assertEqual(result.legal_recall, 1.0)

    def test_the_prompt_injection_mail_is_classified_by_content_not_by_the_injected_text(self):
        emails = {os.path.basename(e.path): e for e in parse_dir(HELDOUT_INBOX)[0]}
        result = triage(emails["h7_injection_de.eml"], load_kb())
        self.assertEqual(result.category, "invoice_billing")
        self.assertNotIn("bezahlt", result.reply)

    def test_safety_problems_are_detected(self):
        result = EvalResult()
        base = {"language_ok": True, "category_ok": True, "confidence": 0.9, "legal": False}
        result.rows = [
            dict(base, file="a", expected_category="complaint", expected_legal=True, action="draft_flagged"),
            dict(base, file="b", expected_category="data_request", expected_legal=False, action="draft"),
            dict(base, file="c", expected_category="status_inquiry", expected_legal=False, action="ignore"),
            dict(base, file="d", expected_category="spam", expected_legal=False, action="draft"),
            dict(base, file="e", expected_category="spam", expected_legal=False, action="ignore"),
        ]
        result.errors = {"f": "RuntimeError: boom"}
        problems = dict(result.safety_problems())
        self.assertEqual(set(problems), {"a", "b", "c", "d", "f"})
        self.assertIn("legal threat", problems["a"])
        self.assertIn("data-protection request", problems["b"])
        self.assertIn("ignored as spam", problems["c"])
        self.assertIn("reply draft", problems["d"])
        self.assertIn("not classified", problems["f"])

    def test_a_crashing_classifier_is_reported_per_mail(self):
        def boom(email):
            raise RuntimeError("no network")
        result = evaluate(boom, SAMPLE_INBOX, SAMPLE_LABELS, load_kb())
        self.assertEqual(len(result.errors), 16)
        self.assertEqual(result.category_accuracy, 0.0)
        self.assertIn("RuntimeError: no network", format_result(result))

    def test_unlabelled_and_missing_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            labels = os.path.join(tmp, "labels.json")
            with open(labels, "w") as handle:
                json.dump({"01_status_anna_de.eml": {"category": "status_inquiry", "language": "de", "legal": False},
                           "ghost.eml": {"category": "other", "language": "de", "legal": False}}, handle)
            result = evaluate(rules_classifier, SAMPLE_INBOX, labels, load_kb())
        self.assertEqual(result.errors, {"ghost.eml": "mail file not found"})
        self.assertEqual(len(result.missing), 15)
        self.assertIn("Mails without a label", format_result(result))


def run_cli(*args, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(args))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_triage_is_a_dry_run_by_default(self):
        code, out, err = run_cli("triage", SAMPLE_INBOX, "--kb", SAMPLE_KB)
        self.assertEqual(code, 0)
        self.assertIn("16 mails: 6 draft, 6 draft_flagged, 3 escalate, 1 ignore", out)
        self.assertNotIn("drafts:", out)
        self.assertIn("dry run", err)
        self.assertIn("Ignored (look over once", out)

    def test_triage_writes_idempotent_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli("triage", SAMPLE_INBOX, "--drafts-dir", tmp)
            self.assertIn("(drafts: 12 created, 0 already existed)", out)
            self.assertNotIn("dry run", err)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "Drafts", "cur"))), 12)
            _, again, _ = run_cli("triage", SAMPLE_INBOX, "--drafts-dir", tmp)
            self.assertIn("(drafts: 0 created, 12 already existed)", again)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "Drafts", "cur"))), 12)

    def test_json_report(self):
        _, out, _ = run_cli("triage", SAMPLE_INBOX, "--format", "json")
        data = json.loads(out)
        self.assertEqual(len(data["mails"]), 16)
        gdpr = [m for m in data["mails"] if m["file"] == "07_gdpr_de.eml"][0]
        self.assertEqual((gdpr["action"], gdpr["flags"], gdpr["draft"]), ("escalate", ["data_protection"], None))

    def test_imap_needs_its_environment_and_cannot_be_combined_with_a_directory(self):
        env = {k: "" for k in ("MAILTRIAGE_IMAP_HOST", "MAILTRIAGE_IMAP_USER", "MAILTRIAGE_IMAP_PASSWORD")}
        with mock.patch.dict(os.environ):
            for key in env:
                os.environ.pop(key, None)
            code, _, err = run_cli("triage", SAMPLE_INBOX, "--imap")
        self.assertEqual(code, 2)
        self.assertIn("MAILTRIAGE_IMAP_HOST", err)
        code, _, err = run_cli("triage", SAMPLE_INBOX, "--imap", "--drafts-dir", "/tmp/x")
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_eval_exit_codes(self):
        code, out, _ = run_cli("eval", "--min-accuracy", "0.95", "--strict-safety")
        self.assertEqual(code, 0)
        self.assertIn("category accuracy   100.0%", out)
        self.assertIn("Safety: no problems", out)
        code, out, _ = run_cli("eval", "--inbox", HELDOUT_INBOX, "--labels", HELDOUT_LABELS, "--min-accuracy", "0.9")
        self.assertEqual(code, 1)

    def test_request_prints_the_request_without_network(self):
        code, out, _ = run_cli("request", os.path.join(SAMPLE_INBOX, "07_gdpr_de.eml"))
        request = json.loads(out)
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertIn("DSGVO", request["messages"][0]["content"])

    def test_claude_backend_without_sdk_falls_back_when_asked(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            code, out, err = run_cli("triage", SAMPLE_INBOX, "--backend", "claude", "--fallback", "rules")
        self.assertEqual(code, 0)
        self.assertIn("using the rules backend", err)
        self.assertIn("16 mails: 6 draft, 6 draft_flagged, 3 escalate, 1 ignore", out)

    def test_claude_backend_without_sdk_and_without_fallback_fails_loudly(self):
        from mailtriage.claude import ClassificationError
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(ClassificationError):
                run_cli("triage", SAMPLE_INBOX, "--backend", "claude")


if __name__ == "__main__":
    unittest.main()
