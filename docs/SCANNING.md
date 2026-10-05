# Seitenscanner

Der Scanner erfasst Bilder einer einzelnen flachen Seite. Die Verarbeitung läuft auf dem Host-PC mit OpenCV und NumPy. Während der Erfassung werden weder Codex noch andere KI-Dienste aufgerufen. Eine anschließende Aufgabenanalyse wird ausschließlich manuell gestartet. Textregionen sind lediglich geometrische Kandidaten; es gibt keine Transkription und keine Aufgabenlösung.

## Bedienung

1. Die Pi- oder ESP32-Kamera nach der jeweiligen Geräteanleitung koppeln. Am PC `http://127.0.0.1:8000/host/scanner` öffnen, oder auf der Host-Seite **Seitenscanner öffnen** wählen.
2. Die Seite auf einen kontrastierenden Untergrund legen. **Neuen Scan starten** und zunächst das gesamte Blatt samt vier Ecken möglichst gerade von oben zeigen. Der Rahmen muss in drei aufeinanderfolgenden brauchbaren Frames stabil sein.
3. Falls der Rahmen nicht erkannt wird: **Rahmen markieren**, im eingefrorenen Bild vier Ecken anklicken und übernehmen. Die Seite muss ausreichend groß sein und vollständig im Bild liegen. Ein falsch gewählter Rahmen erfordert einen neuen Scan.
4. Langsam näher herangehen. Anschließend überlappende Bahnen fahren; mindestens die Hälfte des vorherigen Ausschnitts sichtbar lassen. Nicht abrupt von der Gesamtübersicht zu einem winzigen Detail springen.
5. Die Seitenkarte beobachten. **Grün:** Qualität und Detaildichte genügen der Schätzung. **Gelb:** schon gesehen, aber noch nicht ausreichend erfasst. **Rot:** nicht beobachtet. **Blau:** aktuelle Kameraposition. Nur erkannte Textregionen werden eingefärbt und für den Fortschritt bewertet. Leere Ränder bleiben ungefärbt und müssen nicht nah aufgenommen werden. Prüfen, ob alle gewünschten Textstellen farbig markiert sind. Nach der Gesamtübersicht ist typischerweise noch fast nichts grün.
6. Bei verlorener Position ein Stück zurückgehen. Bei dauerhaft gelben Stellen Abstand, Beleuchtung und Belichtung prüfen. Rev 1.3 hat keinen motorisierten Autofokus.
7. Der Scanner schließt bei mindestens 98 % ausreichend detaillierter Zielpixel und mindestens 90 % guter Pixel in jeder erkannten Textregion und drei gültigen Abschlussprüfungen automatisch ab. Mindestens vier ausgewählte Bilder sind nötig. Die Anzeige kennzeichnet den Abschluss weiterhin als Schätzung.

**Pausieren/Fortsetzen** behält die aktuelle Karte. Verlassen oder Ausblenden des Tabs pausiert; spätestens nach 20 Sekunden ohne Erneuerung erlischt die Kameralease. Danach ist Fortsetzen nötig. Belichtung kann auf der Host-Seite angepasst werden. **Ungespeicherten Scan verwerfen** entfernt nur den laufenden Stand aus dem Arbeitsspeicher. **Teilscan speichern** sichert einen unvollständigen Stand mit entsprechender Kennzeichnung.

Ein PC-Neustart verliert ungespeicherte Scans. Gespeicherte Scans bleiben erhalten und werden unterhalb des Scanners aufgelistet. Gespeicherte Scans werden derzeit nicht wieder zur weiteren Erfassung geöffnet.

## Pipeline und Grenzen der Ressourcen

```text
Pi: rpicam-vid → MJPEG 1280 × 960, maximal 3 fps, JPEG-Qualität 90
  → authentifizierter Upload an vorhandene Live-Route
Host: unveränderte Bilddaten → begrenzter Latest-Frame-Puffer
  → Belichtung/Schärfe → Seitenrahmen → geometrische Registrierung
  → native Qualitätskarte → bessere Bereiche übernehmen
  → Abdeckung/Live-Vorschau → automatischer oder manueller Export
```

Die Pi-Kamera erhält nur ein neues Streamprofil. Sie führt kein OCR und kein Stitching aus. Die tatsächliche Bildrate hängt von Kamera, WLAN und Host ab. Der Host prüft das neueste Original, sobald der Worker frei ist; die frühere feste Grenze von etwa 1,8 Bildern/s entfällt. Bei Überlast wird nur das wartende Zwischenbild ersetzt. Der einzelne Scan-Worker blockiert keine HTTP-Anfragen. Uploads sind auf 2 MiB, decodierte Scanbilder auf 2,5 Megapixel begrenzt.

