"""Read .eml files into `Email` objects."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import timezone
from email import policy
from email.parser import BytesParser
from typing import List, Tuple

from .models import Email

_GERMAN = set("""ich und der die das nicht bitte mit für ist wir sie haben kann meine meiner ihr ihre guten tag
hallo danke grüße gruß wie wann zu auf im von den dem ein eine leider gern gerne uns mir sehr geehrte damen herren
wenn bis noch auch oder wurde habe können könnte würde""".split())
_ENGLISH = set("""the and is you please with for have can my your hello hi thanks regards what when are this that
would could we our do does not to of it if be was were had from at""".split())


def detect_language(text: str) -> str:
    """'de' or 'en' by counting common function words. Ties go to German."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    german = sum(1 for w in words if w in _GERMAN)
    english = sum(1 for w in words if w in _ENGLISH)
    return "en" if english > german else "de"


def _text(value) -> str:
    """Display names from raw 8-bit headers arrive with surrogate escapes - repair them."""
    if value is None:
        return ""
    return str(value).encode("utf-8", "surrogateescape").decode("utf-8", "replace").strip()


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_file(path: str) -> Email:
    with open(path, "rb") as handle:
        raw = handle.read()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = message["From"].addresses[0] if message["From"] and message["From"].addresses else None
    if sender is None:
        raise ValueError("no usable From header")
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = _strip_html(body)
    message_id = _text(message["Message-ID"])
    when = message["Date"].datetime if message["Date"] else None
    reply_to = ""
    if message["Reply-To"] and message["Reply-To"].addresses:
        reply_to = message["Reply-To"].addresses[0].addr_spec
    subject = _text(message["Subject"])
    return Email(
        source_id=hashlib.sha1((message_id or raw.decode("utf-8", "replace")).encode("utf-8")).hexdigest()[:16],
        message_id=message_id,
        from_name=_text(sender.display_name),
        from_addr=sender.addr_spec.lower(),
        reply_to=reply_to.lower(),
        subject=subject,
        date=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if when and when.tzinfo else "",
        in_reply_to=_text(message["In-Reply-To"]),
        references=_text(message["References"]).split(),
        body=body.strip(),
        language=detect_language(subject + " " + body),
        path=path,
    )


def parse_dir(directory: str) -> Tuple[List[Email], List[str]]:
    """Every .eml in a directory; an unreadable file is reported, not fatal."""
    emails, errors = [], []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".eml"):
            continue
        try:
            emails.append(parse_file(os.path.join(directory, name)))
        except (ValueError, OSError, KeyError, AttributeError) as exc:
            errors.append("%s: %s" % (name, exc))
    return emails, errors
