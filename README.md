# DE Power Desk v5.1

Ein Intraday-Dashboard für den deutschen Strommarkt auf Basis von ENTSO-E-Daten, optional ergänzt um netztransparenz.de. Es läuft mit FastAPI und lokal gebündeltem Plotly und wird auf Render mit Docker betrieben. Routen, Umgebungsvariablen und Deployment sind zu v4.4.1 kompatibel.

## Die sechs Signale auf einen Blick

| Kachel | Was sie zeigt | Warum es zählt |
|---|---|---|
| **EE-Ist vs. DA-Prognose** | Solar + Wind (Ist) minus DA-Prognose (A01), aufgeteilt nach Solar, Onshore und Offshore; zusätzlich Ist minus ID-Stand von 08:00 | Die DA-Prognose ist das, was die Auktion eingepreist hat. Die Basis bleibt den ganzen Tag gleich. |
| **EE-Revision nächste 4 h** | Laufende Prognose (A18) minus DA für die kommenden 4 Stunden, dazu der Durchschnitt für den Rest des Tages | Das einzige vorausschauende Signal auf der Seite |
| **Residuallast vs. DA** | (Last − EE) Ist minus (Last − EE) DA, dazu der Lastprognosefehler | Zeigt, was konventionelle Kraftwerke und Importe zusätzlich decken müssen |
| **Netto-Import** | Physischer Fluss, DA-Fahrplan, Gesamtfahrplan (A09/A05). Intraday-XB = Gesamt − DA, Phys. − Fahrplan = physisch − Gesamt | Trennt grenzüberschreitenden Intraday-Handel vom Rest. In der Summe über alle Grenzen ist „Phys. − Fahrplan“ kein Ringfluss (der hebt sich auf), sondern vor allem Regelenergie-Austausch und Redispatch. Leitungen, die den ganzen Tag 0 MW führen, werden markiert. |
| **Kraftwerke DE** | Nicht verfügbare Leistung in DE-LU, davon ungeplant, Veränderung zum gleichen Zeitpunkt gestern, neue oder geänderte Meldungen der letzten 24 h | Die Veränderung und neue Meldungen sind das Signal, nicht der absolute Bestand |
| **Systembilanz / reBAP** | A86-Bilanz (negativ = System kurz), reBAP (A85), aFRR, NRV-Saldo | Ob das System gerade kurz oder lang ist |

Die Markierung „bullish/bearish“ ist eine einfache Desk-Heuristik: EE-Abweichung ab ±300 MW, Residuallast ab ±500 MW, Kraftwerks-Δ ab ±300 MW. Sie ist keine Handelsempfehlung.

## Neu in v5.1 gegenüber v5.0

Fachliche Korrekturen nach Abgleich mit den ENTSO-E *Detailed Data Descriptions* v3r4, der netztransparenz.de-API-Dokumentation v1.14 und der reBAP-Modellbeschreibung:

- **Systembilanz (A86) streng nach TR 17.1.H.** Gezählt wird nur die Bilanz D (businessType A19). TR 17.1.H sieht einen zweiten Wert vor (MV − SV); eine weitere Zeitreihe im Dokument würde sonst still mitaddiert. `flowDirection` wird explizit gemappt: A01 = Überschuss (+, lang), A02 = Defizit (−, kurz), A03 = ausgeglichen (0). Unbekannte oder fehlende Codes werden verworfen und unter `sources.A86.ignored` gemeldet statt als positiv geraten. Werte in MW (MAW) werden in MWh umgerechnet.
- **Revisionen mit Richtungswechsel.** Bisher war die Richtung Teil des Revisionsschlüssels. Kippte eine Revision eine MTU von lang auf kurz, blieben beide Werte stehen und wurden verrechnet. Jetzt ersetzt die neueste Revision die MTU komplett.
- **reBAP als vorläufig gekennzeichnet.** Werte vom selben Tag sind operative Schätzungen; der Abrechnungspreis kommt später qualitätsgesichert. Die App liest `docStatus` (A01 vorläufig, A02 final, X01 geschätzt) und zeigt „vorläufig“, solange nicht final gemeldet ist.
- **Grenzflüsse richtig benannt.** „Ungeplant“ heißt jetzt „Phys. − Fahrplan“. In der Summe über alle Grenzen heben sich Ringflüsse auf; der Rest ist, was Handelsfahrpläne laut TR 12.1.F ausschließen (Regelenergie-Austausch, Redispatch, Nothilfe), plus Abweichungen, HVDC-Verluste und Datenlücken. Pro Grenze dominieren Ring-/Transitflüsse, an Core-Grenzen ist der Fahrplan aber eine rechnerische Zerlegung der Nettopositionen.
- **Ø 1 h neben der letzten MTU.** Die jüngsten Ist-Werte sind laut TR 16.1.B/C Hochrechnungen und werden mit Messwerten nachgezogen. EE-Abweichung, Residuallast-Überraschung und Systembilanz zeigen deshalb zusätzlich das Mittel der letzten vier MTUs.
- **Zeitumstellung.** Die Diagramme plotten intern in UTC und beschriften in Europe/Berlin. Vorher lagen am Tag der Rückstellung (z. B. 25.10.2026) die beiden Stunden 02:00–03:00 übereinander.
- **Nullwerte** werden in Balkendiagrammen keiner Seite mehr zugeordnet (vorher: 0 = „lang“ bzw. „über Prognose“).
- **Betrieb:** Logging aller abgefangenen Upstream-Fehler (Token maskiert, ohne Tracebacks), längerer Cache für vergangene Tage (`ENTSOE_PAST_DAY_CACHE_SECONDS`, Standard 1 h), Container läuft als unprivilegierter User, GitHub-Actions-Tests, Render deployt nur nach grünen Tests (`autoDeployTrigger: checksPass`).

## Neu in v5.0 gegenüber v4.4.1

- **Fester Vergleich gegen DA beim EE-Prognosefehler.** Bisher wechselte die Vergleichsbasis im Tagesverlauf von DA über ID zur laufenden Prognose (A18). A18 ist für vergangene Viertelstunden fast ein Nowcast. Am 22.09.2026 lag der mittlere Fehler gegen A18 bei 557 MW, gegen DA bei 1.213 MW. Jetzt gilt fest DA, der Vergleich gegen den ID-Stand wird separat gezeigt.
- **Grenzflüsse mit Gesamtfahrplan (A09, Vertragstyp A05).** Die Differenz „physisch − DA“ vermischte Intraday-Handel und Ringflüsse (Beispiel PL am 22.09.: DA +1.139 MW, physisch −18 MW). Jetzt werden DA, Intraday-XB und ungeplante Flüsse getrennt ausgewiesen, je Grenze und als Summe. Grenzen, auf denen den ganzen Tag 0 MW fließen (z. B. NordLink), werden markiert statt als „ok“ durchzugehen.
- **Kraftwerke: Lücke bei A77 geschlossen.** Bisher wurden Anlagenmeldungen (A77) verworfen, sobald eine Zone überhaupt Blockmeldungen (A80) hatte. Anlagen, die nur auf Anlagenebene melden (Windparks, viele GuD), fehlten dadurch. Jetzt werden A77-Meldungen ergänzt, wenn für dieselbe Anlage keine A80-Meldung vorliegt. Doppelzählung wird über die ID der Anlage ausgeschlossen.
- **Kraftwerke: Signal statt Bestand.** Der Fokus liegt auf DE-LU. Neu sind der Anteil ungeplanter Ausfälle, die Veränderung zu gestern je Zone und eine Liste neuer oder geänderter Meldungen der letzten 24 h (nach Veröffentlichungszeit). Die alte Summe über vier Länder mit 56 GW war fast nur Bestand: 70 % Ausfälle ab 30 Tagen, davon 23 GW französische Kernkraft in Revision.
- **Regelenergie.** Ist netztransparenz.de konfiguriert, werden NRV-Saldo und aFRR/mFRR für ganz Deutschland geholt, einschließlich Amprion. Ohne diese Zugangsdaten zeigt das Dashboard die ENTSO-E-Summe der drei veröffentlichenden Regelzonen und kennzeichnet sie ausdrücklich als „ohne AMPRION“. Sind die Preise für lang und kurz identisch, wird der reBAP als eine Linie dargestellt.
- **Absicherung.** `?day` ist auf den Zeitraum 2015 bis heute + 2 Tage begrenzt, denn jedes neue Datum kostet rund 115 Abfragen bei ENTSO-E. `/health` meldet, ob Basic Auth aktiv ist, und die Oberfläche warnt sichtbar, wenn die Seite öffentlich ist.
- **Neue Oberfläche:** eine Signalleiste, getrennte Diagramme statt Doppelachsen, Tabellen je Grenze und Zone, Hell- und Dunkelmodus sowie eine Handyansicht.
- **Entfernt:** Der Zeitpunkt „document created“ an den Prognosen war in Wahrheit der Abfragezeitpunkt und damit irreführend.

