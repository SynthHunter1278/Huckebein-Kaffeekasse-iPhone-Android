#!/usr/bin/env python3
"""Process Yahoo EmailJS coffee-sponsor requests and update config.json safely."""
import email
import imaplib
import json
import os
import re
import subprocess
from datetime import datetime, time, timedelta
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config.json"
STATE = ROOT / ".github" / "state" / "sponsor-mail-state.json"
TZ = ZoneInfo("Europe/Berlin")
START = time(6, 0)
END = time(15, 0)
ALLOWED_SENDER = "toeber2000@yahoo.de"

def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def decode_header_value(value):
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)

def message_text(msg):
    chunks = []
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                    .replace("&quot;", '"').replace("&#39;", "'"))
        chunks.append(text)
    return "\n".join(chunks)

def field(text, names):
    for name in names:
        match = re.search(r"(?im)^\s*" + re.escape(name) + r"\s*[:=]\s*(.*?)\s*$", text)
        if match:
            return match.group(1).strip()
    return ""

def parse_breakfast_booking(msg, uid):
    """Parse confirmed Friday-breakfast bookings sent by the participant app."""
    body = message_text(msg)
    subject = decode_header_value(msg.get("Subject", ""))
    item = field(body, ["item", "Artikel", "Produkt"])
    normalized = (subject + "\n" + item).upper().replace("Ü", "UE")
    if "FRUEHSTUECKSBUCHUNG" not in normalized and "FREITAGSFRUEHSTUECK" not in normalized:
        return None, "Keine Frühstücksbuchung"

    name = field(body, ["user", "Name", "Teilnehmer", "App-Name"])
    name = re.sub(r"\s+", " ", name).strip()
    if not name or name.lower() in ("anonymer teilnehmer", "unbekannter teilnehmer"):
        return None, "Teilnehmername fehlt"

    stamp = field(body, ["timestamp", "Zeitstempel", "Datum"])
    booking_time = None
    if stamp:
        for fmt in ("%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                booking_time = datetime.strptime(stamp, fmt).replace(tzinfo=TZ)
                break
            except ValueError:
                pass
    if booking_time is None:
        try:
            booking_time = parsedate_to_datetime(msg.get("Date")).astimezone(TZ)
        except Exception:
            booking_time = datetime.now(TZ)

    notification_id = field(body, ["notification_id", "Anmelde-ID", "Buchungsnummer"])
    return {
        "id": str(uid),
        "notification_id": notification_id,
        "name": name,
        "item": item or "Freitagsfrühstück",
        "created_at": booking_time.isoformat()
    }, ""


def parse_paypal_decision(msg, uid):
    """Parse the participant's explicit PayPal payment decision email."""
    body = message_text(msg)
    user_value = field(body, ["user", "Name", "Teilnehmer", "App-Name"])
    normalized = user_value.upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
    if "ZAHLUNG VOM TEILNEHMER" not in normalized:
        return None, "Keine PayPal-Statusmeldung"

    if "ALS NICHT BEZAHLT BESTAETIGT" in normalized:
        payment_status = "NICHT BEZAHLT"
    elif "ALS BEZAHLT BESTAETIGT" in normalized:
        payment_status = "BEZAHLT"
    else:
        return None, "PayPal-Zahlungsstatus fehlt"

    name = re.sub(
        r"\\s*[–-]\\s*ZAHLUNG VOM TEILNEHMER ALS (?:NICHT )?BEZAHLT BESTÄTIGT\\s*$",
        "", user_value, flags=re.I
    ).strip()
    amount_raw = field(body, ["amount", "Betrag"])
    amount_match = re.search(r"(\\d+(?:[.,]\\d{1,2})?)", amount_raw)
    if not amount_match:
        return None, "PayPal-Betrag fehlt"
    try:
        amount = round(float(amount_match.group(1).replace(",", ".")), 2)
    except ValueError:
        return None, "PayPal-Betrag ist ungültig"
    if amount not in (5.0, 10.0, 15.0):
        return None, "PayPal-Betrag ist nicht freigegeben"

    stamp = field(body, ["timestamp", "Zeitstempel", "Datum"])
    decision_time = None
    if stamp:
        for fmt in ("%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                decision_time = datetime.strptime(stamp, fmt).replace(tzinfo=TZ)
                break
            except ValueError:
                pass
    if decision_time is None:
        try:
            decision_time = parsedate_to_datetime(msg.get("Date")).astimezone(TZ)
        except Exception:
            decision_time = datetime.now(TZ)

    return {
        "id": str(uid),
        "request_id": field(body, ["request_id", "Anfrage-ID"]),
        "name": name or "Unbekannter Teilnehmer",
        "amount": amount,
        "status": payment_status,
        "created_at": decision_time.isoformat()
    }, ""


def parse_confirmation(msg, uid):
    """Recognize the app's explicit settlement-confirmation email."""
    subject = decode_header_value(msg.get("Subject", ""))
    body = message_text(msg)
    combined = subject + "\n" + body
    normalized = combined.upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
    if "ABBUCHUNG BESTAETIGT" not in normalized and "ABBUCHUNG BESTÄTIGT" not in combined.upper():
        return None, "Keine Abrechnungsbestätigung"
    settlement_id = ""
    m = re.search(r"(?:Abrechnungs-ID|settlement_id)\s*[:=]\s*(KAFFEE-[A-Za-z0-9_-]{8,100})", combined, re.I)
    if m:
        settlement_id = m.group(1).strip()
    if not settlement_id:
        m = re.search(r"notification_id\s*[:=]\s*BESTAETIGT-(KAFFEE-[A-Za-z0-9_-]{8,100})", combined, re.I)
        if m:
            settlement_id = m.group(1).strip()
    if not settlement_id:
        return {"uid": str(uid), "settlement_id": "", "name": ""}, "Abrechnungs-ID der Bestätigung fehlt"
    name = field(body, ["user", "Name", "Teilnehmer", "App-Name"])
    if not name:
        m = re.search(r"(?im)\buser\b\s*[:=]\s*([^\r\n<]{1,80})", body)
        if m:
            name = m.group(1).strip()
    name = re.sub(r"\s+", " ", name).strip()
    try:
        confirmed_at = parsedate_to_datetime(msg.get("Date")).astimezone(TZ)
    except Exception:
        confirmed_at = datetime.now(TZ)
    return {
        "uid": str(uid),
        "settlement_id": settlement_id,
        "name": name,
        "confirmed_at": confirmed_at
    }, ""

def parse_request(msg, uid):
    subject = decode_header_value(msg.get("Subject", ""))
    body = message_text(msg)
    combined = subject + "\n" + body
    if "SPENDERANMELDUNG" not in combined.upper() or "KAFFEE SPENDIEREN" not in combined.upper():
        return None, "Keine Spenderanmeldung"
    request_id = ""
    m = re.search(r"(?:Anmelde-ID|notification_id)\s*[:=]\s*([A-Za-z0-9_-]{8,100})", combined, re.I)
    if m:
        request_id = m.group(1)
    if not request_id:
        return None, "Anmelde-ID fehlt"
    name = field(body, ["user", "Name", "Teilnehmer", "App-Name"])
    if not name:
        # EmailJS templates sometimes render parameters as a compact key/value row.
        m = re.search(r"(?im)\buser\b\s*[:=]\s*([^\r\n<]{1,80})", body)
        if m:
            name = m.group(1).strip()
    name = re.sub(r"\s+", " ", name).strip()
    if not name or len(name) > 60 or name.lower() in ("unbekannter teilnehmer", "anonymer teilnehmer"):
        return None, "Teilnehmername fehlt oder ist ungültig"
    stamp = field(body, ["timestamp", "Zeitstempel", "Datum"])
    request_time = None
    if stamp:
        for fmt in ("%d.%m.%Y, %H:%M:%S", "%d.%m.%Y, %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                request_time = datetime.strptime(stamp, fmt).replace(tzinfo=TZ)
                break
            except ValueError:
                pass
    if request_time is None:
        try:
            request_time = parsedate_to_datetime(msg.get("Date")).astimezone(TZ)
        except Exception:
            return None, "Zeitstempel fehlt oder ist nicht lesbar"
    return {"uid": str(uid), "request_id": request_id, "name": name, "requested_at": request_time}, ""

def is_signup_time_valid(request_time, now):
    """Only accept requests from today, between 06:00 inclusive and 15:00 exclusive."""
    local_request = request_time.astimezone(TZ)
    local_now = now.astimezone(TZ)
    return (
        local_request.date() == local_now.date()
        and START <= local_request.time().replace(tzinfo=None) < END
    )


def search_relevant_uids(mail, last_uid):
    """Search only coffee-kasse notifications instead of downloading the whole inbox."""
    status, data = mail.uid(
        "search", None,
        'OR SUBJECT "KAFFEEKASSE" TEXT "KAFFEEKASSE"'
    )
    if status != "OK":
        raise RuntimeError("Yahoo-Postfach konnte nicht nach Kaffeekassen-Nachrichten durchsucht werden")
    return [
        int(value) for value in data[0].split()
        if value.isdigit() and int(value) > int(last_uid)
    ]


def commit_state():
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", ".github/state/sponsor-mail-state.json", "config.json", "breakfast-log.json", "paypal-log.json"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Automatische Kaffee-Spenderfreigabe"], cwd=ROOT, check=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True)

def main():
    account = os.environ["YAHOO_EMAIL"]
    password = os.environ["YAHOO_APP_PASSWORD"]
    state = load_json(STATE, {"last_uid": 0, "processed_ids": []})
    config = load_json(CONFIG, {})
    breakfast_log_path = ROOT / "breakfast-log.json"
    breakfast_log = load_json(breakfast_log_path, {"entries": []})
    if not isinstance(breakfast_log, dict) or not isinstance(breakfast_log.get("entries"), list):
        breakfast_log = {"entries": []}
    paypal_log_path = ROOT / "paypal-log.json"
    paypal_log = load_json(paypal_log_path, {"entries": []})
    if not isinstance(paypal_log, dict) or not isinstance(paypal_log.get("entries"), list):
        paypal_log = {"entries": []}
    birthday = config.setdefault("geburtstag", {})
    now = datetime.now(TZ)

    mail = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
    try:
        mail.login(account, password)
        mail.select("INBOX")
        diagnostic = False  # Live processing enabled after diagnostic inspection
        uids = search_relevant_uids(mail, 0 if diagnostic else state.get("last_uid", 0))
        if diagnostic:
            uids = uids[-30:]
            print(f"Diagnosemodus: {len(uids)} neueste passende Nachrichten werden nur anhand ihrer Kopfzeilen geprüft; es wird nichts verändert.")
        else:
            print(f"{len(uids)} passende Kaffeekassen-Nachrichten seit dem letzten Lauf gefunden.")
        processed = set(state.get("processed_ids", []))
        changed = False

        for uid in sorted(uids):
            status, fetched = mail.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                raise RuntimeError(f"E-Mail UID {uid} konnte nicht gelesen werden")
            msg = email.message_from_bytes(fetched[0][1])
            if diagnostic:
                sender_debug = parseaddr(msg.get("From", ""))[1].strip().casefold()
                reply_debug = parseaddr(msg.get("Reply-To", ""))[1].strip().casefold()
                to_debug = parseaddr(msg.get("To", ""))[1].strip().casefold()
                subject_debug = decode_header_value(msg.get("Subject", ""))
                request_debug, request_reason = parse_request(msg, uid)
                confirmation_debug = parse_confirmation(msg, uid)
                if confirmation_debug:
                    confirm_debug, confirm_reason = confirmation_debug
                else:
                    confirm_debug, confirm_reason = None, "Keine Abrechnungsbestätigung"
                if request_debug:
                    parsed_debug = f"REQUEST name={request_debug['name']!r} id={request_debug['request_id']!r} at={request_debug['requested_at'].isoformat()}"
                elif confirm_debug:
                    parsed_debug = f"CONFIRM id={confirm_debug.get('settlement_id')!r} name={confirm_debug.get('name')!r}"
                else:
                    parsed_debug = f"NO_MATCH request={request_reason!r} confirmation={confirm_reason!r}"
                print(f"DIAG UID {uid}: FROM={sender_debug!r} REPLY_TO={reply_debug!r} TO={to_debug!r} SUBJECT={subject_debug!r} {parsed_debug}")
                continue
            # Nur Nachrichten mit Kaffeekassen-Bezug werden gesucht. Auch deren UID
            # wird markiert, wenn der Absender nicht freigegeben ist, damit derselbe
            # Fremdversand nicht bei jedem 5-Minuten-Lauf erneut geprüft wird.
            state["last_uid"] = uid
            changed = True
            sender = parseaddr(msg.get("From", ""))[1].strip().casefold()
            if sender != ALLOWED_SENDER:
                print(f"UID {uid}: passende Betreff-/Textnachricht, aber Absender {sender or '(unbekannt)'} nicht freigegeben")
                continue
            paypal_entry, paypal_reason = parse_paypal_decision(msg, uid)
            if paypal_entry:
                if not any(str(item.get("id")) == paypal_entry["id"] for item in paypal_log["entries"] if isinstance(item, dict)):
                    paypal_log["entries"].append(paypal_entry)
                    paypal_log["entries"] = paypal_log["entries"][-1000:]
                    print(f"PayPal-Protokoll: {paypal_entry['name']} – {paypal_entry['amount']:.2f} EUR – {paypal_entry['status']}")
                continue

            breakfast_entry, breakfast_reason = parse_breakfast_booking(msg, uid)
            if breakfast_entry:
                if not any(str(item.get("id")) == breakfast_entry["id"] for item in breakfast_log["entries"] if isinstance(item, dict)):
                    breakfast_log["entries"].append(breakfast_entry)
                    breakfast_log["entries"] = breakfast_log["entries"][-1000:]
                    print(f"Frühstücksprotokoll: {breakfast_entry['name']} – {breakfast_entry['created_at']}")
                continue

            confirmation, confirmation_reason = parse_confirmation(msg, uid)
            if confirmation:
                settlement = birthday.get("abrechnung") or {}
                confirmation_key = "CONFIRM:" + confirmation.get("settlement_id", "")
                if confirmation_key in processed:
                    print(f"UID {uid}: doppelte Abrechnungsbestätigung ignoriert")
                    continue
                if not confirmation.get("settlement_id"):
                    print(f"UID {uid}: Bestätigung ignoriert ({confirmation_reason})")
                    continue
                if settlement.get("id") != confirmation["settlement_id"]:
                    print(f"UID {uid}: Bestätigung passt nicht zur aktuell offenen Abrechnung")
                    continue
                if settlement.get("status") != "offen":
                    print(f"UID {uid}: Abrechnung ist nicht mehr offen; Bestätigung ignoriert")
                    continue
                if confirmation.get("name") and confirmation["name"].casefold() != str(settlement.get("spender", "")).casefold():
                    print(f"UID {uid}: Name der Bestätigung stimmt nicht mit dem Spender überein")
                    continue
                confirmed_at = confirmation["confirmed_at"]
                settlement.update({
                    "status": "bestätigt",
                    "bestaetigtAm": confirmed_at.isoformat(),
                    "bestaetigtVon": confirmation.get("name") or settlement.get("spender", ""),
                    "bestaetigungUid": str(uid),
                    "bestaetigungQuelle": "Kassen-App per EmailJS"
                })
                birthday["abrechnung"] = settlement
                processed.add(confirmation_key)
                print(f"Abrechnung {settlement['id']} durch App-Bestätigung abgeschlossen.")
                continue

            request, reason = parse_request(msg, uid)
            if not request:
                print(f"UID {uid}: ignoriert ({reason})")
                continue
            if request["request_id"] in processed:
                print(f"UID {uid}: doppelte Anmelde-ID ignoriert")
                continue
            processed.add(request["request_id"])
            request_time = request["requested_at"]
            # Nur Anmeldungen, die tatsächlich zwischen 06:00 und 15:00 Uhr eingingen.
            if not is_signup_time_valid(request_time, now):
                print(f"UID {uid}: außerhalb des Anmeldezeitfensters")
                continue
            # Ein verspäteter GitHub-Start darf eine rechtzeitig eingegangene Anmeldung
            # nicht verwerfen. Die Zeit des tatsächlichen Antrags ist maßgeblich.
            if now.date() != request_time.date():
                print(f"UID {uid}: Anmeldung gehört zu einem anderen Kalendertag")
                continue
            lock_raw = birthday.get("sperreBis", "")
            try:
                lock_until = datetime.fromisoformat(lock_raw.replace("Z", "+00:00")).astimezone(TZ) if lock_raw else None
            except ValueError:
                lock_until = None
            # Auch manuell aktivierte Alt-Aktionen ohne explizite Sperrzeit berücksichtigen.
            if not lock_until and bool(birthday.get("aktiv", birthday.get("active", False))) and birthday.get("datum"):
                try:
                    event_day = datetime.strptime(birthday["datum"], "%Y-%m-%d").date()
                    lock_until = datetime.combine(event_day + timedelta(days=1), START, TZ)
                except ValueError:
                    lock_until = None
            if lock_until and lock_until > now:
                print(f"UID {uid}: Sperre bis {lock_until.isoformat()} aktiv")
                continue
            settlement = birthday.get("abrechnung") or {}
            if settlement.get("status") == "offen":
                print(f"UID {uid}: offene Abrechnung vorhanden; keine neue Aktion wird gestartet")
                continue
            # Der bestehende Abrechnungseintrag bleibt unangetastet.
            next_morning = datetime.combine(request_time.date() + timedelta(days=1), START, TZ)
            birthday.update({
                "aktiv": True,
                "spender": request["name"],
                "datum": request_time.date().isoformat(),
                "aktionsId": "KAFFEE-" + request_time.strftime("%Y%m%d-%H%M%S") + "-" + request["request_id"][-8:],
                "sperreBis": next_morning.isoformat(),
                "freigabeZeitpunkt": request_time.isoformat(),
                "automatischFreigegeben": True
            })
            print(f"Freigeschaltet: {request['name']} (Anmelde-ID {request['request_id']})")
            # Nur eine Anfrage je Sperrfenster freischalten.
            break

        state["processed_ids"] = sorted(processed)[-1000:]
        if changed:
            CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            commit_state()
    finally:
        try:
            mail.logout()
        except Exception:
            pass

if __name__ == "__main__":
    main()
