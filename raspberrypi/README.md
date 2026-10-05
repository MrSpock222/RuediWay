# Raspberry-Pi-Kamera

Der PC bleibt FastAPI- und Codex-Server. Der Pi nimmt nach einem Klick auf **Foto aufnehmen** am PC (`/host`) oder in der Wear-OS-App ein Bild auf. Er sendet es zur Analyse an den PC; die Antwort erscheint am Host und auf gekoppelten Uhren. Die Handy-Fotoauswahl bleibt verfügbar.

Am Host gibt es zusätzlich **Nur Foto anzeigen**: Ein neues Bild erscheint direkt auf der Host-Seite, ohne Codex-Aufruf. Die vorhandene Textantwort bleibt unverändert. Nur das letzte Vorschaubild bleibt im PC-Arbeitsspeicher, bis es durch eine neue Vorschau ersetzt, die Kamera entkoppelt/neu gekoppelt oder der Server beendet wird. Der Bildabruf ist ausschließlich am PC verfügbar; keine Vorschau wird an die Uhr oder einen KI-Anbieter gesendet.

## Voraussetzungen

- Python 3.7+ und `rpicam-still`, `libcamera-still` oder `raspistill` auf dem Pi. Keine Python-Zusatzpakete erforderlich.
- Kamera und PC müssen erreichbar sein. Getestete Hardware: Raspberry Pi 1 B, OV5647, Raspbian 13, Python 3.13, `rpicam-still`.
- Fotos verwenden 2592 × 1944 Pixel und drei Sekunden zum Einregeln der Belichtung. Fotos liegen nur vorübergehend auf Pi und PC.

## Einrichtung

`camera_client.py`, `video.py` und `ruediway-camera.service` nach `~/RuediWay/raspberrypi/` auf dem Pi kopieren. Am PC den Server starten und auf der Host-Seite einen Code erzeugen. Dann auf dem Pi:

```sh
python3 ~/RuediWay/raspberrypi/camera_client.py doctor
python3 ~/RuediWay/raspberrypi/camera_client.py pair --server http://192.168.254.118:8000
```

Den Code bei der Abfrage eingeben. Für einen manuellen Start:

```sh
python3 ~/RuediWay/raspberrypi/camera_client.py run
```

## Als Dienst starten

```sh
mkdir -p ~/.config/systemd/user
cp ~/RuediWay/raspberrypi/ruediway-camera.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ruediway-camera.service
sudo loginctl enable-linger "$USER"
```

Linger lässt den Benutzerdienst nach dem SSH-Logout und beim Start des Pi laufen. Die Kamera meldet sich alle vier Sekunden beim PC und holt Aufträge etwa alle zwei Sekunden ab. Aufnahmen werden nur durch einen bewussten Klick ausgelöst, nie automatisch wiederholt. Nach 15 Sekunden ohne Meldung zeigt der Host die Kamera als getrennt an.

## Neu koppeln und Fehler prüfen

Sitzungen sind wie beim Handy und der Uhr höchstens 24 Stunden gültig und gehen beim PC-Server-Neustart verloren. Dann stoppt der Pi-Dienst. Erneut einen Code erzeugen, den obigen `pair`-Befehl ausführen und anschließend:

```sh
systemctl --user restart ruediway-camera.service
systemctl --user status ruediway-camera.service
journalctl --user -u ruediway-camera.service -n 30 --no-pager
```

Die Sitzung liegt mit Dateirechten `600` unter `~/.config/ruediway/camera.json`. Diese Datei enthält einen Zugangsschlüssel und gehört nicht ins Repository. Eine neue Kamera-Kopplung ersetzt die vorherige Kamera. Andere Geräte behalten ihre Sitzung.

Nur eine Kamera und eine laufende Aufnahme werden unterstützt. Ein Auftrag läuft spätestens nach vier Minuten ab; verspätete Ergebnisse werden verworfen. Beim Entkoppeln der auslösenden Uhr wird ihr laufender Auftrag verworfen. Die bereits gestartete Kameraaufnahme/CLI kann dabei noch auslaufen.

Der Pi öffnet keinen zusätzlichen Netzwerkport. Der HTTP-Verkehr zum PC ist unverschlüsselt und für das private Heimnetz vorgesehen.

## Textfilter und Belichtung

Am Host Bildmodus und Belichtung einstellen und **Einstellungen übernehmen** wählen. Die Auswahl gilt für neue Pi-Aufnahmen vom Host und von der Uhr sowie für Live. Bereits gestartete Aufträge behalten ihre Einstellungen. Nach einem Server-Neustart gilt Textmodus mit automatischer Belichtung und −1 EV.

- **Farbe / Original:** keine Text-Nachbearbeitung; Belichtungsregler gelten trotzdem.
- **Text:** Graustufen, Kontrastspreizung und maßvolle Schärfung am PC.
- **Schwarzweiß:** lokaler Schwellenwert für dunkle Schrift. Kann Details in Diagrammen entfernen; bei schlechterem Ergebnis Text oder Farbe verwenden.
- **Belichtung:** negative Werte machen automatische Aufnahmen dunkler.
- **Feste Belichtungszeit:** bei sehr hellen Displays 1/500 oder 1/1000 s testen, bei dunklem Bild verlängern. EV wirkt nur bei automatischer Zeit. Kurze Zeiten können Bildschirmflimmern sichtbar machen.

Camera Rev 1.3 verwendet den OV5647-Sensor und hat keinen motorisierten Autofokus. Abstand, ruhige Montage und gleichmäßiges Licht bleiben entscheidend. Ausgebrannte oder unscharfe Schrift lässt sich nicht durch Filter wiederherstellen. Hinweise auf fast weiße, schwarze oder detailarme Bilder sind keine zuverlässige Schärfemessung.

## Live-Video

**Live-Vorschau starten** zeigt MJPEG mit 640 × 480 Pixeln. Die Kamera ist auf 5 Bilder/s begrenzt; die tatsächliche Rate hängt von Pi und WLAN ab. Benötigt `rpicam-vid` oder `libcamera-vid`. Verarbeitung erfolgt am PC, ohne KI-Aufruf oder Videoaufzeichnung; nur das jüngste Live-Bild bleibt im Arbeitsspeicher.

Stoppen, Verlassen des Tabs oder 20 Sekunden ohne Host-Erneuerung beendet Live. Für volle Fotoaufnahmen pausiert der Stream und startet anschließend wieder, sofern der Host geöffnet bleibt. Einstellungsänderungen starten den Stream kurz neu. Veraltete Frames werden nach fünf Sekunden ausgeblendet. Live ist nur am lokalen PC erreichbar.

## Seitenscan

Der Host-Scanner fordert ein eigenes Profil mit 1280 × 960 Pixeln, bis zu 3 Bildern/s und JPEG-Qualität 90 an. Dafür beide Dateien camera_client.py und video.py aktualisieren. Frame-Auswahl, Qualitätskarte und Rekonstruktion laufen auf dem PC. Der Pi benötigt weiterhin keine zusätzlichen Python-Pakete. Anleitung: [Seitenscanner](../docs/SCANNING.md).