## Start lokal

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ENTSOE_API_KEY eintragen
python -m uvicorn app.main:app --port 8000
```

**Ohne API-Key ansehen:** `python scripts/demo_server.py --port 8000` startet die echte App mit einer synthetischen ENTSO-E-Quelle. Parser, Aggregation und Oberfläche laufen vollständig durch, nur die Zahlen sind erfunden.

## Tests

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v                                  # offline, 107 Tests
python scripts/live_smoke.py --env-file .env --day 2026-09-22            # echter ENTSO-E-Abgleich
python scripts/ntp_check.py --day 2026-09-22                             # netztransparenz.de-Zugang prüfen
python scripts/smoke_http.py --base-url https://de-power-desk.onrender.com --day 2026-09-22 --user … --password …
```

## Datenquellen und Grenzen

- **EE, Last:** DE Member State (10Y1001A1001A83F). A75/A16 Ist, A69 A01/A40/A18, A65 A16/A01. Residuallast = Last − Solar − Wind; Biomasse und Laufwasser zählen nicht dazu.
- **Grenzen:** DE-LU gegenüber 11 Nachbarn. A11 physisch, A09 A01 (DA) und A09 A05 (Gesamt). Eine Summe wird nur gebildet, wenn alle Grenzen zur selben Viertelstunde Daten haben.
- **Kraftwerke:** A80 und A77 für DE-LU, FR, NL, BE; die neueste Revision zählt, Stornierungen werden entfernt, pro Einheit gilt die größte Einschränkung. Die Veränderung über 24 h vergleicht beide Zeitpunkte mit dem heutigen Wissensstand. „Neu oder geändert“ richtet sich nach dem `createdDateTime` der Meldung.
- **Regelenergie:** A86 = Bilanz D nach TR 17.1.H (DDD v3r4: D > 0 Überschuss, D < 0 Defizit), nur businessType A19; `flowDirection` A01 = Überschuss, A02 = Defizit, A03 = ausgeglichen. Das Vorzeichen wurde zusätzlich an Live-Daten vom 22.09.2026 geprüft (r = −0,65 mit dem Netto-aFRR-Abruf; negativ heißt kurz). reBAP am selben Tag ist vorläufig. Beim NRV-Saldo gilt die deutsche Konvention: positiv heißt Unterdeckung. Die Anbindung an netztransparenz.de folgt der offiziellen API-Dokumentation (v1.14) und erkennt die URL-Form selbst. `scripts/ntp_check.py` prüft den Zugang, siehe DEPLOY-RENDER.md.
- Fehlende Daten werden nie als 0 dargestellt. Historische ENTSO-E-Daten können revidiert werden.
