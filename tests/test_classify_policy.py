import unittest
from datetime import date

from mailtriage import classify, policy


class ClassifyTests(unittest.TestCase):
    def check(self, subject, body, expected):
        category, confidence = classify.classify(subject, body)
        self.assertEqual(category, expected, msg="%r / %r -> %s (%.2f)" % (subject, body, category, confidence))
        return confidence

    def test_each_category_in_both_languages(self):
        cases = [
            ("Stand meiner Bewerbung", "Wie ist der Stand?", "status_inquiry"),
            ("Application status", "Did you receive my application?", "status_inquiry"),
            ("Anfrage Personal", "Wir benötigen fünf Lagerhelfer. Haben Sie Kapazitäten?", "availability_request"),
            ("Staffing request", "We would need 3 packers. Do you have availability?", "availability_request"),
            ("Terminverschiebung", "Kann ich den Termin verschieben?", "scheduling"),
            ("Interview time", "Wednesday does not work for me, do you have another slot?", "scheduling"),
            ("Beschwerde", "Das ist inakzeptabel, wir sind unzufrieden.", "complaint"),
            ("Complaint", "This is unacceptable.", "complaint"),
            ("Rechnung 17", "Bitte senden Sie eine Gutschrift.", "invoice_billing"),
            ("Payment confirmation", "We transferred the payment for the invoice.", "invoice_billing"),
            ("Löschung meiner Daten", "Gemäß DSGVO bitte alles löschen.", "data_request"),
            ("Please remove me", "Please delete my personal data, GDPR.", "data_request"),
            ("Öffnungszeiten", "Wann sind Sie geöffnet? Gibt es Parkplätze?", "general_question"),
            ("Opening hours", "Is there parking at the office?", "general_question"),
            ("Special offer", "Limited offer! Click here to unsubscribe from our SEO newsletter.", "spam"),
        ]
        for subject, body, expected in cases:
            self.check(subject, body, expected)

    def test_nothing_matches_gives_other_with_zero_confidence(self):
        self.assertEqual(classify.classify("Kurze Frage", "ich bin Fotograf"), ("other", 0.0))
        self.assertEqual(classify.classify("", ""), ("other", 0.0))

    def test_a_subject_hit_counts_double(self):
        in_subject = classify.score("Rechnung", "")["invoice_billing"]
        in_body = classify.score("", "Rechnung")["invoice_billing"]
        self.assertEqual(in_subject, 2 * in_body)

    def test_confidence_drops_when_two_categories_compete(self):
        clear = self.check("Beschwerde", "Das ist inakzeptabel.", "complaint")
        mixed = classify.classify("Beschwerde Rechnung", "Die Rechnung ist falsch, wir sind unzufrieden.")[1]
        self.assertLess(mixed, clear)

    def test_confidence_is_between_zero_and_one(self):
        for subject, body in (("Rechnung", "Rechnung Gutschrift Zahlung"), ("Terminverschiebung", "Termin"), ("", "x")):
            self.assertTrue(0.0 <= classify.classify(subject, body)[1] < 1.0)

    def test_legal_wording(self):
        for text in ("Ich übergebe das meinem Anwalt.", "We will take legal action.", "Das Gericht entscheidet.",
                     "I will sue you.", "Abmahnung folgt"):
            self.assertTrue(classify.is_legal("", text), msg=text)
        self.assertFalse(classify.is_legal("Anfrage", "Haben Sie Kapazitäten für November?"))
        self.assertTrue(classify.is_legal("Letzte Aufforderung vom Rechtsanwalt", ""))

    def test_data_protection_net_is_broader_than_the_category(self):
        for text in ("Please remove my CV and personal details from your database.", "Bitte löschen Sie meine Unterlagen.",
                     "Ich möchte Auskunft über die gespeicherten Daten", "Delete my account", "DSGVO",
                     "Recht auf Auskunft: meine Daten", "right to be forgotten"):
            self.assertTrue(classify.is_data_protection("", text), msg=text)
        for text in ("Haben Sie Kapazitäten?", "Wann ist mein Termin?", "Rechnung 123 ist falsch"):
            self.assertFalse(classify.is_data_protection("", text), msg=text)


