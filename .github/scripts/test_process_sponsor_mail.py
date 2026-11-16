import importlib.util
import unittest
from email.message import EmailMessage
from pathlib import Path

SCRIPT = Path(__file__).with_name("process_sponsor_mail.py")
SPEC = importlib.util.spec_from_file_location("sponsor_mail", SCRIPT)
sponsor_mail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sponsor_mail)


def make_mail(subject, body, sender="Toeber2000@yahoo.de"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "test@example.com"
    msg["Date"] = "Mon, 16 Nov 2026 12:00:00 +0100"
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


class SponsorMailTests(unittest.TestCase):
    def test_sender_is_parsed_and_normalized(self):
        msg = make_mail("Test", "Text")
        self.assertEqual(
            sponsor_mail.parseaddr(msg.get("From", ""))[1].strip().casefold(),
            sponsor_mail.ALLOWED_SENDER,
        )

    def test_valid_sponsor_request(self):
        msg = make_mail(
            "KAFFEEKASSE - KAFFEE SPENDIEREN - SPENDERANMELDUNG",
            "Anmelde-ID: request-12345678\nuser: Testperson\ntimestamp: 16.11.2026, 12:00:00",
        )
        request, reason = sponsor_mail.parse_request(msg, 101)
        self.assertEqual(reason, "")
        self.assertIsNotNone(request)
        self.assertEqual(request["request_id"], "request-12345678")
        self.assertEqual(request["name"], "Testperson")
        self.assertEqual(request["requested_at"].hour, 12)

    def test_valid_settlement_confirmation(self):
        msg = make_mail(
            "KAFFEEKASSE - ABBUCHUNG BESTÄTIGT",
            "Abrechnungs-ID: KAFFEE-20261116-1794840389876\nuser: Testperson",
        )
        confirmation, reason = sponsor_mail.parse_confirmation(msg, 102)
        self.assertEqual(reason, "")
        self.assertIsNotNone(confirmation)
        self.assertEqual(
            confirmation["settlement_id"],
            "KAFFEE-20261116-1794840389876",
        )
        self.assertEqual(confirmation["name"], "Testperson")

    def test_unrelated_mail_is_ignored(self):
        msg = make_mail("Private Nachricht", "Keine Anmeldung")
        request, reason = sponsor_mail.parse_request(msg, 103)
        self.assertIsNone(request)
        self.assertEqual(reason, "Keine Spenderanmeldung")

    def test_search_is_limited_to_coffee_kasse_messages(self):
        class FakeMail:
            def __init__(self):
                self.args = None

            def uid(self, *args):
                self.args = args
                return "OK", [b"10 11 25"]

        fake = FakeMail()
        found = sponsor_mail.search_relevant_uids(fake, 10)
        self.assertEqual(found, [11, 25])
        self.assertEqual(
            fake.args,
            ("search", None, 'OR SUBJECT "KAFFEEKASSE" TEXT "KAFFEEKASSE"'),
        )


if __name__ == "__main__":
    unittest.main()
