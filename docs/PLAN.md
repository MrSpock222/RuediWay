# Projektplan

Stand: 5. Oktober 2026.

## Umgesetzt

- Eigenes lokales Seiten-Scan-System mit Pi- oder ESP32-Stream, Qualitätsprüfung, Seitenrahmen, Registrierung, Rekonstruktion, Live-Abdeckung und vollständigem/partiellem Export. Die Erfassung ruft keine KI auf. Details: [Scanner](SCANNING.md).
- Textbezogene Abdeckung, lokale Prüfung der Schriftkonturen, Helligkeitsausgleich und begrenzte Überblendung. Unsicher zugeordnete Bildränder werden ausgeschlossen; konservative Nachbearbeitung erzeugt eine neue Kopie statt das Original zu überschreiben.
- Manueller Button **Aufgaben beantworten** für gespeicherte Scans: Gesamtbild und Textansicht gemeinsam an Codex, Antwort dauerhaft beim Scan sowie über die bestehende Verteilung am Host und auf der Uhr.
- XIAO ESP32-S3 Sense als alternative Kamera, Firmware 3 mit paralleler Aufnahme, drei festen JPEG-Puffern, grober lokaler Vorauswahl und regelmäßigen Originalbildern. Rekonstruktion und Qualitätskarte bleiben auf dem PC. USB-Einrichtung hält WLAN-Zugangsdaten aus dem Repository fern.
- Host verarbeitet Originalbilder ohne feste 0,55-Sekunden-Sperre; bei Überlast bleibt nur das neueste wartende Bild. USB- und Host-Messwerte machen Erfassung, Vorauswahl, Übertragung und Verarbeitung getrennt sichtbar.
- 74 Python-Tests einschließlich vollständigem synthetischem Scan, Geometrie, Textabdeckung, Export, Scan-Analyse, Zugriffsschutz und Trennung von reduzierten Vorschauen und Originalbildern.

- Handy-Fotoauswahl → FastAPI → Codex CLI → Textantwort im Browser und in der Wear-OS-App.
- Raspberry-Pi-Kamera mit Auslöser auf Host und Uhr → Foto zum PC → Codex-Analyse → Antwort auf Host und Uhr. Pi-Dienst, Kamerastatus und Auftragsverwaltung sind eingerichtet.
- Host: Foto ohne Analyse, Text-/Schwarzweißfilter und EV; beim Pi zusätzlich manuelle Belichtungszeit und 5-MP-Fotos. Normale Live-Vorschau pausiert für Fotos, stoppt beim Verlassen und wird nicht aufgezeichnet oder analysiert. Der Scanner speichert ausgewählte Quellbilder mit seinem Export.
- Direkter Bildinput mit festem Modell `gpt-6-luna` und Reasoning `medium`.
- Lokale PC-Seite mit sechsstelligen Einmalcodes und automatisch aktualisiertem Geräte-Status.
- Fünf Minuten gültige Codes, begrenzte Fehlversuche und Sitzungen bis zu 24 Stunden.
- Bildprüfung, 10-MB- und 16-Megapixel-Grenzen, temporäre Dateien und CLI-Timeout. Eine Analyse gleichzeitig.
- Eigenständige Wear-OS-App für die Pixel Watch 5, Version 1.3: Kopplung, Kamera-Auslöser, automatische Ergebnisanzeige bei geöffneter App und scrollbar lesbare Antworten.
- Lokales Löschen der Uhr-Antwort, ausdrückliches Trennen beim Neukoppeln und leere Ergebnisanzeige nach neuer Kopplung.
- Separate Geräteprüfungen: Android-Debug-Build und APK-Signatur, echte Pi-Kameraaufnahme mit Luna/medium, Pi-Live-Video und Profilwechsel. ESP32-Firmware erfolgreich gebaut und geflasht; lokale Vorauswahl, Originalbild-Verarbeitung sowie Foto während Live mit anschließender Stream-Fortsetzung getestet. Messwerte stehen in der [ESP32-Anleitung](../esp32/README.md).

## Nächste Schritte

1. Scanner und ESP32-Vorauswahl mit weiterer echter Handbewegung, unterschiedlichen Blättern, Licht und Abständen kalibrieren; Buchentzerrung und OCR als getrennte Erweiterungen vorsehen.
2. Bedienung auf der verbundenen Pixel Watch prüfen, insbesondere Wiederverbinden, Löschen und lange Antworten.
3. Optional Hintergrundbenachrichtigungen auf der Uhr ergänzen.
4. Optional HTTPS ergänzen.
5. Nur falls direkte Bildauswertung nicht genügt: lokale OCR als Fallback ergänzen. OCR ist derzeit nicht implementiert.

## Grenzen

Der PC-Server läuft lokal; die KI-Auswertung benötigt Internet. Kontingentgrenzen, fehlende Anmeldung und CLI-Fehler werden als Fehler angezeigt. Browser und Uhr erhalten die letzte erfolgreiche Antwort aus dem Arbeitsspeicher. Gespeicherte Scans und ihre Analysen bleiben dagegen lokal erhalten. Ein Neustart verliert ungespeicherte Scans und erfordert erneute Gerätekopplung.

Das Projekt ist für ein privates LAN gedacht. HTTP verschlüsselt den Verkehr nicht. Die Uhr aktualisiert nur bei geöffneter App; Hintergrundbenachrichtigungen und automatisierte Gerätetests sind noch offen.
