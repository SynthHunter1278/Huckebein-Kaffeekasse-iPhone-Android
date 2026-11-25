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
    def test_monthly_quota_counts_only_current_month_kasse_emails(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 24, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        current = make_mail(
            "KAFFEEKASSE – FRÜHSTÜCKSBUCHUNG",
            "item: Freitagsfrühstück",
        )
        current.replace_header("Date", "Tue, 24 Nov 2026 11:00:00 +0100")
        current.replace_header("To", "Kaffeekasse <toeber2000@yahoo.de>")
        previous = make_mail("KAFFEEKASSE – TEST", "item: test")
        previous.replace_header("Date", "Sat, 31 Oct 2026 11:00:00 +0200")
        previous.replace_header("To", "Kaffeekasse <toeber2000@yahoo.de>")
        unrelated = make_mail("Private Nachricht", "Hallo")
        unrelated.replace_header("Date", "Tue, 24 Nov 2026 11:00:00 +0100")
        unrelated.replace_header("To", "Kaffeekasse <toeber2000@yahoo.de>")
        self.assertTrue(sponsor_mail.is_email_from_this_month(current, now, "toeber2000@yahoo.de"))
        self.assertFalse(sponsor_mail.is_email_from_this_month(previous, now, "toeber2000@yahoo.de"))
        self.assertFalse(sponsor_mail.is_email_from_this_month(unrelated, now, "toeber2000@yahoo.de"))

    def test_notification_is_trusted_by_recipient_mailbox_not_sender(self):
        msg = make_mail("KAFFEEKASSE – BUCHUNG", "user: Testperson", sender="teilnehmer@example.org")
        msg.replace_header("To", "Kassenverwaltung <toeber2000@yahoo.de>")
        self.assertTrue(sponsor_mail.is_message_for_account(msg, "toeber2000@yahoo.de"))
        self.assertFalse(sponsor_mail.is_message_for_account(msg, "anderes-postfach@yahoo.de"))

    def test_sender_is_parsed_and_normalized(self):
        msg = make_mail("Test", "Text")
        self.assertEqual(
            sponsor_mail.parseaddr(msg.get("From", ""))[1].strip().casefold(),
            "toeber2000@yahoo.de",
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

    def test_friday_breakfast_booking(self):
        msg = make_mail(
            "KAFFEEKASSE – FRÜHSTÜCKSBUCHUNG",
            "item: Freitagsfrühstück\nuser: Testperson\ntimestamp: 16.11.2026, 12:00:00\nnotification_id: BREAKFAST-123",
        )
        entry, reason = sponsor_mail.parse_breakfast_booking(msg, 301)
        self.assertEqual(reason, "")
        self.assertEqual(entry["name"], "Testperson")
        self.assertEqual(entry["item"], "Freitagsfrühstück")
        self.assertEqual(entry["notification_id"], "BREAKFAST-123")


    def test_unrelated_app_source_with_breakfast_keyword_is_not_a_booking(self):
        msg = make_mail(
            "KAFFEEKASSE – BUCHUNG",
            "item: Automaten Kaffee, preis: 0.50 }" + "\\n" +
            "timestamp: 26.10.2026, 09:20:39" + "\\n" +
            "const FRUEHSTUECKSBUCHUNG = true; user: Testperson"
        )
        entry, reason = sponsor_mail.parse_breakfast_booking(msg, 778)
        self.assertIsNone(entry)

    def test_free_coffee_redemption_during_sponsor_action(self):
        msg = make_mail(
            "☕ KAFFEEKASSE – KAFFEEAKTION – GRATISENTNAHME",
            "item: KAFFEEAKTION GRATISENTNAHME | 1 Automatenkaffee | Aktions-ID: KAFFEE-20261116-123\n"
            "user: Testperson\ntimestamp: 16.11.2026, 12:00:00\n"
            "notification_id: FREE-COFFEE-123",
        )
        entry, reason = sponsor_mail.parse_free_coffee_redemption(msg, 501)
        self.assertEqual(reason, "")
        self.assertEqual(entry["name"], "Testperson")
        self.assertEqual(entry["notification_id"], "FREE-COFFEE-123")
        self.assertEqual(entry["action_id"], "KAFFEE-20261116-123")
        self.assertEqual(entry["item"], "KAFFEEAKTION GRATISENTNAHME | 1 Automatenkaffee")


    def test_inline_emailjs_fields_are_readable(self):
        msg = make_mail(
            "KAFFEEKASSE – KAFFEEAKTION – GRATISENTNAHME",
            "item: KAFFEEAKTION GRATISENTNAHME | Aktions-ID: KAFFEE-20261125-ABC12345 | user: Testperson | timestamp: 25.11.2026, 12:00:00 | notification_id: FREE-123"
        )
        entry, reason = sponsor_mail.parse_free_coffee_redemption(msg, 777)
        self.assertEqual(reason, "")
        self.assertEqual(entry["name"], "Testperson")
        self.assertEqual(entry["notification_id"], "FREE-123")

    def test_free_coffee_without_campaign_id_is_not_counted(self):
        msg = make_mail(
            "KAFFEEKASSE – KAFFEEAKTION – GRATISENTNAHME",
            "item: KAFFEEAKTION GRATISENTNAHME | 1 Automatenkaffee\n"
            "user: Testperson\ntimestamp: 16.11.2026, 12:00:00",
        )
        entry, reason = sponsor_mail.parse_free_coffee_redemption(msg, 503)
        self.assertIsNone(entry)

    def test_other_free_coffee_mail_is_not_counted(self):
        msg = make_mail("KAFFEEKASSE – BUCHUNG", "item: Normaler Kaffee\nuser: Testperson")
        entry, reason = sponsor_mail.parse_free_coffee_redemption(msg, 502)
        self.assertIsNone(entry)

    def test_paypal_paid_decision(self):
        msg = make_mail(
            "KAFFEEKASSE - PAYPAL-AUFLADUNG",
            "user: KAFFEEKASSE – Testperson – ZAHLUNG VOM TEILNEHMER ALS BEZAHLT BESTÄTIGT\n"
            "amount: 10,00 €\ntimestamp: 16.11.2026, 12:00:00\n"
            "status: BEZAHLT\nrequest_id: PP-123456",
        )
        entry, reason = sponsor_mail.parse_paypal_decision(msg, 401)
        self.assertEqual(reason, "")
        self.assertEqual(entry["name"], "Testperson")
        self.assertEqual(entry["amount"], 10.0)
        self.assertEqual(entry["status"], "BEZAHLT")

    def test_paypal_not_paid_decision(self):
        msg = make_mail(
            "KAFFEEKASSE - PAYPAL-AUFLADUNG",
            "user: KAFFEEKASSE – Testperson – ZAHLUNG VOM TEILNEHMER ALS NICHT BEZAHLT BESTÄTIGT\n"
            "amount: 5,00 €\ntimestamp: 16.11.2026, 12:00:00",
        )
        entry, reason = sponsor_mail.parse_paypal_decision(msg, 402)
        self.assertEqual(reason, "")
        self.assertEqual(entry["status"], "NICHT BEZAHLT")
        self.assertEqual(entry["amount"], 5.0)

    def test_unrelated_mail_is_ignored(self):
        msg = make_mail("Private Nachricht", "Keine Anmeldung")
        request, reason = sponsor_mail.parse_request(msg, 103)
        self.assertIsNone(request)
        self.assertEqual(reason, "Keine Spenderanmeldung")

    def test_signup_window_boundaries(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
        now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=tz)
        cases = [
            (datetime(2026, 11, 16, 5, 59, 59, tzinfo=tz), False),
            (datetime(2026, 11, 16, 6, 0, 0, tzinfo=tz), True),
            (datetime(2026, 11, 16, 14, 59, 59, tzinfo=tz), True),
            (datetime(2026, 11, 16, 15, 0, 0, tzinfo=tz), False),
            (datetime(2026, 11, 15, 12, 0, 0, tzinfo=tz), False),
            (datetime(2026, 11, 17, 12, 0, 0, tzinfo=tz), False),
        ]
        for request_time, expected in cases:
            with self.subTest(request_time=request_time):
                self.assertEqual(
                    sponsor_mail.is_signup_time_valid(request_time, now),
                    expected,
                )

    def test_automatic_settlement_is_created_after_1500_from_matching_redemptions(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
        config = {
            "preise": {"btn-kaffee": 0.5},
            "geburtstag": {
                "aktiv": True,
                "spender": "Testspender",
                "datum": "2026-11-25",
                "aktionsId": "KAFFEE-20261125-ABC12345",
                "automatischFreigegeben": True,
                "abrechnung": {"id": "MANUELLER-TEST", "anzahl": 12, "gesamt": 6, "status": "offen"}
            }
        }
        logs = {"entries": [
            {"id": "FREE-1", "action_id": "KAFFEE-20261125-ABC12345"},
            {"id": "FREE-2", "action_id": "KAFFEE-20261125-ABC12345"},
            {"id": "OTHER", "action_id": "KAFFEE-ANDERE-AKTION"}
        ]}
        before = datetime(2026, 11, 25, 14, 59, tzinfo=tz)
        self.assertFalse(sponsor_mail.finalize_birthday_settlement(config, logs, before))
        after = datetime(2026, 11, 25, 15, 5, tzinfo=tz)
        self.assertTrue(sponsor_mail.finalize_birthday_settlement(config, logs, after))
        settlement = config["geburtstag"]["abrechnung"]
        self.assertEqual(settlement["anzahl"], 2)
        self.assertEqual(settlement["gesamt"], 1.0)
        self.assertEqual(settlement["status"], "offen")
        self.assertTrue(settlement["automatischErstellt"])
        self.assertFalse(config["geburtstag"]["aktiv"])
        self.assertFalse(sponsor_mail.finalize_birthday_settlement(config, logs, after))

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
            ("search", None, sponsor_mail.IMAP_RELEVANT_SEARCH),
        )


if __name__ == "__main__":
    unittest.main()
