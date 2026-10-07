import os
import tempfile
import unittest
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from unittest import mock

from mailtriage import replies
from mailtriage.drafts import BANNER, ImapDraftStore, MaildirDraftStore, build_draft
from helpers import load_kb, make_email

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def facts_for(email):
    return replies.gather_facts(email, load_kb())


class FormatTests(unittest.TestCase):
    def test_dates_and_slots(self):
        self.assertEqual(replies.format_date("2026-11-09", "de"), "09.11.2026")
        self.assertEqual(replies.format_date("2026-11-09", "en"), "9 November 2026")
        self.assertEqual(replies.format_slot("2026-10-15T14:00", "de"), "Do. 15.10.2026, 14:00 Uhr")
        self.assertEqual(replies.format_slot("2026-10-15T14:00", "en"), "Thu 15 October 2026, 14:00")


class FactsTests(unittest.TestCase):
    def test_inflected_keywords_match_but_substrings_do_not(self):
        self.assertTrue(replies._mentions("We need 3 packers", "packer"))
        self.assertTrue(replies._mentions("Is the office opening soon?", "open"))
        self.assertTrue(replies._mentions("Wir suchen eine Packerin", "packer"))
        self.assertFalse(replies._mentions("a wallpaper", "paper "))
        self.assertFalse(replies._mentions("The autopilot", "auto"))          # 'autopilot' is not an inflection
        self.assertFalse(replies._mentions("Stapelware", "stapler"))

    def test_person_capacity_faq_and_slots(self):
        email = make_email(subject="Staffing", body="3 packers and a forklift driver; opening hours?",
                           from_addr="eva.probe@kunde-probe.example.org", date="2026-10-14T08:00:00Z")
        facts = facts_for(email)
        self.assertEqual(facts["person"]["name"], "Kunde Probe Ltd.")
        self.assertEqual({c["label_en"] for c in facts["capacity"]}, {"packers", "forklift drivers"})
        self.assertEqual(len(facts["faq"]), 1)
        self.assertEqual(facts["slots"], ["2026-10-15T09:00", "2026-10-15T14:00", "2026-10-16T10:00"])

    def test_slots_start_after_the_day_the_mail_arrived(self):
        early = facts_for(make_email(date="2026-10-14T08:00:00Z"))["slots"]
        late = facts_for(make_email(date="2026-10-15T08:00:00Z"))["slots"]
        self.assertIn("2026-10-15T09:00", early)
        self.assertNotIn("2026-10-15T09:00", late)             # same day is not 'after'
        self.assertEqual(facts_for(make_email(date="2026-12-01T08:00:00Z"))["slots"], [])


class GreetingTests(unittest.TestCase):
    def greeting(self, email):
        return replies._greeting(email, facts_for(email))

    def test_full_sender_name_is_used(self):
        self.assertEqual(self.greeting(make_email(from_name="Clara Müller", language="de")), "Guten Tag Clara Müller,")
        self.assertEqual(self.greeting(make_email(from_name="Ben Muster", language="en")), "Hello Ben Muster,")

    def test_customer_company_name_from_the_knowledge_base_is_not_a_greeting(self):
        email = make_email(from_name="Buchhaltung", from_addr="buchhaltung@kunde-demo.example.net")
        self.assertEqual(self.greeting(email), "Guten Tag,")

    def test_applicant_name_from_the_knowledge_base_is_used_when_the_header_has_none(self):
        email = make_email(from_name="", from_addr="anna.beispiel@example.com")
        self.assertEqual(self.greeting(email), "Guten Tag Anna Beispiel,")

    def test_unknown_single_word_sender(self):
        self.assertEqual(self.greeting(make_email(from_name="Tom", language="en")), "Hello,")


