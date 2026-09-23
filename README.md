# RüdiWay

**Projektname: RuediWay**

Handy-Browser-Kamera → lokaler FastAPI-Server → Codex CLI → Textantwort im Browser und auf einer Wear-OS-Uhr.

## Projektziel

Ein Foto am Handy aufnehmen, am PC verarbeiten und eine kurze Antwort am Handy sowie in der Uhr-App anzeigen. Bilder gehen direkt an Codex; lokale OCR ist ausschließlich als späterer Fallback vorgesehen. Server und Oberfläche laufen lokal, die KI-Auswertung über Codex benötigt eine Internetverbindung und sendet das Bild an den konfigurierten Anbieter.

## Start unter Windows

Voraussetzungen: Python 3.11 oder neuer und Codex CLI im PATH. Zuerst `codex login` ausführen und mit dem gewünschten Konto anmelden. Kontingent und Abrechnung richten sich nach Anmeldung und Konfiguration; dieses Projekt verwendet keinen eigenen API-Client.

```powershell
cd J:/Github/RuediWay
py -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Wenn `.venv` schon existiert, kannst du die beiden Einrichtungsbefehle überspringen. Am PC `http://127.0.0.1:8000/host` öffnen und auf **Code erzeugen** klicken. Am Handy im selben WLAN `http://<LAN-IP-des-PCs>:8000` öffnen und den sechsstelligen Code eingeben. Der Code gilt fünf Minuten und nur einmal. Die PC-Seite zeigt an, ob ein Handy gerade verbunden ist; sie aktualisiert sich jede Sekunde. Nach „Verbindung trennen“ oder dem Schließen des Handy-Browsers verschwindet der Status direkt. Bei einer unterbrochenen Netzwerkverbindung greift spätestens nach 15 Sekunden eine Zeitgrenze. Die Kopplung selbst bleibt bis zu 24 Stunden gültig, solange der Server läuft. Bei einem Neustart muss ein neuer Code erzeugt werden.

Für eine Uhr einen eigenen Code am PC erzeugen und in der Uhr-App die LAN-Adresse des PCs eingeben. Das Android-Studio-Projekt und die Einrichtung stehen in `wearos/README.md`. Die PC-Seite zählt Handy und Uhr als Geräte. Die Uhr zeigt die letzte erfolgreiche Antwort automatisch an, solange ihre App geöffnet ist; sie aktualisiert etwa alle vier Sekunden. Nach einem Server-Neustart ist diese Antwort gelöscht und die Uhr muss neu gekoppelt werden.

Windows-Firewall gegebenenfalls für das private Netzwerk freigeben. Nur im vertrauenswürdigen privaten Netz betreiben, nicht öffentlich weiterleiten. HTTP verschlüsselt weder Foto noch Kopplungscode.

V1 verwendet eine mobile Fotoauswahl mit Kameraoption, keine Live-Vorschau. Welche Kameraauswahl erscheint, entscheidet der Browser. Eine spätere Live-Kamera über `getUserMedia` braucht auf dem Handy HTTPS mit vertrauenswürdigem Zertifikat.

## Struktur

- `app/server.py` und `app/pairing.py`: Webseiten, Einmalcode, Handy-Sitzung, Upload und Fehlerbehandlung
- `app/backend.py`: begrenzter Codex-Prozess mit Bildinput und Timeout
- `static/index.html`: mobile Fotoaufnahme und Textantwort
- `static/host.html`: PC-Seite zum Erzeugen des Kopplungscodes
- `app/results.py` und `/watch/result`: letzte Antwort für gekoppelte Geräte
- `wearos/`: eigenständige Android-Studio-App für die Uhr
- `prompts/analyze.txt`: Analyseauftrag
- `captures/`: temporäre Bilder und Antworten; automatisch entfernt, von Git ausgeschlossen
- `docs/PLAN.md`: Umsetzungsschritte und offene Punkte
- `tests/test_backend.py`: CLI-Verhalten ohne echte KI-Anfragen

`CODEX_BIN` kann den vollständigen Pfad zu `codex.exe` setzen. `RUEDIWAY_TIMEOUT` legt das Zeitlimit in Sekunden fest (Standard 120). Für die Bildanalyse sind `gpt-6-luna` und Reasoning `medium` fest eingestellt. Das Backend verwendet Read-only-Sandbox, ignoriert die Benutzerkonfiguration und persistiert keine Codex-Sitzung. Die bestehende Codex-Anmeldung wird verwendet. Ein erfolgreiches Login und funktionierende Sandbox sind Voraussetzung für den echten End-to-End-Test.

Tests: `.venv/Scripts/python.exe -m unittest discover -s tests -v`

Offizielle Referenz: https://learn.chatgpt.com/docs/non-interactive-mode

## Repository und Codex-Projekt

Lokaler Projektname: **RuediWay**, Ordner: `J:/Github/RuediWay`. Den Ordner in Codex über die Projektauswahl hinzufügen. Der Git-Remote `origin` zeigt auf https://github.com/MrSpock222/RuediWay. Die vorbereiteten Dateien liegen lokal als uncommittete Änderungen auf dem bestehenden GitHub-Startcommit.
