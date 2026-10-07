"""Reply composition. A reply may only state facts that are in the knowledge base (`facts`); where a fact is
missing the reply says so and the draft is flagged for a human. Nothing is promised that the knowledge base
does not back up."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .models import Email

_WEEKDAYS = {"de": ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."],
             "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
_THANKS = {"de": "vielen Dank für Ihre Nachricht. ", "en": "thank you for your message. "}
_STATUS = {
    "received": {"de": "Ihre Bewerbung ist bei uns eingegangen und wird derzeit geprüft.",
                 "en": "We have received your application and are currently reviewing it."},
    "in_review": {"de": "Ihre Bewerbung wird derzeit von uns geprüft. Sobald es eine Entscheidung gibt, melden wir uns bei Ihnen.",
                  "en": "Your application is being reviewed. As soon as there is a decision we will get back to you."},
    "interview_scheduled": {"de": "Für Sie ist ein Vorstellungsgespräch vorgesehen: {note}.",
                            "en": "An interview has been scheduled for you: {note}."},
}


def format_date(iso: str, language: str) -> str:
    year, month, day = (int(part) for part in iso[:10].split("-"))
    if language == "de":
        return "%02d.%02d.%04d" % (day, month, year)
    return "%d %s %d" % (day, _MONTHS_EN[month - 1], year)


def format_slot(iso: str, language: str) -> str:
    when = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
    weekday = _WEEKDAYS[language][when.weekday()]
    if language == "de":
        return "%s %s, %s Uhr" % (weekday, when.strftime("%d.%m.%Y"), when.strftime("%H:%M"))
    return "%s %s, %s" % (weekday, format_date(when.strftime("%Y-%m-%d"), "en"), when.strftime("%H:%M"))


def _mentions(text: str, keyword: str) -> bool:
    """Whole-word match that tolerates an inflection of up to three letters: 'packer' also finds 'packers'
    and 'packerin', 'open' finds 'opening'. A keyword with a space ('opening hours') matches as written."""
    return re.search(r"\b%s\w{0,3}\b" % re.escape(keyword), text, re.IGNORECASE) is not None


def gather_facts(email: Email, kb: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the knowledge base can tell us about this mail. Also handed to the language model."""
    text = "%s\n%s" % (email.subject, email.body)
    person = kb.get("people", {}).get(email.from_addr)
    capacity = [item for item in kb.get("capacity", []) if any(_mentions(text, k) for k in item["keywords"])]
    faq = [item for item in kb.get("faq", []) if any(_mentions(text, k) for k in item["keywords"])]
    after = email.date[:10] if email.date else ""
    slots = [s for s in kb.get("interview_slots", []) if s[:10] > after][:3]
    return {"person": person, "capacity": capacity, "faq": faq, "slots": slots,
            "company": kb.get("company", {})}


def _addressee(email: Email, facts: Dict[str, Any]) -> str:
    """Who to greet. The sender's own display name if it looks like a person ('first last'); otherwise the
    applicant's name from the knowledge base. A customer record holds the *company* name, which is not a
    greeting, and a bare 'Buchhaltung' is not a name either - then the greeting stays generic."""
    if len(email.from_name.split()) >= 2:
        return email.from_name
    person = facts["person"] or {}
    if person.get("role") == "applicant":
        return person.get("name", "")
    return ""


def _greeting(email: Email, facts: Dict[str, Any]) -> str:
    name = _addressee(email, facts)
    if email.language == "de":
        return "Guten Tag %s," % name if name else "Guten Tag,"
    return "Hello %s," % name if name else "Hello,"


def closing(email: Email, facts: Dict[str, Any]) -> str:
    company = facts["company"]
    if email.language == "de":
        return "Mit freundlichen Grüßen\n%s\n%s" % (company.get("team_de", ""), company.get("name", ""))
    return "Kind regards\n%s\n%s" % (company.get("team_en", ""), company.get("name", ""))


