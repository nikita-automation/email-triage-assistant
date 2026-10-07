"""Shared test helpers."""

import json
import os

from mailtriage.models import Email

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SAMPLE_INBOX = os.path.join(ROOT, "sample", "inbox")
SAMPLE_LABELS = os.path.join(ROOT, "sample", "labels.json")
SAMPLE_KB = os.path.join(ROOT, "sample", "knowledge.json")
HELDOUT_INBOX = os.path.join(ROOT, "heldout", "inbox")
HELDOUT_LABELS = os.path.join(ROOT, "heldout", "labels.json")


def load_kb():
    with open(SAMPLE_KB, encoding="utf-8") as handle:
        return json.load(handle)


def make_email(subject="Hallo", body="", from_addr="someone@example.org", from_name="Some One",
               language="de", date="2026-10-12T08:00:00Z", message_id="<m1@example.org>"):
    return Email(source_id="abc123", message_id=message_id, from_name=from_name, from_addr=from_addr,
                 reply_to="", subject=subject, date=date, in_reply_to="", references=[], body=body,
                 language=language, path="/tmp/x.eml")
