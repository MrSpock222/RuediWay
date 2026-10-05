# XIAO ESP32-S3 Sense als RuediWay-Kamera

Der XIAO ersetzt den Raspberry Pi als Kamera. Rekonstruktion, Scan-Fortschritt und Codex-Auswertung bleiben auf dem Windows-Host. Fotos können weiterhin am Host oder auf der Uhr ausgelöst werden. Die bestehende Scan-Analyse verwendet weiterhin ihren eigenen Button.

## Hardware und Firmware

- XIAO ESP32-S3 **Sense mit Kameraplatine**, angeschlossene WLAN-Antenne, USB-Stromversorgung.
- Arduino-Board `esp32:esp32:XIAO_ESP32S3`, getestet mit ESP32-Core **3.3.12**.
- **OPI PSRAM einschalten**, USB Mode **Hardware CDC and JTAG**, USB CDC On Boot **Enabled**, 8 MB Flash. Die Firmware nutzt die Espressif-Kamera- und JSON-Bibliotheken aus dem Board-Paket; keine zusätzliche Arduino-Library nötig.
- Die Pins folgen der [Seeed-Dokumentation](https://wiki.seeedstudio.com/xiao_esp32s3_camera_usage/) und dem installierten Espressif-Beispiel `CAMERA_MODEL_XIAO_ESP32S3`. Kamerasensor und PSRAM werden beim Start geprüft.

Die frühere Raspberry-Pi-Kamera mit CSI-Flachbandkabel wird dafür nicht verwendet; die Firmware verwendet die Kameraplatine des Sense.

## Einrichtung

1. Host starten wie bisher. Der PC muss über seine LAN-Adresse erreichbar sein, beispielsweise `http://192.168.254.118:8000`.
2. `RuediWayCamera/RuediWayCamera.ino` in Arduino öffnen, die genannten Board-Optionen wählen und auf den XIAO laden. Das ersetzt das vorherige Testprogramm, ohne einen vollständigen Flash-Erase auszuführen.
3. Im Projektverzeichnis einmal `.venv/Scripts/python.exe -m pip install -r esp32/requirements.txt` ausführen.
4. `.venv/Scripts/pythonw.exe esp32/setup_device.py --port COM6 --server http://192.168.254.118:8000` starten.
5. Im lokalen Fenster WLAN-Namen und Passwort des **2,4-GHz-WLANs** eintragen und **WLAN einrichten und mit Host koppeln** wählen. Vorher den Arduino Serial Monitor schließen, damit der USB-Port frei ist.

Das Fenster prüft die Kamera, erzeugt über die lokale Host-Schnittstelle einen einmaligen Kopplungscode und überträgt die Einrichtung über USB. WLAN-Passwort und Kameratoken stehen nicht im Sketch und werden nicht in Repository-Dateien geschrieben. Sie bleiben im nichtflüchtigen Speicher des ESP32 erhalten; diese Firmware aktiviert keine Flash-Verschlüsselung.

Nach erfolgreicher Einrichtung braucht der XIAO nur Strom und WLAN. Verbindungsabbrüche werden mit begrenzter Wiederholrate behandelt. Nach einem **Host-Neustart oder Ablauf der bisherigen 24-Stunden-Kopplung** das USB-Fenster erneut öffnen und **Gespeichertes WLAN behalten – nur neu koppeln** auswählen. Kopplung erfolgt bewusst nicht ohne neuen Host-Code. Eine neu gekoppelte Kamera ersetzt die vorherige Kamera am Host.

## Bildprofile

| Vorgang | Auflösung | Ziel-Bildrate / Qualität |
| --- | --- | --- |
| Foto / Analyse | 1600 × 1200 | einzelnes JPEG, Sensorqualität 10 |
| Live-Vorschau | 640 × 480 | Aufnahme bis 10 Bilder/s, Qualität 20; Übertragung abhängig vom WLAN |
| Seitenscanner | 1280 × 1024 | Aufnahme bis ca. 8 Bilder/s, Versand bis 5 Bilder/s, Qualität 12 |
| Reduzierte Scan-Vorschau | 160 × 128 | statt eines zurückgestellten Originalbilds, Qualität 75 des Software-Encoders |

Bei der Sensor-JPEG-Qualität bedeutet ein kleinerer Wert höhere Qualität; beim Software-Encoder der kleinen Vorschau bedeutet ein größerer Wert höhere Qualität. Tatsächliche Bildrate hängt von Belichtung, WLAN und Host ab. Die Originalauflösung bleibt erhalten. Bei Profilwechseln werden alte Bilder verworfen.

### Aufnahme und Übertragung ab Firmware 3

`ruediway-xiao-3` verwendet einen eigenen Aufnahme-Task. Drei feste PSRAM-Puffer von je 768 KiB reichen für ein Bild beim Upload, ein wartendes Bild und die nächste Aufnahme. Der Sensorpuffer wird nach dem Kopieren sofort freigegeben. Überholte wartende Bilder werden ersetzt; ein gutes Original wird gegenüber einer schlechteren/ähnlichen Aufnahme höchstens 350 ms bevorzugt. Vor dem Versand werden Bilder über 1,5 Sekunden Alter verworfen. Netzwerkwartezeiten blockieren die Kameraaufnahme nicht. Ein expliziter Fotoauftrag pausiert den Stream und wird wie bisher einmalig bearbeitet.

Im Scanprofil wird ein Achtelbild lokal decodiert und grob auf Helligkeit, Kontrast, Kanten und Änderung gegenüber dem zuletzt bestätigten Original geprüft. Decoder-Arbeitsspeicher und RGB-Vorschau liegen für schnelleren Zugriff im internen RAM; die großen JPEG-Puffer liegen im PSRAM. Offensichtlich schwache oder nahezu gleiche Ansichten werden bevorzugt als kleine Vorschau übertragen. Die Heuristik kann weder Schrift lesen noch verlässliche Seitenabdeckung bestimmen. Spätestens eine Sekunde nach dem letzten bestätigten Original wird deshalb wieder ein Original zur Host-Prüfung vorgesehen; Aufnahme, wartende Bilder und Übertragung verlängern den tatsächlichen Abstand. Bei fehlgeschlagener Vorauswahl wird ebenfalls das Original verwendet.

Reduzierte Vorschauen tragen `X-Ruediway-Preview-Only: 1`. Der aktualisierte Host zeigt sie an, übergibt sie aber niemals an die Rekonstruktion. Ohne dieses Kennzeichen gilt das Bild weiterhin als Original, sodass der Pi kompatibel bleibt. **Vor Firmware 3 auch den Host aktualisieren.** Der Host verarbeitet Originalbilder ohne die frühere feste 0,55-Sekunden-Sperre. Ein begrenztes Postfach hält nur das neueste wartende Bild, während der Worker arbeitet.

Zusätzliche USB-Messwerte: `frames_captured`, `frames_replaced`, `preview_frames_sent`, `selection_ms`, `decode_ms`, `measure_ms`, `encode_ms`, `frame_age_ms`, `decode_failures`, `capture_failures`, `pipeline_ready`, `free_psram`, `coarse_sharpness` und `coarse_movement`. Die Host-Route `/host/scan/status` liefert unter `pipeline` Eingangszahl, geprüfte Bilder, ersetzte Zwischenbilder, Verarbeitungszeit, Wartezeit und Bildrate. Eine ersetzte Zwischenaufnahme bedeutet nicht automatisch eine Lücke auf der Seite; dafür bleibt die Qualitätskarte maßgeblich.

Praxistest am 5. Oktober 2026 mit dem OV3660-Testgerät: Die lokale Vorauswahl benötigte etwa 140–162 ms pro Originalbild. Bei bewegtem Text verarbeitete der Host rund 2,9–3,1 Originalbilder/s; zuvor begrenzte die feste Sperre den Eingang auf höchstens rund 1,8/s. Im abschließenden 20-Sekunden-Lauf wurden 14 von 56 bestätigten Übertragungen als kleine Vorschau gesendet, ohne Kamera-, Decoder- oder Uploadfehler. Ein Fotoauftrag während der Live-Vorschau lieferte 1600×1200 Pixel nach etwa 1,9 Sekunden; anschließend lief der Stream automatisch weiter. Das sind einzelne Messungen unter den jeweiligen Licht- und WLAN-Bedingungen, keine garantierten Bildraten oder Aussagen zur Vollständigkeit eines Scans.

Firmware `ruediway-xiao-2` hält die HTTP-Verbindungen zum Host offen und verwendet getrennte Verbindungen für Bilder/Aufträge und Präsenzmeldungen. Antworten werden vollständig gelesen, bevor eine Verbindung wiederverwendet wird. Bei Übertragungsfehlern wird die Verbindung neu aufgebaut; Fotoaufträge werden nicht automatisch doppelt gesendet. Die Bildauflösung und JPEG-Qualität bleiben dabei erhalten.

### Wenn das Live-Bild stockt

Zuerst die **mitgelieferte WLAN-Antenne am kleinen Antennenanschluss des XIAO** prüfen. Ohne diese Antenne war am Testgerät trotz erfolgreicher Kopplung der Empfang bei etwa −83 dBm: Bilder kamen nur alle 6–12 Sekunden an oder Uploads brachen ab. Eine bestehende Kopplung allein beweist deshalb noch keine brauchbare Verbindung für Bilder.

Nach Anschluss der Antenne und mit Firmware `ruediway-xiao-2` am 5. Oktober 2026 gemessen: etwa −47 bis −48 dBm, 209 Vorschau-Bilder in 60 Sekunden, mittlerer Abstand (Median) 0,297 Sekunden und längste Pause 0,625 Sekunden. Das sind rund 3,5 Bilder/s im lokalen Test; kein garantierter Durchsatz für andere WLANs oder das höher aufgelöste Scanprofil.

Für längeren Kamerabetrieb den mitgelieferten Kühlkörper entsprechend der [Seeed-Montageanleitung](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/#installing-the-upgraded-heat-sink) am Thermal-Pad anbringen. Zur Montage die Stromversorgung trennen. Die ebenfalls mitgelieferten Stiftleisten werden für zusätzliche GPIO-Verbindungen gebraucht; die hier verwendete Kamera-/USB-/WLAN-Verbindung benötigt sie nicht.

Über USB liefert `{"cmd":"status"}` zusätzlich `wifi_rssi_dbm`, `capture_ms`, `upload_ms`, `poll_ms`, `frame_bytes`, `frames_sent`, `frame_errors` und `upload_code`. Die Zeiten beziehen sich auf den letzten jeweiligen Vorgang, die Zähler gelten seit dem Neustart. So lässt sich langsame Kameraaufnahme von langsamer WLAN-Übertragung unterscheiden. In der Vorschau sollten bei guter Verbindung etwa fünf Bilder pro Sekunde möglich sein; der Scanner überträgt bewusst größere Bilder seltener.

Der Belichtungsregler verwendet Sensorstufen von −2 bis +2. **Feste Belichtungszeiten in Mikrosekunden werden nicht unterstützt** und am Host deaktiviert. Die Firmware verwendet automatische Belichtungszeit und Verstärkung, aber keinen Autofokus. Farbe/Text/Schwarzweiß verarbeitet der Host; der Scanner erhält weiterhin die ursprünglichen JPEGs.

## Schnittstelle

Die Kamera nutzt ausgehende HTTP-Anfragen mit dem vorhandenen `X-Ruediway-Token`:

- `/pair` mit `device: camera` und `camera_model: xiao_esp32s3`.
- `/camera/heartbeat` und `/camera/next` für Präsenz und Aufträge.
- `/camera/live/{session}/{revision}/frame` für Live und Scan.
- Neu: `/camera/jobs/{id}/jpeg` nimmt ein rohes JPEG entgegen und bestätigt mit **202**, bevor der Host im Hintergrund Vorschau/Analyse ausführt. Der Auftrag wird vor dem Einlesen reserviert, damit Wiederholungen keine zweite Analyse auslösen. Die bisherige Multipart-Route des Pi bleibt erhalten.

USB spricht zeilenweise JSON mit `cmd: status`, `cmd: configure` oder `cmd: pair`. Status enthält Kamerasensor-ID, PSRAM-Größe, Verbindung und IP, aber keine Passwörter oder Tokens. Das Einrichtungsprogramm verwendet diese Schnittstelle; WLAN-Daten müssen nicht in einen Chat eingegeben werden.

## Bauen per CLI

```powershell
arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default,EraseFlash=none" --build-path esp32/build esp32/RuediWayCamera
arduino-cli upload --port COM6 --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default,EraseFlash=none" --input-dir esp32/build esp32/RuediWayCamera
```

Arduino IDE enthält die CLI unter `resources/app/lib/backend/resources/arduino-cli.exe`, falls sie nicht im PATH liegt. Bestehende Scans und die Pi-Implementierung werden durch die ESP32-Erweiterung nicht verändert.
