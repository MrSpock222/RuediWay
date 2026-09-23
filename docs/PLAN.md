# Projektplan

Stand: 24. September 2026.

## Umgesetzt

- Handy-Fotoauswahl → FastAPI → Codex CLI → Textantwort im Browser und in der Wear-OS-App.
- Direkter Bildinput mit festem Modell `gpt-6-luna` und Reasoning `medium`.
- Lokale PC-Seite mit sechsstelligen Einmalcodes und automatisch aktualisiertem Geräte-Status.
- Fünf Minuten gültige Codes, begrenzte Fehlversuche und Sitzungen bis zu 24 Stunden.
- Bildprüfung, 10-MB- und 16-Megapixel-Grenzen, temporäre Dateien und CLI-Timeout. Eine Analyse gleichzeitig.
- Eigenständige Wear-OS-App für die Pixel Watch 5, Version 1.2: Kopplung, automatische Ergebnisanzeige bei geöffneter App und scrollbar lesbare Antworten.
- Lokales Löschen der Uhr-Antwort, ausdrückliches Trennen beim Neukoppeln und leere Ergebnisanzeige nach neuer Kopplung.
- Elf Python-Tests, erfolgreicher Android-Debug-Build und geprüfter CLI-Bildinput mit Luna/medium.

## Nächste Schritte

1. Bedienung auf der verbundenen Pixel Watch prüfen, insbesondere Wiederverbinden, Löschen und lange Antworten.
2. Optional Hintergrundbenachrichtigungen auf der Uhr ergänzen.
3. Optional HTTPS und Live-Kameravorschau ergänzen.
4. Nur falls direkte Bildauswertung nicht genügt: lokale OCR als Fallback ergänzen. OCR ist derzeit nicht implementiert.

## Grenzen

Der PC-Server läuft lokal; die KI-Auswertung benötigt Internet. Kontingentgrenzen, fehlende Anmeldung und CLI-Fehler werden als Fehler angezeigt. Kein Antwortverlauf: Der Server hält nur die letzte erfolgreiche Antwort im Arbeitsspeicher. Nach einem Neustart müssen Geräte erneut gekoppelt werden.

Das Projekt ist für ein privates LAN gedacht. HTTP verschlüsselt den Verkehr nicht. Die Uhr aktualisiert nur bei geöffneter App; Hintergrundbenachrichtigungen und automatisierte Gerätetests sind noch offen.