Der XIAO ESP32-S3 Sense nutzt ab Firmware 3 eine parallele Aufnahme mit drei festen JPEG-Puffern und vorsichtiger lokaler Vorauswahl. Reduzierte 160×128-Vorschauen werden als `X-Ruediway-Preview-Only: 1` gekennzeichnet und dienen ausschließlich der Live-Anzeige. Die Rekonstruktion und ihre Qualitätsprüfung erhalten weiterhin native Originalbilder mit 1280×1024 Pixeln. In regelmäßigen Abständen wird auch bei unsicherer Vorauswahl ein Original an den Host geschickt, damit schwache Schrift nicht dauerhaft allein durch die Mikrocontroller-Heuristik ausgeschlossen wird. Näheres unter [ESP32](../esp32/README.md).

`/host/scan/status` liefert unter `pipeline` die tatsächlich eingegangenen Originalbilder, geprüfte Bilder, ersetzte Zwischenbilder, jüngste Verarbeitungs-/Wartezeit und gemessene Verarbeitungsrate. Die Rate hängt vom Bildinhalt ab; sie ist keine Zusage über erreichbare Coverage oder Lesbarkeit.

Module:

- `geometry.py`: Seitenviereck, SIFT, Verhältnisprüfung der Deskriptoren, RANSAC-Homographie; bei schwacher Perspektive und räumlich knapper Stützung ein stabileres affines Modell. Mindestens 35 Inlier, räumliche Verteilung, Projektionsgrenzen und Bildvergleich gegen die unveränderte Übersicht begrenzen Fehlzuordnungen.
- `quality.py`: Belichtung, native Detaildichte, lokale Schärfe und Kontrast, Schriftgrößen- und Linienmerkmale. Bereits bekannte Details werden nicht als weißer Bereich akzeptiert, nur weil sie in einem neuen Bild verschwimmen. Der Untergrund außerhalb der registrierten Seite geht nicht in die Seitenqualität ein.
- `engine.py`: feste Seitenkoordinaten, 2800 Pixel längste Seite, Auswahl von bis zu 100 Originalframes und 25 Merkmalsreferenzen. Ein Bereich wird nur durch eine besser bewertete Aufnahme ersetzt. Keine künstliche Detailerzeugung.
- `service.py`: ein Worker, ein neuestes Eingabebild, Steuerbefehle, Lease-Pause und Export. Veraltete Sitzungen können neue Scans nicht überschreiben.
- `routes.py`: ausschließlich lokal erreichbare Scan-Steuerung und Ergebnisdateien.

Die Qualitätskarte verwendet 80-Pixel-Zellen und färbt darin nur die erkannten Textregionen ein. Eine Zelle gilt als gut, wenn mindestens 90 % ihrer Text-Zielpixel den Qualitätsschwellenwert erreichen. Der Prozentwert zählt die ausreichend detaillierten Zielpixel direkt; leere Ränder tragen weder zum Zähler noch zum Nenner bei. Maßgeblich bleibt die ursprüngliche Kameraauflösung, nicht die Größe des hochskalierten Gesamtbilds. Dies ist keine OCR-bestätigte Lesbarkeitsprüfung.

`text_regions.py` gruppiert lokale dunkle Zeichenkonturen aus nativen Bildern. Die Übersicht legt erste Textziele fest; geometrisch geprüfte Nahaufnahmen können weitere ergänzen. Bereits bekannte Ziele werden nicht entfernt, wenn ein späterer Ausschnitt sie nicht erkennt. Neue Textstellen können den Prozentwert senken und setzen die Abschlussprüfung zurück. Ohne erkannte Textregionen ist kein automatischer Abschluss möglich. Sehr blasse Schrift, einzelne Symbole und Grafiken können übersehen werden; die farbige Zielkarte muss visuell geprüft werden. Alte Exporte behalten ihre bisherige Flächenbewertung und werden als **Seitenfläche** gekennzeichnet. Neue Exporte verwenden `coverage_basis: text` und speichern die geschätzten Textregionen mit Qualitätsanteilen.

## Ergebnis und Datenhaltung

Ein Export wird zunächst in einem neuen temporären Unterordner geschrieben und anschließend in `scans/<scan-id>/` umbenannt. Bestehende Scans werden nicht überschrieben.

