# RuediWay

Stand: **5. Oktober 2026**.

Fotos vom Handy, Raspberry Pi oder **XIAO ESP32-S3 Sense** am PC auswerten und Antworten im Browser oder auf einer Wear-OS-Uhr lesen. Der Seitenscanner setzt überlappende Kameraaufnahmen zu einer Seite zusammen; die Aufgabenanalyse startet erst auf ausdrücklichen Knopfdruck.

```text
Handy-Foto ───────────────────────────────┐
Pi- oder ESP32-Kamera → Foto / Stream ────┤
                                        ↓
                              lokaler FastAPI-Host
                               ├─ Live-Vorschau
                               ├─ Seitenscan → gespeichertes Scan-Paket
                               └─ angeforderte Analyse → Codex CLI
                                                          ↓
                                          Host / Browser / Wear OS
```

Der PC übernimmt Bildverarbeitung, Rekonstruktion und KI-Anbindung. **Scannen und Live-Vorschau rufen keine KI auf.** Die Bildanalyse verwendet fest **gpt-6-luna mit Reasoning medium** und übergibt Bilder direkt an Codex. OCR ist als möglicher Fallback vorgesehen, derzeit jedoch nicht implementiert. Die KI-Auswertung benötigt Internet und übermittelt die ausgewählten Bilder an den konfigurierten Anbieter.

## Aktuelle Erweiterungen

- **ESP32 als Kamera:** Der XIAO ESP32-S3 Sense ersetzt bei Bedarf den Pi. Firmware 3 nimmt unabhängig vom Upload auf und prüft Bilder grob auf dem Gerät vor. Wiederholte oder schwache Ansichten können als kleine Vorschau übertragen werden; die Rekonstruktion erhält ausschließlich Originalbilder. Firmware, USB-Einrichtung, Messwerte und Build-Anleitung: [ESP32](esp32/README.md).
- **Scanner auf dem PC:** Seitenrahmen, geometrische Zuordnung, Qualitätskarte und Rekonstruktion aus überlappenden Aufnahmen. Fortschritt bezieht sich auf erkannte Textbereiche; leere Ränder müssen nicht nah abgescannt werden.
- **Sauberere Übergänge:** Helligkeitsausgleich und begrenzte Überblendung beim vollständigen und partiellen Export. Unsicher registrierte Bildränder werden nicht übernommen. Eine konservative Nachbearbeitung gespeicherter Scans erstellt bei Bedarf eine neue Kopie.
- **Scan gezielt auswerten:** **Aufgaben beantworten** sendet Gesamtbild und Textansicht gemeinsam an die bestehende Codex-Pipeline. Die Antwort bleibt dem Scan zugeordnet und erscheint auch am Host und auf der gekoppelten Uhr.
- **Kamera vom Host oder von der Uhr auslösen:** Pi und ESP32 unterstützen Fotoaufträge, Verbindungsstatus und Live-Vorschau. **Nur Foto anzeigen** erstellt am Host eine Vorschau ohne KI-Analyse.
- **Schnellere Verarbeitung:** Der Host prüft neue Originalbilder, sobald sein Worker frei ist. Ersetzt wird nur das neueste wartende Zwischenbild; es entsteht keine wachsende Warteschlange. Im letzten ESP32-Praxistest wurden bei bewegtem Text etwa 2,9–3,1 Originalbilder/s verarbeitet. Das hängt von Motiv, Licht und WLAN ab.

## Eine Seite erfassen und auswerten

