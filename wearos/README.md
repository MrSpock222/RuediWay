# RuediWay für Wear OS

Die eigenständige Uhr-App zeigt die letzte erfolgreiche Fotoanalyse des lokalen RuediWay-Servers. Sie ist für die Pixel Watch 5 vorgesehen und läuft ohne zusätzliche Handy-App. Die Uhr muss den PC im lokalen Netz erreichen können. Solange die App geöffnet ist, prüft sie etwa alle vier Sekunden, ob eine neue Antwort vorliegt.

## Einrichten

1. Den RuediWay-Server auf dem PC starten und `http://127.0.0.1:8000/host` öffnen.
2. In Android Studio **Open** wählen und diesen `wearos`-Ordner öffnen. Gradle synchronisieren und das Modul `app` auf der verbundenen Pixel Watch 5 installieren.
3. Auf der Uhr die LAN-Adresse des PCs mit Port eingeben, zum Beispiel `192.168.254.118:8000`. Nicht `127.0.0.1` verwenden: Das wäre die Uhr selbst.
4. Am PC **Code erzeugen** drücken und den sechsstelligen Code auf der Uhr eingeben. Für ein bereits gekoppeltes Handy einen weiteren Code erzeugen; ein Code gilt nur für ein Gerät.
5. Ein Foto im Handy-Browser analysieren. Die neue Antwort erscheint bei geöffneter Uhr-App automatisch.

Die Uhr speichert Serveradresse und Sitzung lokal. Eine Kopplung gilt höchstens 24 Stunden und endet bei einem Server-Neustart. Mit **Antwort löschen** verschwindet die aktuelle Antwort nur auf der Uhr; sie erscheint auch nach erneutem Öffnen nicht wieder. Die nächste Analyse wird automatisch angezeigt. **Neu koppeln** trennt zuerst die bisherige Sitzung am PC und öffnet danach die Eingabe für Adresse und Code. Bei einer neuen Kopplung ist die Ergebnisanzeige leer; alte Antworten erscheinen nicht erneut. Andere verbundene Geräte behalten ihre Anzeige. Wenn der PC nicht erreichbar ist, bleibt die Kopplung erhalten und die Uhr zeigt einen Fehler zum erneuten Versuch. Die PC-Seite zählt die Uhr als verbunden, solange die App geöffnet ist.

## Technik und Grenzen

Die App nutzt direkte HTTP-Anfragen an den PC (`/pair` und `/watch/result`). Der Server speichert nur die letzte erfolgreiche Antwort im Arbeitsspeicher; nach einem Neustart ist sie weg. Die Uhr erhält weder Kamerabilder noch alte Antworten als Verlauf. Es gibt keine Hintergrundbenachrichtigungen: Neue Antworten erscheinen, wenn die App geöffnet ist. HTTP im lokalen Netz ist unverschlüsselt; nur in einem vertrauenswürdigen privaten Netzwerk verwenden.

Das Projekt verwendet Java ohne zusätzliche Laufzeitbibliotheken, Android Gradle Plugin 9.4.0, Gradle 9.6.0 und API 37. Android Studio kann fehlende SDK- oder Gradle-Komponenten beim ersten Öffnen installieren.
