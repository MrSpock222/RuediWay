# Projektplan

## V1 – Grundgerüst

Fotoaufnahme/-auswahl im Handy-Browser → POST /analyze → temporär gespeichertes JPEG → `codex exec --image` → finale Textantwort → JSON → Browser.

Die PC-Seite erzeugt einen fünf Minuten gültigen Einmalcode. Das Handy tauscht ihn gegen eine auf 24 Stunden begrenzte Sitzung ein und hält eine WebSocket-Verbindung für den Live-Status offen. Beim Schließen erkennt der Server die Trennung direkt; bei einem abrupten Netzverlust wechselt die PC-Seite spätestens 15 Sekunden nach der letzten Meldung auf „getrennt“. Bildprüfung, 10-MB-Limit für die Bilddatei, 16-Megapixel-Limit, eindeutige temporäre Verzeichnisse, Aufräumen und Prozess-Timeout bleiben bestehen. Nur eine Analyse gleichzeitig. Es ist für den privaten lokalen Betrieb gedacht, nicht als öffentlicher Upload-Dienst.

## Nächste Schritte

1. Einmalkopplung vom PC auf dem Handy testen.
2. Optional HTTPS und Live-Kameravorschau ergänzen.
3. Nur falls direkte Bildauswertung nicht genügt: lokale OCR (z. B. Tesseract) ergänzen und erkannten Text an dieselbe CLI übergeben. OCR ist derzeit nicht implementiert und wird nicht stillschweigend verwendet.

## Grenzen

Kein garantierter Offline-KI-Betrieb und keine pauschale Kostenzusage. CLI-Fehler, Kontingentgrenzen und fehlende Anmeldung werden als Fehler angezeigt. Die Kopplung ist für das private LAN gedacht. Bei HTTP können andere Geräte im Netz Verkehr mitlesen; für öffentliche Netze ist HTTPS nötig.
