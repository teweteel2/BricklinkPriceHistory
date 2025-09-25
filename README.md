# BricklinkPriceHistory

Dieses Repository enthält Hilfsskripte, um BrickLink Preisübersichten herunterzuladen
und die erzeugten JSON-Dateien in eine Google Firestore Datenbank zu übertragen.

## Anforderungen

* Python 3.11 oder neuer
* Ein Google Cloud Projekt mit aktiviertem Firestore
* Service-Account Anmeldedaten, verfügbar über die Umgebungsvariable
  `GOOGLE_APPLICATION_CREDENTIALS`
* Installierte Abhängigkeiten (am besten mit derselben Python-Version, die die
  Skripte ausführt). In durch den Paketmanager verwalteten Python-Installationen
  blockiert `pip` unter Umständen direkte Installationen (PEP 668). Lege in
  diesem Fall ein virtuelles Umfeld an und installiere die Pakete dort:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -r requirements.txt
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

### Anmeldedaten

Standardmäßig liest `sync.py` die Umgebungsvariable
`GOOGLE_APPLICATION_CREDENTIALS`. Mit dem Parameter `--credentials` kann der
Pfad zur Service-Account-Datei explizit gesetzt werden. In beiden Fällen prüft
das Skript vor dem Start, ob die Datei existiert, und bricht ansonsten mit
einer verständlichen Fehlermeldung ab.

Sollte beim Zugriff auf Firestore die Meldung `PermissionDenied` erscheinen,
stimmen in der Regel Projekt oder Berechtigungen nicht. Stelle sicher, dass:

* die Firestore-API für das angegebene GCP-Projekt aktiviert ist,
* der Service-Account mindestens die Rolle **Cloud Datastore User** oder eine
  weitergehende Firestore-Rolle besitzt,
* `--project` (oder die Projektangabe in der JSON-Datei) auf dasselbe Projekt
  zeigt, in dem sich die Datenbank befindet.
