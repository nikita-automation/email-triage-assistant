import os
import tempfile
import unittest

from mailtriage.mailparse import detect_language, parse_dir, parse_file
from helpers import SAMPLE_INBOX


def write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class LanguageTests(unittest.TestCase):
    def test_german_and_english(self):
        self.assertEqual(detect_language("Guten Tag, ich habe mich beworben und bitte um eine Antwort"), "de")
        self.assertEqual(detect_language("Hello, I applied last week and would like to know what the status is"), "en")

    def test_ties_and_empty_text_go_to_german(self):
        self.assertEqual(detect_language(""), "de")
        self.assertEqual(detect_language("12345"), "de")


class ParseFileTests(unittest.TestCase):
    def test_every_sample_mail_is_readable(self):
        emails, errors = parse_dir(SAMPLE_INBOX)
        self.assertEqual(errors, [])
        self.assertEqual(len(emails), 16)
        self.assertEqual(len({e.source_id for e in emails}), 16)

    def test_encoded_display_name_and_subject_are_decoded(self):
        by_name = {os.path.basename(e.path): e for e in parse_dir(SAMPLE_INBOX)[0]}
        clara = by_name["04_scheduling_clara_de.eml"]
        self.assertEqual(clara.from_name, "Clara Müller")
        self.assertEqual(clara.from_addr, "clara.mueller@example.org")
        self.assertEqual(by_name["07_gdpr_de.eml"].subject, "Löschung meiner Daten")

    def test_date_is_utc_and_language_is_detected(self):
        by_name = {os.path.basename(e.path): e for e in parse_dir(SAMPLE_INBOX)[0]}
        self.assertEqual(by_name["01_status_anna_de.eml"].date, "2026-10-12T06:14:02Z")     # 08:14 +02:00
        self.assertEqual(by_name["01_status_anna_de.eml"].language, "de")
        self.assertEqual(by_name["02_status_ben_en.eml"].language, "en")

    def test_html_only_mail_is_reduced_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "h.eml", 'From: A B <a@example.org>\nSubject: x\nMessage-ID: <h@x>\n'
                                       'Content-Type: text/html; charset="utf-8"\n\n'
                                       '<html><body><p>Hallo <b>Welt</b></p><p>bitte um Rückruf</p></body></html>\n')
            email = parse_file(path)
        self.assertEqual(email.body, "Hallo Welt bitte um Rückruf")

    def test_multipart_prefers_the_plain_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "m.eml",
                         'From: A B <a@example.org>\nSubject: x\nMessage-ID: <m@x>\nMIME-Version: 1.0\n'
                         'Content-Type: multipart/alternative; boundary="b"\n\n'
                         '--b\nContent-Type: text/plain; charset="utf-8"\n\nplain text\n'
                         '--b\nContent-Type: text/html; charset="utf-8"\n\n<p>html text</p>\n--b--\n')
            self.assertEqual(parse_file(path).body, "plain text")

    def test_threading_headers_and_reply_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "t.eml", "From: A <a@example.org>\nReply-To: Boss <boss@example.org>\nSubject: x\n"
                                       "Message-ID: <t@x>\nIn-Reply-To: <p@x>\nReferences: <r1@x> <p@x>\n\nbody\n")
            email = parse_file(path)
        self.assertEqual(email.reply_to, "boss@example.org")
        self.assertEqual(email.in_reply_to, "<p@x>")
        self.assertEqual(email.references, ["<r1@x>", "<p@x>"])

    def test_source_id_is_stable_and_falls_back_to_content_without_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write(tmp, "a.eml", "From: A <a@example.org>\nSubject: x\n\nbody one\n")
            b = write(tmp, "b.eml", "From: A <a@example.org>\nSubject: x\n\nbody two\n")
            self.assertEqual(parse_file(a).source_id, parse_file(a).source_id)
            self.assertNotEqual(parse_file(a).source_id, parse_file(b).source_id)

    def test_unreadable_files_are_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "good.eml", "From: A <a@example.org>\nSubject: x\n\nbody\n")
            write(tmp, "nosender.eml", "Subject: no sender\n\nbody\n")
            write(tmp, "notes.txt", "ignored")
            emails, errors = parse_dir(tmp)
        self.assertEqual(len(emails), 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("nosender.eml"))


if __name__ == "__main__":
    unittest.main()