class PolicyTests(unittest.TestCase):
    def test_base_actions(self):
        expected = {"status_inquiry": "draft", "scheduling": "draft", "general_question": "draft",
                    "availability_request": "draft_flagged", "complaint": "draft_flagged",
                    "invoice_billing": "draft_flagged", "data_request": "escalate", "other": "escalate",
                    "spam": "ignore"}
        self.assertEqual(set(expected), set(policy.BASE_ACTION))
        for category, action in expected.items():
            self.assertEqual(policy.decide(category, 0.9, False, [])[0], action, msg=category)

    def test_legal_and_data_protection_always_escalate_whatever_the_category(self):
        for category in policy.BASE_ACTION:
            action, flags = policy.decide(category, 0.99, True, [])
            self.assertEqual((action, "legal" in flags), ("escalate", True), msg=category)
            action, flags = policy.decide(category, 0.99, False, [], data_protection=True)
            self.assertEqual((action, "data_protection" in flags), ("escalate", True), msg=category)

    def test_both_nets_add_both_flags(self):
        self.assertEqual(policy.decide("spam", 0.9, True, [], True), ("escalate", ["legal", "data_protection"]))

    def test_low_confidence_escalates_drafts_but_not_spam_or_data_requests(self):
        low = policy.MIN_CONFIDENCE - 0.01
        self.assertEqual(policy.decide("status_inquiry", low, False, []), ("escalate", ["low_confidence"]))
        self.assertEqual(policy.decide("invoice_billing", low, False, [])[0], "escalate")
        self.assertEqual(policy.decide("spam", low, False, [])[0], "ignore")
        self.assertEqual(policy.decide("other", 0.0, False, []), ("escalate", []))

    def test_confidence_threshold_is_inclusive(self):
        self.assertEqual(policy.decide("status_inquiry", policy.MIN_CONFIDENCE, False, [])[0], "draft")

    def test_a_reply_flag_downgrades_a_plain_draft(self):
        self.assertEqual(policy.decide("status_inquiry", 0.9, False, ["unknown_sender"]),
                         ("draft_flagged", ["unknown_sender"]))

    def test_availability_always_carries_the_commitment_flag(self):
        self.assertEqual(policy.decide("availability_request", 0.9, False, []), ("draft_flagged", ["commitment"]))
        self.assertEqual(policy.decide("availability_request", 0.9, False, ["no_capacity_match"])[1],
                         ["no_capacity_match", "commitment"])

    def test_the_input_flag_list_is_not_modified(self):
        flags = ["money"]
        policy.decide("invoice_billing", 0.9, True, flags, True)
        self.assertEqual(flags, ["money"])

    def test_one_month_deadline(self):
        self.assertEqual(policy.add_one_month(date(2026, 10, 12)), date(2026, 11, 12))
        self.assertEqual(policy.add_one_month(date(2026, 12, 15)), date(2027, 1, 15))
        self.assertEqual(policy.add_one_month(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(policy.add_one_month(date(2028, 1, 31)), date(2028, 2, 29))     # leap year
        self.assertEqual(policy.add_one_month(date(2026, 3, 31)), date(2026, 4, 30))
        self.assertEqual(policy.add_one_month(date(2026, 12, 31)), date(2027, 1, 31))

    def test_notes(self):
        notes = policy.notes_for("data_request", False, "2026-10-12T06:00:00Z")
        self.assertIn("due 2026-11-12", notes[0])
        self.assertEqual(policy.notes_for("status_inquiry", False, "2026-10-12T06:00:00Z"), [])
        self.assertEqual(len(policy.notes_for("invoice_billing", True, "")), 1)
        self.assertEqual(len(policy.notes_for("other", False, "2026-10-12T06:00:00Z", data_protection=True)), 1)
        self.assertNotIn(", due ", policy.notes_for("data_request", False, "")[0])


if __name__ == "__main__":
    unittest.main()
