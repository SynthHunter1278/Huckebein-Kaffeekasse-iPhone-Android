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
        previous = make_mail("KAFFEEKASSE – TEST", "item: test")
        previous.replace_header("Date", "Sat, 31 Oct 2026 11:00:00 +0200")
        unrelated = make_mail("Private Nachricht", "Hallo")
        unrelated.replace_header("Date", "Tue, 24 Nov 2026 11:00:00 +0100")
        self.assertTrue(sponsor_mail.is_email_from_this_month(current, now))
        self.assertFalse(sponsor_mail.is_email_from_this_month(previous, now))
        self.assertFalse(sponsor_mail.is_email_from_this_month(unrelated, now))

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
        self.assertEqual(entry["item"], "KAFFEEAKTION GRATISENTNAHME | 1 Automatenkaffee | Aktions-ID: KAFFEE-20261116-123")


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
            "user: Testperson – ZAHLUNG VOM TEILNEHMER ALS BEZAHLT BESTÄTIGT\n"
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
            "user: Testperson – ZAHLUNG VOM TEILNEHMER ALS NICHT BEZAHLT BESTÄTIGT\n"
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

    def test_monthly_quota_skips_one_unreadable_header_without_aborting(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        class FakeMail:
            def uid(self, *args):
                if args[0] == "search":
                    return "OK", [b"10 11 12"]
                uid = args[1]
                if uid == "11":
                    return "NO", [b"header unavailable"]
                msg = make_mail("KAFFEEKASSE - TEST", "test")
                msg.replace_header("Date", "Tue, 24 Nov 2026 11:00:00 +0100")
                return "OK", [(b"BODY[HEADER]", msg.as_bytes())]

        now = datetime(2026, 11, 24, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertIsNone(sponsor_mail.count_monthly_email_notifications(FakeMail(), now))

    def test_paypal_code_formula_matches_cent_based_validator(self):
        multiplier = 1307
        day_multiplier = 713
        offset = 418952
        day = 25
        for euros in (5, 10, 15):
            code = round(euros * 100) * multiplier + day * day_multiplier + offset
            raw_cents = (code - offset - day * day_multiplier) / multiplier
            amount_cents = round(raw_cents)
            expected_code = amount_cents * multiplier + day * day_multiplier + offset
            self.assertEqual(code, expected_code)
            self.assertEqual(amount_cents / 100, euros)


if __name__ == "__main__":
    unittest.main()
