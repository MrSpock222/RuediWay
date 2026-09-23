# RuediWay

Foto am Handy aufnehmen, am PC auswerten und die Antwort im Handy-Browser oder auf einer Wear-OS-Uhr lesen.

```text
Handy-Browser → lokaler FastAPI-Server → Codex CLI
                        ↓                   ↓
                  Wear-OS-App ← Textantwort → Handy-Browser
```

Die Bildanalyse verwendet fest **gpt-6-luna mit Reasoning medium**. Das Foto wird direkt an Codex übergeben. OCR ist nur als möglicher Fallback vorgesehen und derzeit nicht implementiert. Der Server läuft lokal; die KI-Auswertung benötigt Internet und übermittelt das Foto an den konfigurierten Anbieter.

## Aktueller Stand · 24. September 2026

Im Entwicklungsstand vom 23./24. September sind folgende Funktionen und Korrekturen enthalten:

- **Handy:** Fotoauswahl mit Kameraoption, Analyse und lesbare Textantwort. Bei mehreren nummerierten Fragen fordert der Prompt Antworten zu jeder sichtbaren Nummer an, ohne Markdown oder LaTeX-Befehle.
- **PC:** Eine lokale Host-Seite erzeugt sechsstellige Einmalcodes. Jeder Code gilt fünf Minuten für genau eine Kopplung und wird nach fünf falschen Versuchen ungültig.
- **Verbindungsstatus:** Die PC-Seite aktualisiert die Zahl verbundener Geräte jede Sekunde. Der Handy-Browser meldet seine Präsenz über WebSocket; ausbleibende Meldungen werden nach 15 Sekunden als getrennt gewertet. Das tatsächliche Erkennen hängt auch vom Verhalten des Browsers beim Schließen ab.
- **Wear OS, App-Version 1.2:** Eigenständiges Android-Studio-Projekt im Ordner `wearos`, vorgesehen für die Pixel Watch 5. Die geöffnete App ruft neue Antworten etwa alle vier Sekunden ab und zeigt lange Texte scrollbar an.
- **Antwort löschen:** Die aktuelle Antwort bleibt auf der Uhr ausgeblendet, auch nach erneutem Öffnen. Eine neue Analyse erscheint automatisch.
- **Neu koppeln:** Die Uhr beendet zunächst ihre Sitzung am PC und öffnet anschließend die Kopplung. Bei einem Verbindungsfehler kann das Trennen erneut versucht werden.
- **Ergebnis-Reset:** Eine neue Kopplung beginnt mit leerer Anzeige und erhält nur danach veröffentlichte Antworten. Andere bestehende Kopplungen behalten Zugriff auf ihre aktuelle Antwort.
- **Feste Modellauswahl:** `gpt-6-luna` und Reasoning `medium` werden bei jeder Analyse ausdrücklich an die CLI übergeben.

Die elf Python-Tests bestehen. Das Wear-OS-Debug-APK wurde gebaut und seine Signatur geprüft; ein CLI-Bildaufruf mit Luna/medium war erfolgreich. Der automatisierte Gerätetest auf einer verbundenen Uhr steht noch aus.

## PC-Server unter Windows starten

Voraussetzungen:

- Python 3.11 oder neuer.
- Codex CLI im `PATH`, mit Unterstützung für die verwendeten Optionen und Zugriff auf `gpt-6-luna`.
- Ein angemeldetes Codex-Konto: vor dem ersten Start `codex login` ausführen. Kontingent und Abrechnung richten sich nach Anmeldung und Konfiguration; RuediWay verwendet keinen separaten API-Client.
- Handy, Uhr und PC müssen den lokalen Server im Netzwerk erreichen können.

Im lokalen Repository ausführen:

```powershell
cd J:/GitHub/RuediWay
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Wenn die virtuelle Umgebung bereits eingerichtet ist, genügt der letzte Befehl. Bei geänderten Abhängigkeiten die Installation erneut ausführen.

Am PC die [Host-Seite](http://127.0.0.1:8000/host) öffnen. Sie ist ausschließlich über die lokale Loopback-Adresse erreichbar.

## Handy koppeln und ein Foto analysieren

1. Am PC auf **Code erzeugen** klicken.
2. Am Handy im selben privaten Netzwerk `http://<LAN-IP-des-PCs>:8000` öffnen. Die LAN-Adresse lässt sich am PC mit `ipconfig` ablesen.
3. Den sechsstelligen Code eingeben, ein Foto aufnehmen oder auswählen und **Analysieren** drücken.
4. Die Antwort erscheint im Browser und auf einer zuvor gekoppelten, geöffneten Uhr-App.

