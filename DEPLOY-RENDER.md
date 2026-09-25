# Update des bestehenden Render-Dienstes auf v5.1

> v5.0 → v5.1: keine neuen Pflicht-Variablen. Optional `ENTSOE_PAST_DAY_CACHE_SECONDS` (Standard 3600) und `LOG_LEVEL` (Standard INFO). Wenn der Dienst nicht über das Blueprint verwaltet wird, in Render unter **Settings → Build & Deploy → Auto-Deploy** „After CI Checks Pass“ wählen, damit nur Commits mit grünen Tests live gehen.

## Erstmaliges Update auf v5.0

1. Den **Inhalt** von `entsoe-desk-v5.0-web` in den Root des bestehenden Repos kopieren. `Dockerfile`, `render.yaml` und `app/` liegen weiter direkt im Root. Die alten Dateien `AUDIT-v4.4*.md` und `validation/` können gelöscht werden.
2. Lokal prüfen:
   ```bash
   pip install -r requirements-dev.txt
   python -m unittest discover -s tests
   python scripts/demo_server.py      # optional: Oberfläche ohne API-Key ansehen
   ```
3. In Render unter **Environment** setzen:
   - `ENTSOE_API_KEY`: bleibt wie bisher
   - `DASHBOARD_USERNAME` und `DASHBOARD_PASSWORD`: **dringend empfohlen**. Ohne sie ist das Dashboard öffentlich, und jeder kann über `?day=` dein ENTSO-E-Kontingent verbrauchen. Bei mehr als 400 Anfragen pro Minute sperrt ENTSO-E Token bzw. IP.
   - optional `NTP_CLIENT_ID` und `NTP_CLIENT_SECRET` (siehe unten)
   - Bewusst ohne Login (z. B. zum Testen): Benutzer und Passwort leer lassen und `DASHBOARD_PUBLIC_OK=true` setzen. Dann verschwindet die Warnung „Öffentlich ohne Login“.
4. Committen und auf den Branch pushen, den Render deployt. Auto-Deploy baut neu, die URL bleibt gleich.
5. `/health` aufrufen. Erwartet werden `version` = `5.1.0`, `configured` = `true`, `auth_enabled` = `true` und `netztransparenz` = `true`.
6. `python scripts/smoke_http.py --base-url https://de-power-desk.onrender.com --day <gestern> --user … --password …`

## netztransparenz.de (optional, aber der einzige Weg zu aFRR für ganz Deutschland)

ENTSO-E hat für Amprion keine Aktivierungsdaten. Die Summe für Deutschland gibt es deshalb nur bei den Übertragungsnetzbetreibern selbst.

1. Kostenlos registrieren unter https://extranet.netztransparenz.de und im OAuth Manager einen API-Client anlegen.
2. Client-ID und Secret als `NTP_CLIENT_ID` und `NTP_CLIENT_SECRET` in Render eintragen.
3. Danach meldet `/health` `"netztransparenz": true`. Im Panel „Systembilanz“ steht dann die Quelle „netztransparenz.de“ auf `ok`, und die Kachel zeigt NRV-Saldo sowie aFRR für ganz Deutschland.

**Vor dem Deploy lokal prüfen (1 Minute):**

```bash
NTP_CLIENT_ID=… NTP_CLIENT_SECRET=… python scripts/ntp_check.py --day <gestern>
```

Das Skript meldet, ob das Token funktioniert, welche URL-Form die API akzeptiert, wie die CSV-Kopfzeile aussieht und welche Werte für Deutschland ankommen. Am Ende steht `RESULT: OK` oder die Stelle, an der es hakt. Das Secret wird nie ausgegeben.

Die API-Dokumentation zeigt den Zeitraum nicht eindeutig: als Pfad (`…/Betrieblich/<von>/<bis>`) oder als Query (`?dateFrom=…&dateTo=…`). Der Client probiert beide Formen und merkt sich die, die funktioniert. Liefert der betriebliche Datensatz für einen Tag nichts, nimmt er den qualitätsgesicherten. Nach dem Deploy steht unter `/api/balancing` → `sources.NTP.diag` je Datensatz, welche Variante gegriffen hat (ohne Zugangsdaten). Bei Fehlern fällt das Dashboard auf ENTSO-E zurück und zeigt den Fehler an.

## Betrieb

- Ein vollständiger Seitenaufbau kostet rund 115 Abfragen bei ENTSO-E (vorher rund 90, weil jetzt der Gesamtfahrplan für jede Grenze dazukommt). Server-Cache: 4 min, bei Grenzen und Regelenergie 10 min, bei Kraftwerken 15 min. Der Limiter gilt pro Prozess.
- Im Free-Tier schläft der Dienst nach 15 Minuten ohne Zugriff ein. Der erste Aufruf danach dauert 30–60 s, und der Cache ist leer.
- netztransparenz.de erlaubt 2 Anfragen pro Sekunde; der Client hält Abstand. Wer das Limit wiederholt überschreitet, wird 2 Stunden gesperrt.

## Rollback

Den v4.4.1-Commit erneut deployen. Es gibt keine Migrationen, und die neuen Variablen sind optional.