class ComposeTests(unittest.TestCase):
    def reply(self, category, **kwargs):
        email = make_email(**kwargs)
        return replies.compose(email, category, facts_for(email))

    def test_categories_without_a_draft(self):
        for category in ("data_request", "spam", "other"):
            self.assertEqual(self.reply(category), (None, []))

    def test_status_for_a_known_applicant_states_only_the_recorded_fact(self):
        text, flags = self.reply("status_inquiry", from_addr="ben.muster@example.org", from_name="Ben Muster",
                                 language="en")
        self.assertEqual(flags, [])
        self.assertIn("being reviewed", text)
        text, flags = self.reply("status_inquiry", from_addr="anna.beispiel@example.com", from_name="Anna Beispiel")
        self.assertIn("Mittwoch, 14.10.2026, 10:00 Uhr", text)

    def test_status_for_an_unknown_sender_promises_nothing_and_flags(self):
        text, flags = self.reply("status_inquiry", from_addr="nobody@example.org", from_name="")
        self.assertEqual(flags, ["unknown_sender"])
        self.assertNotIn("Uhr", text)
        self.assertNotIn("geprüft", text)

    def test_scheduling_offers_the_calendar_slots_and_nothing_else(self):
        text, flags = self.reply("scheduling", from_addr="clara.mueller@example.org", from_name="Clara Müller")
        self.assertEqual(flags, [])
        self.assertEqual(text.count("\n- "), 3)
        self.assertIn("Do. 15.10.2026, 09:00 Uhr", text)
        text, flags = self.reply("scheduling", date="2026-12-01T08:00:00Z")
        self.assertIn("no_slots", flags)
        self.assertNotIn("\n- ", text)

    def test_availability_quotes_the_knowledge_base_and_marks_it_non_binding(self):
        text, flags = self.reply("availability_request", subject="Staffing", body="We need packers and a forklift driver",
                                 from_addr="eva.probe@kunde-probe.example.org", from_name="Eva Probe", language="en")
        self.assertIn("packers: 2, from 16 November 2026", text)
        self.assertIn("forklift drivers: 2, from 9 November 2026", text)
        self.assertIn("non-binding", text)
        self.assertIn("Eva Demo will contact you", text)
        self.assertEqual(flags, [])                     # 'commitment' is added by the policy, not here

    def test_availability_without_a_match_promises_nothing(self):
        text, flags = self.reply("availability_request", body="We need 10 welders", language="en")
        self.assertIn("no_capacity_match", flags)
        self.assertNotIn("non-binding", text)

    def test_complaint_and_invoice_are_acknowledgements_that_commit_to_nothing(self):
        text, flags = self.reply("complaint", from_addr="einkauf@logistik-probe.example.net", from_name="J. Fiktiv")
        self.assertEqual(flags, ["complaint"])
        self.assertIn("Eva Demo meldet sich bei Ihnen", text)
        self.assertNotIn("Gutschrift", text)
        text, flags = self.reply("invoice_billing", language="en")
        self.assertEqual(flags, ["money"])
        self.assertNotIn("credit", text.lower())
        self.assertNotIn("refund", text.lower())

    def test_general_question_answers_from_the_faq_or_flags(self):
        text, flags = self.reply("general_question", body="Wann haben Sie geöffnet? Gibt es einen Parkplatz?")
        self.assertEqual(flags, [])
        self.assertIn("08:00 bis 17:00", text)
        self.assertIn("Besucherparkplätze", text)
        text, flags = self.reply("general_question", body="Haben Sie eine Kantine?")
        self.assertEqual(flags, ["no_faq_match"])

    def test_language_follows_the_mail(self):
        text, _ = self.reply("general_question", body="opening hours?", language="en")
        self.assertIn("Kind regards", text)
        text, _ = self.reply("general_question", body="Öffnungszeiten?", language="de")
        self.assertIn("Mit freundlichen Grüßen", text)


class BuildDraftTests(unittest.TestCase):
    def draft(self, **kwargs):
        email = make_email(subject="Terminverschiebung", body="Zeile 1\nZeile 2", from_name="Clara Müller",
                           from_addr="clara.mueller@example.org", **kwargs)
        return email, build_draft(email, "Antworttext", "scheduling", 0.91, ["money"], "jobs@example.com", NOW)

    def test_headers_threading_and_metadata(self):
        email, message = self.draft()
        self.assertEqual(message["Subject"], "Re: Terminverschiebung")
        self.assertEqual(message["From"], "jobs@example.com")
        self.assertIn("clara.mueller@example.org", message["To"])
        self.assertEqual(message["In-Reply-To"], "<m1@example.org>")
        self.assertEqual(message["References"], "<m1@example.org>")
        self.assertEqual(message["X-Triage-Source-Id"], "abc123")
        self.assertEqual(message["X-Triage-Category"], "scheduling")
        self.assertEqual(message["X-Triage-Confidence"], "0.91")
        self.assertEqual(message["X-Triage-Flags"], "money")
        self.assertEqual(message["Message-ID"], "<draft-abc123@mailtriage.local>")

    def test_existing_reply_prefix_is_not_doubled(self):
        for subject in ("Re: Frage", "AW: Frage", "re: Frage"):
            email = make_email(subject=subject)
            message = build_draft(email, "x", "other", 0.5, [], "jobs@example.com", NOW)
            self.assertEqual(message["Subject"], subject)

    def test_banner_comes_first_and_the_original_is_quoted(self):
        _, message = self.draft()
        body = message.get_content()
        self.assertTrue(body.startswith(BANNER["de"]))
        self.assertIn("> Zeile 1", body)
        self.assertIn("Am 2026-10-12 schrieb Clara Müller:", body)

    def test_no_flags_is_written_as_none_and_missing_message_id_is_survivable(self):
        email = make_email(message_id="")
        message = build_draft(email, "x", "other", 0.5, [], "jobs@example.com", NOW)
        self.assertEqual(message["X-Triage-Flags"], "none")
        self.assertIsNone(message["In-Reply-To"])

    def test_long_quotes_are_cut_after_15_lines(self):
        email = make_email(body="\n".join("zeile %d" % i for i in range(40)))
        body = build_draft(email, "x", "other", 0.5, [], "jobs@example.com", NOW).get_content()
        self.assertIn("> zeile 14", body)
        self.assertNotIn("> zeile 15", body)