| Datei | Inhalt |
| --- | --- |
| `page.png` | Rekonstruiertes Farbbild; schwache Bereiche können noch aus der Übersicht stammen |
| `document.png` | Graustufen mit maßvoller lokaler Kontrastanhebung |
| `coverage.jpg` | Farbige Qualitätskarte |
| `sources/*.jpg` | Ausgewählte Kameraausschnitte ohne Textfilter, erneut als JPEG gespeichert |
| `metadata.json` | Abschlussstatus, Rasterqualität, Quellbilder, Homographien, Registrierungs- und Qualitätswerte, Textkandidaten im jeweiligen Quellbild |
| `scan.zip` | Paket mit sämtlichen oben genannten Dateien |

`ocr_text` bleibt `null`. Damit kann ein späterer Verarbeitungsschritt sowohl das Gesamtbild als auch die besser aufgelösten Originalausschnitte nutzen. Die reine Erfassung veröffentlicht keine Antwort an die Uhr. Erst die manuell gestartete Aufgabenanalyse verwendet die bestehende Ergebnisverteilung.

Die Daten liegen dauerhaft lokal und werden nicht automatisch gelöscht. Pro Scan können einige hundert Megabyte anfallen, da das ZIP die Bilddateien zusätzlich enthält. Vor langen Sitzungen freien Speicher prüfen. Nur die lokale Host-Seite darf Ergebnisse abrufen; Pi, Handy und Uhr erhalten über diese Routen keinen Zugriff. `scans/` ist von Git ausgeschlossen.

## Was derzeit zuverlässig vorausgesetzt werden muss

- **Bekannte Außengrenze:** Ohne anfängliche Gesamtübersicht lässt sich nicht feststellen, ob rechts oder links noch ein unbekannter Seitenteil fehlt. Ein falscher Rahmen führt entsprechend zu einer falschen Zielregion.
- **Eine ruhende, flache Seite:** Freie Kamerabewegung ist vorgesehen, aber keine gleichzeitige Bewegung des Blatts, kein Umblättern und keine starke Buchwölbung. Es gibt noch kein 3D-Modell, Book-Dewarping oder kamerakalibrierte Linsenkorrektur.
- **Optisch lesbare Bilder:** Filter können ausgebrannte oder völlig unscharfe Zeichen nicht wiederherstellen. Wiederkehrende Muster, winzige Schrift und fast leere Ausschnitte können Tracking verhindern; das System fordert dann mehr Überlappung.
- **Geschätzter Abschluss:** Auch grüne Flächen garantieren keine korrekte spätere OCR. Kleine Lücken können innerhalb der Schwellen bleiben. Sehr feine, kontrastarme Linien können konservativ gelb bleiben; der manuelle Export wird dann ausdrücklich als Teilscan bezeichnet.

