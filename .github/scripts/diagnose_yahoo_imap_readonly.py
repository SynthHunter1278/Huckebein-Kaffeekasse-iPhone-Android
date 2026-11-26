#!/usr/bin/env python3
"""Read-only Yahoo IMAP diagnostics. Never marks messages read or changes repository data."""
import email
import imaplib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import process_sponsor_mail as processor


def uid_search(mail, criteria):
    status, data = mail.uid("search", None, criteria)
    if status != "OK":
        return None
    if not data or not data[0]:
        return []
    return [int(value) for value in data[0].split() if value.isdigit()]


def main():
    account = os.environ["YAHOO_EMAIL"]
    password = os.environ["YAHOO_APP_PASSWORD"]
    mail = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
    try:
        mail.login(account, password)
        list_status, folders = mail.list()
        if list_status != "OK" or not folders:
            raise RuntimeError("Yahoo-Ordnerliste konnte nicht abgerufen werden")
        candidates = []
        for raw in folders:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            end_quote = line.rfind('"')
            start_quote = line.rfind('"', 0, end_quote)
            mailbox_name = line[start_quote + 1:end_quote] if start_quote >= 0 and end_quote > start_quote else line.rsplit(" ", 1)[-1].strip('"')
            normalized = "".join(ch for ch in mailbox_name.casefold() if ch.isalnum())
            if "hanshuckebein" in normalized and "kaffeekasse" in normalized:
                candidates.append(mailbox_name)
        if not candidates:
            raise RuntimeError("Kein passender Yahoo-Kassenordner gefunden")
        # Bei mehreren Namens-Treffern jeden Kandidaten ausdrücklich readonly
        # prüfen und den Ordner mit den passenden Kassenmails bestimmen.
        candidate_counts = {}
        for candidate in candidates:
            candidate_status, _ = mail.select('"' + candidate + '"', readonly=True)
            if candidate_status != "OK":
                candidate_counts[candidate] = "nicht lesbar"
                continue
            matches = processor.search_all_relevant_uids(mail)
            candidate_counts[candidate] = len(matches)
            all_candidate_uids = uid_search(mail, "ALL")
            latest_date = "keine Nachrichten"
            if all_candidate_uids:
                latest_status, latest_data = mail.uid(
                    "fetch", str(all_candidate_uids[-1]), "(BODY.PEEK[HEADER.FIELDS (DATE SUBJECT)])"
                )
                if latest_status == "OK" and latest_data:
                    latest_raw = next(
                        (item[1] for item in latest_data if isinstance(item, tuple) and isinstance(item[1], bytes)),
                        None
                    )
                    if latest_raw:
                        latest_header = email.message_from_bytes(latest_raw)
                        latest_date = str(latest_header.get("Date", "Datum unbekannt"))
            print(
                "MAILBOX_CANDIDATE"
                + " | TOTAL_MESSAGES=" + str(len(all_candidate_uids or []))
                + " | RELEVANT_MESSAGES=" + str(len(matches))
                + " | LATEST_DATE=" + latest_date
            )
        populated = [name for name, count in candidate_counts.items() if isinstance(count, int) and count > 0]
        if len(populated) == 1:
            mailbox = populated[0]
        elif len(candidates) == 1:
            mailbox = candidates[0]
        else:
            details = "; ".join(f"{name}: {count}" for name, count in candidate_counts.items())
            raise RuntimeError("Yahoo-Ordner nicht eindeutig; passende Nachrichten je Ordner: " + details)
        status, count_data = mail.select('"' + mailbox + '"', readonly=True)
        if status != "OK":
            raise RuntimeError("Kassenordner konnte nicht schreibgeschützt geöffnet werden")
        print("MAILBOX: Kassenordner gefunden und readonly ausgewählt")
        print("MESSAGES: " + (count_data[0].decode("ascii", "replace") if count_data and count_data[0] else "unbekannt"))
        print("UIDVALIDITY: " + str(processor.get_uidvalidity(mail)))

        all_uids = uid_search(mail, "ALL")
        unseen_uids = uid_search(mail, "UNSEEN")
        if all_uids is None or unseen_uids is None:
            raise RuntimeError("Yahoo UID SEARCH ALL/UNSEEN fehlgeschlagen")
        print("UID_SEARCH_ALL_COUNT: " + str(len(all_uids)))
        print("UID_SEARCH_UNSEEN_COUNT: " + str(len(unseen_uids)))
        print("UID_SEARCH_ALL_LATEST: " + (",".join(map(str, all_uids[-15:])) if all_uids else "(leer)"))

        for criteria in processor.IMAP_RELEVANT_SEARCHES:
            matches = uid_search(mail, criteria)
            if matches is None:
                print("RELEVANT_SEARCH_ERROR: " + criteria)
            else:
                print("RELEVANT_SEARCH_COUNT: " + str(len(matches)) + " | " + criteria)

        relevant = processor.search_all_relevant_uids(mail)
        print("RELEVANT_UIDS_LATEST: " + (",".join(map(str, relevant[-50:])) if relevant else "(leer)"))
        for uid in relevant[-50:]:
            fetch_status, fetched = mail.uid("fetch", str(uid), "(BODY.PEEK[])")
            if fetch_status != "OK" or not fetched:
                print("MESSAGE UID " + str(uid) + ": FETCH_ERROR")
                continue
            raw = next((item[1] for item in fetched if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if raw is None:
                print("MESSAGE UID " + str(uid) + ": NO_RAW_MESSAGE")
                continue
            msg = email.message_from_bytes(raw)
            recipient_ok = processor.is_message_for_account(msg, account)
            breakfast, breakfast_reason = processor.parse_breakfast_booking(msg, uid)
            sponsor, sponsor_reason = processor.parse_request(msg, uid)
            subject = processor.decode_header_value(msg.get("Subject", ""))
            subject_upper = subject.upper().replace("Ü", "UE")
            kinds = []
            if "FRUEHSTUECKSBUCHUNG" in subject_upper or "FREITAGSFRUEHSTUECK" in subject_upper:
                kinds.append("BREAKFAST_SUBJECT")
            if "SPENDERANMELDUNG" in subject_upper and "KAFFEE SPENDIEREN" in subject_upper:
                kinds.append("SPONSOR_SUBJECT")
            body_upper = processor.message_text(msg).upper().replace("Ü", "UE")
            safe_flags = {
                "SUBJECT_KAFFEEKASSE": "KAFFEEKASSE" in subject_upper,
                "SUBJECT_BREAKFAST": "FRUEHSTUECKSBUCHUNG" in subject_upper or "FREITAGSFRUEHSTUECK" in subject_upper,
                "SUBJECT_SPONSOR": "SPENDERANMELDUNG" in subject_upper,
                "BODY_KAFFEEKASSE": "KAFFEEKASSE" in body_upper,
                "BODY_BREAKFAST": "FRUEHSTUECKSBUCHUNG" in body_upper or "FREITAGSFRUEHSTUECK" in body_upper,
                "BODY_SPONSOR": "SPENDERANMELDUNG" in body_upper,
            }
            print(
                "MESSAGE UID " + str(uid)
                + " | RECIPIENT_MATCH=" + str(recipient_ok)
                + " | DATE=" + str(msg.get("Date", ""))
                + " | FLAGS=" + str(safe_flags)
                + " | BODY_CHARS=" + str(len(body_upper))
                + " | BREAKFAST=" + ("PARSED" if breakfast else breakfast_reason)
                + " | SPONSOR=" + ("PARSED" if sponsor else sponsor_reason)
            )
        print("DIAGNOSTIC_FINISHED: no messages marked read; no app data or state files changed")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