class MaildirTests(unittest.TestCase):
    def test_save_is_atomic_flagged_as_draft_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MaildirDraftStore(tmp)
            email = make_email()
            message = build_draft(email, "x", "other", 0.5, [], "jobs@example.com", NOW)
            self.assertEqual(store.save(message, "abc123"), "created")
            self.assertEqual(store.save(message, "abc123"), "exists")
            self.assertEqual(store.save(message, "other-id"), "created")
            files = os.listdir(os.path.join(tmp, "Drafts", "cur"))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(name.endswith(":2,DS") for name in files))
            self.assertEqual(os.listdir(os.path.join(tmp, "Drafts", "tmp")), [])
            with open(os.path.join(tmp, "Drafts", "cur", files[0]), "rb") as handle:
                parsed = BytesParser(policy=email_policy.default).parse(handle)
            self.assertEqual(parsed["X-Draft-Source"], "mailtriage")

    def test_a_draft_survives_a_new_store_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = build_draft(make_email(), "x", "other", 0.5, [], "jobs@example.com", NOW)
            MaildirDraftStore(tmp).save(message, "abc123")
            self.assertEqual(MaildirDraftStore(tmp).save(message, "abc123"), "exists")


class FakeImap:
    def __init__(self, existing=b""):
        self.calls, self.existing = [], existing

    def login(self, user, password):
        self.calls.append(("login", user))
        return "OK", []

    def select(self, folder):
        self.calls.append(("select", folder))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.calls.append(("search",) + criteria)
        return "OK", [self.existing]

    def append(self, folder, flags, date_time, payload):
        self.calls.append(("append", folder, flags, payload))
        return "OK", []

    def logout(self):
        self.calls.append(("logout",))


class ImapTests(unittest.TestCase):
    def store(self, fake):
        return ImapDraftStore("imap.example.com", "user", "secret", "Drafts", connect=lambda host, port: fake)

    def test_appends_with_the_draft_flag(self):
        fake = FakeImap()
        store = self.store(fake)
        message = build_draft(make_email(), "x", "other", 0.5, [], "jobs@example.com", NOW)
        self.assertEqual(store.save(message, "abc123"), "created")
        store.close()
        append = [c for c in fake.calls if c[0] == "append"][0]
        self.assertEqual(append[1:3], ("Drafts", "\\Draft"))
        self.assertIn(b"X-Triage-Source-Id: abc123", append[3])
        self.assertEqual(fake.calls[-1], ("logout",))

    def test_an_existing_draft_is_not_appended_again(self):
        fake = FakeImap(existing=b"7")
        message = build_draft(make_email(), "x", "other", 0.5, [], "jobs@example.com", NOW)
        self.assertEqual(self.store(fake).save(message, "abc123"), "exists")
        self.assertFalse([c for c in fake.calls if c[0] == "append"])
        self.assertIn(("search", "HEADER", "X-Triage-Source-Id", "abc123"), fake.calls)

    def test_connection_is_reused_and_login_happens_once(self):
        fake = FakeImap()
        store = self.store(fake)
        message = build_draft(make_email(), "x", "other", 0.5, [], "jobs@example.com", NOW)
        store.save(message, "a")
        store.save(message, "b")
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "login"), 1)

    def test_unknown_folder_is_an_error(self):
        fake = FakeImap()
        fake.select = lambda folder: ("NO", [b"nope"])
        with self.assertRaisesRegex(RuntimeError, "cannot open IMAP folder"):
            self.store(fake).save(build_draft(make_email(), "x", "other", 0.5, [], "jobs@example.com", NOW), "a")

    def test_there_is_no_way_to_send(self):
        import mailtriage
        root = os.path.dirname(mailtriage.__file__)
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as handle:
                    source = handle.read()
                self.assertNotIn("smtplib", source, msg=name)
                self.assertNotIn("sendmail", source, msg=name)


if __name__ == "__main__":
    unittest.main()