## Reproduzierbare Prüfung

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
$env:PYTHONPATH=(Get-Location).Path
.venv/Scripts/python.exe tests/probe_scan.py --plain
```

Das Probeprogramm erzeugt eine künstliche Seite und bekannte Perspektiv-Ausschnitte. Es prüft die laufende Zuordnung und schreibt Vorschau, Gesamtbild und Messwerte nach `scan-validation/` (von Git ausgeschlossen). Ohne `--plain` enthält die Seite zusätzlich besonders feine graue Linien und Punkte, die bewusst ein schwierigerer Qualitätstest sind. Synthetische Tests ersetzen keinen Praxistest der freien Handbewegung mit tatsächlicher Kamera, Licht und Abstand.

## Korrektur der Qualitätsprüfung

Der globale Schärfewert dient nur noch dazu, praktisch unbrauchbare Bilder auszusortieren. Viel Weißraum oder schwacher Druck dürfen lesbare Schrift nicht blockieren. Die eigentliche Bewertung misst die Schärfe lokal an Schriftkonturen und berücksichtigt deren Kontrast. Einzelkriterien werden als Mindestanforderungen kombiniert, statt mehrere Abschläge zu multiplizieren. Auflösungsgrenzen, geometrische Zuordnung und Abschlussbedingungen bleiben erhalten. Die Statusantwort enthält zusätzliche Detailwerte und Ablehnungsgründe für die Fehlersuche.


## Gleichmäßige Rekonstruktion und Nachbearbeitung

**Auch beim Teilscan speichern** sind die folgenden Korrekturen bereits enthalten; ein vollständiger Scan ist dafür nicht nötig. Die Anzeige gleicht Helligkeit und Farbstich heller Papierflächen pro Aufnahme aus und überblendet einen schmalen Bereich an Übergängen. Originalbilder, Registrierungsvorlage und Qualitätsschätzung verwenden weiterhin die ursprünglichen Pixel. Es werden keine Zeichen ergänzt. Vollflächige dunkle Grafiken begrenzen die Hintergrundkorrektur; für farbkritische Zwecke die Originalaufnahmen verwenden.

Eine nur indirekt zugeordnete Aufnahme braucht stärkere Übereinstimmung mit der Übersicht; Referenzketten sind auf drei Schritte begrenzt. Bei ungenauerer Zuordnung werden nur Bereiche nahe den tatsächlich passenden Merkmalen übernommen. Das verhindert versetzte Bildränder. Eine Ausdehnung auf das ganze Bild ist nur bei einer besonders präzisen direkten Zuordnung erlaubt (mindestens 60 Inlier, höchstens 0,25 Pixel Restfehler, Korrelation mindestens 0,90, projektives Modell). Deshalb kann mehr Fläche gelb bleiben als zuvor. Die Prozentzahl ist keine Garantie für ein fehlerfreies zusammengesetztes Bild.

Ein gespeicherter Scan kann lokal als **neue Kopie** konservativ rekonstruiert werden:

```powershell
.venv/Scripts/python.exe -m app.scanning.rebuild scans/<alte-id> scans
```

Die Nachbearbeitung verwendet aufgezeichnete Homographien, überprüft die Übereinstimmung erneut und beschränkt sich auf direkt zur Übersicht passende Merkmalsbereiche. Sie verändert das Originalpaket nicht und kennzeichnet das Ergebnis immer als Teilscan. Metadaten enthalten die ursprüngliche Scan-ID und ausgeschlossene Bildnummern. Die Abdeckung wird neu berechnet und kann erheblich sinken; schwache Restbereiche bleiben aus der Übersicht sichtbar. Das ist keine neue Aufnahme und keine OCR- oder KI-Auswertung.


## Gespeicherten Scan analysieren

Bei einem gespeicherten Ergebnis und in der Scan-Liste gibt es **Aufgaben beantworten**. Dieser Button startet die Auswertung über die bestehende Codex-CLI-Konfiguration (`gpt-6-luna`, Reasoning `medium`). Ein vollständiger Scan startet die KI niemals automatisch. Auch ein bewusst gewählter Teilscan ist analysierbar; der Prompt weist dann ausdrücklich auf fehlende Angaben hin.

Eine Anfrage enthält das unverkleinerte `page.png` und `document.png`, ausdrücklich als zwei Ansichten derselben Seite. Die farbige Qualitätskarte und die vollständigen Registrierungsmetadaten werden nicht übertragen. Der Prompt erhält lediglich den Abschlussstatus und verlangt alle sichtbaren Aufgaben in Leserichtung, ohne doppelte Antworten oder erfundene unlesbare Zeichen. Auch bei 100 % kann die optische Lesbarkeit begrenzt sein.

Die Analyse läuft im Hintergrund. Erfolgreiche Antworten erscheinen beim ausgewählten Scan, auf der Host-Seite und über den vorhandenen ResultStore auf einer gekoppelten Uhr. Die Antwort und der Analysezustand werden separat als `analysis.json` im Scanordner gespeichert. Das ursprüngliche Bildpaket einschließlich `scan.zip` bleibt unverändert. Nach einem Host-Neustart kann die Antwort durch denselben Button wieder angezeigt werden; sie wird dabei nicht neu berechnet oder automatisch erneut an die Uhr gesendet.

Doppelklicks auf denselben Scan starten keinen zweiten Aufruf. Eine bereits erfolgreiche Analyse wird wieder angezeigt. Bei Fehlern oder einem unterbrochenen Host-Neustart ist ein ausdrücklicher neuer Versuch möglich. Laufende Scans und andere Fotoanalysen sperren neue Scananalysen gegenseitig. Ein Fehler ersetzt die letzte erfolgreiche Antwort auf der Uhr nicht.

Die lokalen, zugriffsgeschützten Routen sind `POST /host/scans/{id}/analysis` zum Start und `GET /host/scans/{id}/analysis` zum Abfragen. `app/scanning/analysis.py` verwaltet Status, dauerhafte Zuordnung, temporäre Eingabekopien und den gemeinsamen Analyse-Lock. Die Kamera wird für die Analyse eines gespeicherten Scans nicht benötigt.
