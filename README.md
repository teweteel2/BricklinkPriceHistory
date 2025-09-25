# BricklinkPriceHistory

Dieses Repository enthält Hilfsskripte, um BrickLink Preisübersichten herunterzuladen
und die erzeugten JSON-Dateien in eine Google Firestore Datenbank zu übertragen.

## Anforderungen

* Python 3.11 oder neuer
* Ein Google Cloud Projekt mit aktiviertem Firestore
* Service-Account Anmeldedaten, verfügbar über die Umgebungsvariable
  `GOOGLE_APPLICATION_CREDENTIALS`
* Installierte Abhängigkeiten (am besten mit derselben Python-Version, die die
  Skripte ausführt):
  ```bash
  python3 -m pip install -r requirements.txt
  ```

## Bricklink Preisabfrage

Mit `bricklink_price.py` lässt sich der Preis-Guide für einen bestimmten Artikel
abrufen. Das Skript speichert die Antwort standardmäßig als JSON-Datei im
aktuellen Arbeitsverzeichnis.

```bash
python bricklink_price.py SET 75257
```

## Firestore Synchronisation

Das Skript `sync.py` liest alle JSON-Dateien im angegebenen Verzeichnis (Standard:
aktuelles Verzeichnis) ein und schreibt die Daten in eine Firestore Collection.

* Vorhandene Einträge in `results.sold_*.price_detail` werden anhand des
  Feldes `date_ordered` geprüft und nur neue Datensätze eingefügt.
* Alle übrigen Felder werden überschrieben.

Beispielaufruf:

```bash
python sync.py --collection bricklink_price_history --project mein-gcp-projekt
```

Ohne Angabe eines Verzeichnisses werden die JSON-Dateien im aktuellen Ordner
verarbeitet.