def _body(category: str, email: Email, facts: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    lang = email.language
    de = lang == "de"
    person = facts["person"]
    manager = (person or {}).get("account_manager")
    flags: List[str] = []
    lines: List[str] = []

    if category == "status_inquiry":
        if person is None:
            flags.append("unknown_sender")
            lines.append("vielen Dank für Ihre Nachricht. Wir prüfen Ihre Anfrage und melden uns bei Ihnen."
                         if de else "thank you for your message. We are looking into your request and will get back to you.")
        elif person.get("status") in _STATUS:
            lines.append(_THANKS[lang] + _STATUS[person["status"]][lang].format(note=person.get("note", "")))
        else:
            flags.append("unknown_status")
            lines.append("vielen Dank für Ihre Nachricht. Wir prüfen den Stand Ihrer Bewerbung und melden uns bei Ihnen."
                         if de else "thank you for your message. We are checking the status of your application and will get back to you.")

    elif category == "scheduling":
        if person is None:
            flags.append("unknown_sender")
        if facts["slots"]:
            options = "\n".join("- " + format_slot(s, lang) for s in facts["slots"])
            lines.append(("gern finden wir einen neuen Termin. Folgende Zeiten wären möglich:\n%s\nBitte teilen Sie uns mit, "
                          "welcher Termin Ihnen passt." % options) if de else
                         ("we are happy to find another time. These slots are available:\n%s\nPlease let us know which one suits you." % options))
        else:
            flags.append("no_slots")
            lines.append("vielen Dank für Ihre Nachricht. Wir melden uns mit Terminvorschlägen bei Ihnen."
                         if de else "thank you for your message. We will get back to you with new time slots.")

    elif category == "availability_request":
        if facts["capacity"]:
            rows = "\n".join("- %s: %d, %s %s" % (c["label_de" if de else "label_en"], c["headcount"],
                                                   "ab" if de else "from", format_date(c["from"], lang))
                             for c in facts["capacity"])
            lines.append(("vielen Dank für Ihre Anfrage. Nach aktuellem Stand können wir Folgendes anbieten (unverbindlich):\n%s\n"
                          "%s meldet sich zur Bestätigung bei Ihnen." % (rows, manager or "Ein Kollege")) if de else
                         ("thank you for your request. As things stand we can offer the following (non-binding):\n%s\n"
                          "%s will contact you to confirm." % (rows, manager or "A colleague")))
        else:
            flags.append("no_capacity_match")
            lines.append("vielen Dank für Ihre Anfrage. Wir prüfen die Verfügbarkeit und melden uns bei Ihnen."
                         if de else "thank you for your request. We are checking availability and will get back to you.")
        if person is None:
            flags.append("unknown_sender")

    elif category == "complaint":
        who = manager or ("ein Kollege" if de else "a colleague")
        who = who[:1].upper() + who[1:]
        lines.append(("vielen Dank für Ihre Nachricht, und es tut uns leid, dass es zu Problemen gekommen ist. Wir nehmen Ihr "
                      "Anliegen ernst und prüfen es umgehend. %s meldet sich bei Ihnen." % who) if de else
                     ("thank you for your message, and we are sorry that there were problems. We take this seriously and are "
                      "looking into it right away. %s will contact you." % who))
        flags.append("complaint")

    elif category == "invoice_billing":
        lines.append("vielen Dank für Ihre Nachricht zur Rechnung. Wir leiten sie an unsere Buchhaltung weiter und melden uns bei Ihnen."
                     if de else "thank you for your message about the invoice. We are passing it on to our accounting department "
                                "and will get back to you.")
        flags.append("money")

    elif category == "general_question":
        if facts["faq"]:
            lines.append(_THANKS[lang] + "\n".join(item["answer_de" if de else "answer_en"] for item in facts["faq"]))
        else:
            flags.append("no_faq_match")
            lines.append("vielen Dank für Ihre Nachricht. Wir melden uns bei Ihnen." if de
                         else "thank you for your message. We will get back to you.")
    return lines, flags


def compose(email: Email, category: str, facts: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """Return (reply text or None, flags). Categories that never get a draft return (None, [])."""
    if category in ("data_request", "spam", "other"):
        return None, []
    lines, flags = _body(category, email, facts)
    text = "%s\n\n%s\n\n%s" % (_greeting(email, facts), "\n".join(lines), closing(email, facts))
    return text, flags
