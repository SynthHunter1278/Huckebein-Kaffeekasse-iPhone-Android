# Automatische Kaffee-Spenderfreigabe

Der Workflow liest das Yahoo-Postfach alle fünf Minuten per IMAP SSL aus und verarbeitet EmailJS-Spenderanmeldungen. Die bestehende EmailJS-Versandfunktion bleibt unverändert.

## Einmalige Einrichtung

Im GitHub-Repository unter **Settings → Secrets and variables → Actions → New repository secret** anlegen:

- \`YAHOO_EMAIL\`: vollständige Yahoo-Mailadresse, in der EmailJS die Spenderanmeldung zustellt
- \`YAHOO_APP_PASSWORD\`: Yahoo-App-Passwort (nicht das normale Yahoo-Kontopasswort)

Danach unter **Settings → Actions → General → Workflow permissions** sicherstellen, dass Workflows Schreibrechte für das Repository erhalten (**Read and write permissions**).

## Regeln

- Anmeldung und automatische Freigabe nur zwischen 06:00 und 15:00 Uhr (Europe/Berlin).
- Es wird nur die erste gültige Anmeldung freigeschaltet.
- Sperre bis zum Folgetag 06:00 Uhr.
- Eine vorhandene offene Abrechnung wird nicht überschrieben; die Anmeldung bleibt dann ohne Freigabe.
- Mail-UID und Anmelde-ID werden dauerhaft protokolliert, um Doppelverarbeitung zu verhindern.
- Der Workflow läuft alle fünf Minuten; GitHub Actions kann geplante Starts verzögern.

## Wichtiger Hinweis zur E-Mail-Vorlage

Die EmailJS-Vorlage muss mindestens diese Felder in der Nachricht ausgeben: \`user\`, \`item\` (mit Anmelde-ID), und \`timestamp\`. Das Script akzeptiert die Anmelde-ID im Format \`Anmelde-ID: ...\`. Wenn die Vorlage andere Feldbezeichnungen oder ein anderes Layout verwendet, muss der Parser entsprechend angepasst werden.

## Test

Nach dem Eintragen der beiden Secrets kann der Workflow unter **Actions → Automatic coffee sponsor activation → Run workflow** manuell gestartet werden. Erst danach die automatische Zeitplanung produktiv laufen lassen.
