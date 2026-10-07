"""Draft messages and where they are stored.

A draft is a normal RFC 5322 message with `In-Reply-To`/`References` set so mail clients thread it, a visible
banner in the first line so it cannot be sent by accident without being read, and X-headers carrying the
triage result. Two stores:

  MaildirDraftStore  a local Maildir - for the demo, tests, and any mail setup that syncs a Maildir
  ImapDraftStore     appends to the `Drafts` folder of a real IMAP mailbox with the \\Draft flag

Both are idempotent: running the triage twice does not create a second draft for the same mail. Neither can
send anything - there is no SMTP code in this project on purpose.
"""

from __future__ import annotations

import imaplib
import os
import time
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, parsedate_to_datetime
from typing import Any, Callable, List, Optional

from .models import Email

BANNER = {"de": "[ENTWURF - bitte prüfen und anpassen; wurde nicht automatisch gesendet]",
          "en": "[DRAFT - please review and edit; not sent automatically]"}
_WROTE = {"de": "Am %s schrieb %s:", "en": "On %s, %s wrote:"}


def build_draft(email: Email, reply: str, category: str, confidence: float, flags: List[str],
                mailbox: str, now: datetime, drafter: str = "templates") -> EmailMessage:
    message = EmailMessage()
    subject = email.subject or ""
    message["Subject"] = subject if subject.lower().startswith(("re:", "aw:")) else "Re: " + subject
    message["From"] = mailbox
    message["To"] = formataddr((email.from_name, email.reply_to or email.from_addr))
    message["Date"] = format_datetime(now)
    message["Message-ID"] = "<draft-%s@mailtriage.local>" % email.source_id
    if email.message_id:
        message["In-Reply-To"] = email.message_id
        message["References"] = " ".join(email.references + [email.message_id])
    message["X-Draft-Source"] = "mailtriage"
    message["X-Triage-Source-Id"] = email.source_id
    message["X-Triage-Category"] = category
    message["X-Triage-Confidence"] = "%.2f" % confidence
    message["X-Triage-Flags"] = ",".join(flags) if flags else "none"
    message["X-Triage-Drafter"] = drafter

    quoted = "\n".join("> " + line for line in email.body.splitlines()[:15])
    when = email.date[:10] if email.date else "?"
    who = email.from_name or email.from_addr
    message.set_content("%s\n\n%s\n\n%s\n%s\n" % (BANNER[email.language], reply,
                                                  _WROTE[email.language] % (when, who), quoted))
    return message


class MaildirDraftStore:
    def __init__(self, root: str):
        self.folder = os.path.join(root, "Drafts")
        for part in ("tmp", "new", "cur"):
            os.makedirs(os.path.join(self.folder, part), exist_ok=True)

    def _exists(self, source_id: str) -> bool:
        marker = ".%s." % source_id
        return any(marker in name for part in ("new", "cur") for name in os.listdir(os.path.join(self.folder, part)))

    def save(self, message: EmailMessage, source_id: str) -> str:
        if self._exists(source_id):
            return "exists"
        stamp = int(parsedate_to_datetime(message["Date"]).timestamp())
        name = "%d.%s.mailtriage:2,DS" % (stamp, source_id)           # D = draft, S = seen
        temporary = os.path.join(self.folder, "tmp", name)
        with open(temporary, "wb") as handle:
            handle.write(message.as_bytes())
        os.rename(temporary, os.path.join(self.folder, "cur", name))   # atomic: readers never see half a file
        return "created"

    def close(self) -> None:
        pass


class ImapDraftStore:
    """Append drafts to an IMAP mailbox. Credentials come from the caller (the CLI reads them from the
    environment) and are never stored or logged."""

    def __init__(self, host: str, user: str, password: str, folder: str = "Drafts", port: int = 993,
                 connect: Callable[..., Any] = imaplib.IMAP4_SSL):
        self.host, self.user, self.password, self.folder, self.port = host, user, password, folder, port
        self._connect = connect
        self._connection: Optional[Any] = None

    def _conn(self) -> Any:
        if self._connection is None:
            connection = self._connect(self.host, self.port)
            connection.login(self.user, self.password)
            status, _ = connection.select(self.folder)
            if status != "OK":
                raise RuntimeError("cannot open IMAP folder %r" % self.folder)
            self._connection = connection
        return self._connection

    def save(self, message: EmailMessage, source_id: str) -> str:
        connection = self._conn()
        status, found = connection.search(None, "HEADER", "X-Triage-Source-Id", source_id)
        if status == "OK" and found and found[0].strip():
            return "exists"
        status, _ = connection.append(self.folder, "\\Draft", imaplib.Time2Internaldate(time.time()),
                                      message.as_bytes(policy=policy.SMTP))
        if status != "OK":
            raise RuntimeError("IMAP APPEND failed: %s" % status)
        return "created"

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.logout()
            finally:
                self._connection = None