Jedes Gerät benötigt einen eigenen Code. Ein neuer Code ersetzt einen noch nicht verwendeten Code, beendet aber keine bestehenden Sitzungen. Sitzungen gelten höchstens 24 Stunden und gehen bei einem Server-Neustart verloren. **Verbindung trennen** beendet die Handy-Sitzung ausdrücklich.

Die Fotoauswahl bietet je nach Browser eine Kameraoption. Eine Live-Kameravorschau ist noch nicht enthalten.

## Wear-OS-App verwenden

In Android Studio den Ordner [`wearos`](wearos/) öffnen, das Projekt synchronisieren und das Modul `app` auf der verbundenen Uhr starten. Danach auf der Uhr die LAN-Adresse des PCs samt Port `8000` und einen eigenen Code von der Host-Seite eingeben.

Die Uhr startet nach jeder neuen Kopplung mit leerer Ergebnisanzeige. Deshalb zuerst koppeln und danach das Foto am Handy analysieren. Neue Antworten erscheinen nur bei geöffneter Uhr-App; Hintergrundbenachrichtigungen sind noch nicht enthalten.

Details zu Installation, **Antwort löschen** und **Neu koppeln** stehen in der [Wear-OS-Anleitung](wearos/README.md). Das APK kann in Android Studio oder mit dem enthaltenen Gradle-Wrapper gebaut werden:

```powershell
cd wearos
.\gradlew.bat :app:assembleDebug
```

Für den Aufruf im Terminal müssen Java und das Android SDK eingerichtet sein. Das gebaute APK liegt unter `wearos/app/build/outputs/apk/debug/app-debug.apk`; Build-Ausgaben werden nicht im Repository gespeichert.

## Konfiguration und Datenhaltung

| Einstellung | Wert |
| --- | --- |
| Analysemodell | `gpt-6-luna`, fest in `app/backend.py` |
| Reasoning | `medium`, fest in `app/backend.py` |
| `CODEX_BIN` | Optionaler Pfad zur Codex-Programmdatei; Standard `codex` |
| `RUEDIWAY_TIMEOUT` | CLI-Zeitlimit in Sekunden; Standard `120` |
| Bildgrenzen | 10 MB und 16 Megapixel |
| Parallele Analysen | Eine pro Serverprozess |

Der CLI-Aufruf verwendet Read-only-Sandbox, `--ignore-user-config` und `--ephemeral`. Die bestehende Codex-Anmeldung wird verwendet. Persönliche CLI-Modellvorgaben werden durch den expliziten Aufruf ersetzt.

Fotos werden für die Analyse normalisiert, temporär im Ordner `captures` gespeichert und anschließend entfernt. Der Server hält nur die letzte erfolgreiche Textantwort im Arbeitsspeicher, keinen Verlauf. Ein Server-Neustart entfernt diese Antwort und alle Sitzungen. Die Uhr speichert ihre Serveradresse, Sitzung und den lokalen Löschstatus.

Der lokale HTTP-Verkehr ist unverschlüsselt. RuediWay ist für ein vertrauenswürdiges privates Netzwerk gedacht; den Port nicht öffentlich weiterleiten. Falls erforderlich, die Windows-Firewall für das private Netzwerk freigeben. HTTPS ist noch nicht eingerichtet.

## Projektstruktur

| Pfad | Aufgabe |
| --- | --- |
| `app/server.py` | FastAPI-Routen, Upload, Bildprüfung und Webseiten |
| `app/backend.py` | Codex-CLI-Aufruf mit Modell, Reasoning und Timeout |
| `app/pairing.py` | Einmalcodes, Sitzungen, Präsenz und Ergebnis-Reset pro Kopplung |
| `app/results.py` | Letzte erfolgreiche Antwort im Arbeitsspeicher |
| `static/index.html` | Handy-Oberfläche |
| `static/host.html` | PC-Oberfläche für Codes und Verbindungsstatus |
| `wearos/` | Android-Studio-Projekt für die Uhr |
| `prompts/analyze.txt` | Vorgaben für die Bildanalyse |
| `captures/` | Temporäre Dateien; Inhalte von Git ausgeschlossen |
| `tests/` | Tests für CLI-Verhalten, Kopplung und Ergebnisanzeige |
| [docs/PLAN.md](docs/PLAN.md) | Umgesetzter Stand und nächste Schritte |

## Tests

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Die Python-Tests verwenden simulierte CLI-Antworten und lösen keine echten KI-Anfragen aus. Der echte Bildaufruf und der Android-Build wurden separat geprüft.

## Repository

[MrSpock222/RuediWay auf GitHub](https://github.com/MrSpock222/RuediWay) · lokaler Projektordner: `J:/GitHub/RuediWay`.
