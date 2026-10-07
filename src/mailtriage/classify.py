"""Rule-based classifier: weighted keyword patterns for German and English mail.

Each category has patterns with weights; a hit in the subject counts double. The category with the highest
score wins; the confidence is its share of the total score (so a close second place lowers it). No hit at all
means `other` with confidence 0. A separate `legal` detector runs on every mail regardless of category.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

CATEGORIES = ("status_inquiry", "availability_request", "scheduling", "complaint", "invoice_billing",
              "data_request", "general_question", "spam", "other")

PATTERNS: Dict[str, List[Tuple[str, int]]] = {
    "status_inquiry": [
        (r"\bstand\s+(?:meiner|der|ihrer)\s+bewerbung", 3),
        (r"\bbewerbung\b.*\b(?:erhalten|eingegangen|rückmeldung|rueckmeldung|antwort)", 2),
        (r"\b(?:rückmeldung|rueckmeldung|feedback)\b", 1),
        (r"\bwie\s+der\s+stand\b", 2),
        (r"\bapplication\s+status\b|\bstatus\s+of\s+my\s+application\b", 3),
        (r"\bhas\s+my\s+application\b|\breceived\s+my\s+application\b|\bif\s+you\s+received\s+my\b", 2),
        (r"\bwhen\s+(?:can|will)\s+i\s+(?:expect|hear)\b", 2),
        (r"\bbewerbung\b", 1),
        (r"\bapplied\b|\bapplication\b", 1),
    ],
    "availability_request": [
        (r"\bkapazit", 3),
        (r"\b(?:benötigen|brauchen|suchen)\b.*\b(?:mitarbeiter|lagerhelfer|staplerfahrer|packer|helfer|personal|kräfte)", 3),
        (r"\bstaffing\b|\bavailability\b|\btemp(?:orary)?\s+workers?\b", 3),
        (r"\bwe\s+(?:would\s+)?need\b.*\b(?:workers?|staff|packers?|drivers?|helpers?)", 3),
        (r"\b(?:lagerhelfer|staplerfahrer|packer|packers)\b", 1),
        (r"\bpersonal\b", 1),
    ],
    "scheduling": [
        (r"\btermin", 2),
        (r"\bverschieben|\babsagen|\bumbuchen", 2),
        (r"\bvorstellungsgespräch|\bvorstellungsgespraech", 2),
        (r"\binterview\b", 2),
        (r"\breschedul", 3),
        (r"\bappointment\b", 2),
        (r"\bslot\b", 2),
        (r"\bdoes\s+not\s+work\s+for\s+me\b|\bdoesn'?t\s+work\b", 2),
    ],
    "complaint": [
        (r"\bbeschwerde", 4),
        (r"\bcomplaint\b", 4),
        (r"\bunzufrieden|\binakzeptabel|\bunacceptable|\bdisappointed|\benttäuscht", 3),
        (r"\bnicht\s+erschienen", 3),
        (r"\bärgerlich", 2),
        (r"\bnie\s+wieder\b", 2),
    ],
    "invoice_billing": [
        (r"\brechnung", 3),
        (r"\bgutschrift", 3),
        (r"\bzahlung|\bzahlen\b|\büberweisung|\bmahnung", 2),
        (r"\binvoice\b", 3),
        (r"\bpayment\b", 2),
        (r"\bbuchhaltung|\baccounts?\b", 1),
    ],
    "data_request": [
        (r"\bdsgvo\b|\bgdpr\b", 4),
        (r"\blöschung\b|\bloeschung\b", 3),
        (r"\bdelete\s+my\s+(?:data|personal\s+data)\b", 4),
        (r"\bauskunft\b.*\bdaten|\bdaten\b.*\bauskunft", 3),
        (r"\bwiderruf", 2),
        (r"\bdatenschutz", 2),
        (r"\bright\s+to\s+be\s+forgotten\b", 4),
    ],
    "general_question": [
        (r"\böffnungszeiten|\bgeöffnet\b|\bopening\s+hours\b", 3),
        (r"\bparkplatz|\bparkplätze|\bparking\b|\banfahrt\b|\bwegbeschreibung", 3),
        (r"\bwo\s+finde\s+ich\b|\bwie\s+komme\s+ich\b", 2),
    ],
    "spam": [
        (r"\bunsubscribe\b|\babbestellen\b", 3),
        (r"\blimited\s+offer\b", 3),
        (r"\bclick\s+here\b|\bhier\s+klicken\b", 2),
        (r"\bseo\b|\bpage\s+one\b", 2),
        (r"\bfree\s+audit\b|\bkostenlose\s+analyse\b", 2),
        (r"\bbitcoin\b|\bcrypto\b|\blottery\b|\bgewinnspiel\b", 3),
    ],
}
_COMPILED = {cat: [(re.compile(p, re.IGNORECASE), w) for p, w in items]
             for cat, items in PATTERNS.items()}

_LEGAL = re.compile(r"\banwalt\b|\brechtsanwalt\b|\bklage\b|\bverklag|\bgericht\b|\blawyer\b|\bsolicitor\b|"
                    r"\blegal\s+action\b|\bsue\b|\bcourt\b|\bcease\s+and\s+desist\b|\babmahnung\b", re.IGNORECASE)


def score(subject: str, body: str) -> Dict[str, int]:
    scores = {}
    for category, patterns in _COMPILED.items():
        total = 0
        for pattern, weight in patterns:
            if pattern.search(subject):
                total += 2 * weight
            if pattern.search(body):
                total += weight
        scores[category] = total
    return scores


def classify(subject: str, body: str) -> Tuple[str, float]:
    """Return (category, confidence 0..1)."""
    scores = score(subject, body)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_category, top = ranked[0]
    if top == 0:
        return "other", 0.0
    second = ranked[1][1]
    return top_category, round(top / float(top + second + 1), 2)


def is_legal(subject: str, body: str) -> bool:
    return bool(_LEGAL.search(subject) or _LEGAL.search(body))


# Broader than the `data_request` category on purpose: this is a safety net that must also catch a data-protection
# request the classifier labelled wrongly. A false alarm costs a human a minute; a missed request has a legal deadline.
_DATA_PROTECTION = re.compile(
    r"\bdsgvo\b|\bgdpr\b|\bdatenschutz|\bpersonenbezogen|\bpersonal\s+(?:data|details|information)\b|"
    r"\bpersönliche\s+daten\b|\bmeine\s+daten\b|\bmy\s+(?:data|personal)\b|\blöschung\b|\bloeschung\b|"
    r"\b(?:delete|erase|remove)\s+my\b|\b(?:lösch\w*|entfern\w*)\b.{0,40}\b(?:daten|lebenslauf|unterlagen)\b|"
    r"\bright\s+to\s+be\s+forgotten\b|\bauskunft\s+über\s+(?:die\s+)?(?:gespeicherten|meine)", re.IGNORECASE)


def is_data_protection(subject: str, body: str) -> bool:
    return bool(_DATA_PROTECTION.search(subject) or _DATA_PROTECTION.search(body))