Die Pi- oder ESP32-Kamera zuerst nach ihrer Anleitung koppeln. Auf der [Host-Seite](http://127.0.0.1:8000/host) führt **Seitenscanner öffnen** zum [Scanner](http://127.0.0.1:8000/host/scanner).

1. **Neuen Scan starten**, zuerst die ganze Seite mit allen vier Ecken zeigen. Bei Bedarf **Rahmen markieren** verwenden.
2. Langsam näher herangehen und mit mindestens halber Bildüberlappung über das Blatt fahren. Grün bedeutet ausreichend detailliert, Gelb benötigt bessere Aufnahmen; Blau zeigt den aktuellen Ausschnitt.
3. Bei ausreichender Abdeckung der erkannten Textbereiche speichert der Scanner automatisch. Leere Ränder müssen nicht nah abgescannt werden; farbige Markierungen zeigen die geschätzten Textziele. **Teilscan speichern** sichert auch einen unvollständigen Stand, ausdrücklich als Teilscan.
4. Gesamtbild, kontrastverstärkte Textansicht, Originalausschnitte und Metadaten stehen lokal zum Ansehen und als ZIP bereit.
5. Bei Bedarf **Aufgaben beantworten** wählen. Ein gespeicherter Scan startet die Analyse niemals automatisch. Auch bewusst ausgewählte Teilscans können analysiert werden; fehlende Angaben werden im Prompt berücksichtigt.

Die Seite muss am Anfang vollständig sichtbar und möglichst flach sein. Abdeckung und Lesbarkeit werden geschätzt; ein verlässlich scharfes optisches Bild bleibt Voraussetzung. Gekrümmte Buchseiten werden noch nicht entzerrt. OCR ist in diesem Schritt nicht enthalten. Die [Scanner-Anleitung](docs/SCANNING.md) beschreibt Bedienung, Architektur und Grenzen.

Host-Abhängigkeiten einschließlich OpenCV und NumPy werden über `requirements.txt` installiert. Das Pi-Scanprofil verwendet 1280 × 960 Pixel und bis zu 3 Bilder/s; der ESP32 liefert Originale mit 1280 × 1024 Pixeln. Vor Firmware 3 auch den Host aktualisieren, damit reduzierte Vorschauen korrekt behandelt werden. Gespeicherte Scans bleiben unter `scans/`, bis sie lokal gelöscht werden; Git ignoriert diesen Ordner.

**Pausieren/Fortsetzen** erhält einen laufenden Scan im Arbeitsspeicher. Ein Server-Neustart verliert diesen ungespeicherten Stand. Bereits gespeicherte Scans bleiben erhalten.

## Kameras und Uhr

| Gerät | Aufgaben | Einrichtung |
| --- | --- | --- |
| Handy | Foto aufnehmen/auswählen, Analyse starten, Antwort lesen | Browser und Einmalcode |
| Raspberry Pi | Fotos und Stream, Kameraeinstellungen, optionaler Dienststart | [Pi-Anleitung](raspberrypi/README.md) |
| XIAO ESP32-S3 Sense | Fotos, Stream und lokale Bild-Vorauswahl; USB für Einrichtung | [ESP32-Anleitung](esp32/README.md) |
| Wear OS | Kamera auslösen, Antworten lesen und lokal löschen, neu koppeln | [Uhr-Anleitung](wearos/README.md) |

Der Host bietet Textmodus, Schwarzweiß und Belichtungssteuerung. Kameraeinstellungen gelten auch für Aufträge von der Uhr. Manuelle Belichtungszeiten stehen nur für den Pi zur Verfügung; der ESP32 verwendet automatische Belichtung mit EV-Korrektur. Software ergänzt keinen motorisierten Autofokus.

Die normale Live-Vorschau wird nicht aufgezeichnet. Während eines Scans werden ausgewählte Originalausschnitte mit dem Scan gespeichert. Ein Fotoauftrag unterbricht den Stream kurz; danach wird er fortgesetzt. Doppelte Aufträge, fremde Geräte und verspätete Ergebnisse werden abgewiesen.

Nach einem PC-Server-Neustart oder spätestens 24 Stunden müssen Geräte neu gekoppelt werden. Auf dem ESP32 bleiben gespeicherte WLAN-Daten erhalten; das USB-Einrichtungsfenster bietet eine erneute Kopplung ohne erneute Eingabe des WLAN-Passworts.

## Kopplung und Ergebnisanzeige

- **Handy:** Fotoauswahl mit Kameraoption, Analyse und lesbare Textantwort. Bei mehreren nummerierten Fragen fordert der Prompt Antworten zu jeder sichtbaren Nummer an, ohne Markdown oder LaTeX-Befehle.
- **PC:** Eine lokale Host-Seite erzeugt sechsstellige Einmalcodes. Jeder Code gilt fünf Minuten für genau eine Kopplung und wird nach fünf falschen Versuchen ungültig.
- **Verbindungsstatus:** Die PC-Seite aktualisiert die Zahl verbundener Geräte jede Sekunde. Der Handy-Browser meldet seine Präsenz über WebSocket; ausbleibende Meldungen werden nach 15 Sekunden als getrennt gewertet. Das tatsächliche Erkennen hängt auch vom Verhalten des Browsers beim Schließen ab.
- **Wear OS, App-Version 1.3:** Eigenständiges Android-Studio-Projekt im Ordner `wearos`, vorgesehen für die Pixel Watch 5. Die geöffnete App ruft neue Antworten etwa alle vier Sekunden ab, zeigt lange Texte scrollbar an und bietet einen Kamera-Auslöser.
- **Antwort löschen:** Die aktuelle Antwort bleibt auf der Uhr ausgeblendet, auch nach erneutem Öffnen. Eine neue Analyse erscheint automatisch.
- **Neu koppeln:** Die Uhr beendet zunächst ihre Sitzung am PC und öffnet anschließend die Kopplung. Bei einem Verbindungsfehler kann das Trennen erneut versucht werden.
- **Ergebnis-Reset:** Eine neue Kopplung beginnt mit leerer Anzeige und erhält nur danach veröffentlichte Antworten. Andere bestehende Kopplungen behalten Zugriff auf ihre aktuelle Antwort.
- **Feste Modellauswahl:** `gpt-6-luna` und Reasoning `medium` werden bei jeder Analyse ausdrücklich an die CLI übergeben.

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

Die Fotoauswahl bietet je nach Browser eine Kameraoption. Eine Live-Vorschau der Handykamera ist nicht enthalten; die Host-Live-Vorschau verwendet Pi oder ESP32.

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

Einzelfotos werden für die Analyse normalisiert, temporär im Ordner `captures` gespeichert und anschließend entfernt. Der Server hält für Browser und Uhr nur die letzte erfolgreiche Textantwort im Arbeitsspeicher. Ein Server-Neustart entfernt diese Antwort und alle Sitzungen. Die Uhr speichert ihre Serveradresse, Sitzung und den lokalen Löschstatus.

Gespeicherte Scans und ihre ausdrücklich angeforderten Analysen bleiben dagegen lokal unter `scans/` erhalten. `analysis.json` ordnet eine Antwort ihrem Scan zu; das ursprüngliche ZIP bleibt unverändert. Scanbilder, lokale Zugangsdaten und Build-Ausgaben sind nicht Bestandteil des Git-Repositories.

Der lokale HTTP-Verkehr ist unverschlüsselt. RuediWay ist für ein vertrauenswürdiges privates Netzwerk gedacht; den Port nicht öffentlich weiterleiten. Falls erforderlich, die Windows-Firewall für das private Netzwerk freigeben. HTTPS ist noch nicht eingerichtet.

## Projektstruktur

| Pfad | Aufgabe |
| --- | --- |
| `app/server.py` | FastAPI-Routen, Upload, Bildprüfung und Webseiten |
| `app/backend.py` | Codex-CLI-Aufruf mit Modell, Reasoning und Timeout |
| `app/camera.py`, `app/live.py`, `app/imaging.py` | Kameraaufträge, Präsenz, Live-Sitzungen und Bildfilter |
| `app/scanning/` | Lokale Registrierung, Qualitätskarte, Rekonstruktion, Worker, Export und Host-Routen |
| `static/scanner.html` | Scan-Steuerung, Live-Abdeckung, Rahmenmarkierung und gespeicherte Ergebnisse |
| `scans/` | Dauerhafte lokale Scan-Pakete, von Git ausgeschlossen |
| `app/pairing.py` | Einmalcodes, Sitzungen, Präsenz und Ergebnis-Reset pro Kopplung |
| `app/results.py` | Letzte erfolgreiche Antwort im Arbeitsspeicher |
| `static/index.html` | Handy-Oberfläche |
| `static/host.html` | PC-Oberfläche für Codes und Verbindungsstatus |
| `wearos/` | Android-Studio-Projekt für die Uhr |
| `raspberrypi/` | Pi-Kameraclient und Benutzerdienst |
| `esp32/` | Sense-Firmware, parallele Aufnahme, Vorauswahl und USB-Einrichtung |
| `prompts/` | Vorgaben für Foto- und Scan-Analyse |
| `captures/` | Temporäre Dateien; Inhalte von Git ausgeschlossen |
| `tests/` | Tests für CLI, Geräte, Bildverarbeitung, Scanner, Export und Analyse |
| [docs/PLAN.md](docs/PLAN.md) | Umgesetzter Stand und nächste Schritte |

## Tests

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Aktueller Stand: **74 Python-Tests**. Sie verwenden simulierte CLI-Antworten und lösen keine echten KI-Anfragen aus. Geprüft werden unter anderem vollständige synthetische Kamerafahrten, geometrische Genauigkeit, Textabdeckung, Teilscan-Export, Zugriffsschutz, begrenzte Bildpuffer und die Trennung von Vorschau und Scan-Originalen.

Die ESP32-Firmware wurde mit Core 3.3.12 gebaut und auf dem Sense getestet, einschließlich Vorauswahl und Fotoauslösung während Live. Frühere separate Prüfungen umfassen echte Pi-Aufnahmen mit CLI-Auswertung sowie Android-Debug-Build und APK-Signatur. Automatisierte Tests auf einer verbundenen Uhr und weitere Scanner-Praxistests unter unterschiedlichen Aufnahmebedingungen bleiben offen.

## Repository

[MrSpock222/RuediWay auf GitHub](https://github.com/MrSpock222/RuediWay) · lokaler Projektordner: `J:/GitHub/RuediWay`.
