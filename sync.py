"""Synchronize local BrickLink price JSON exports to a Firestore database."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    from google.api_core import exceptions as google_api_exceptions
    from google.cloud import firestore
    from google.auth import exceptions as google_auth_exceptions
    from google.oauth2 import service_account
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Das Paket 'google-cloud-firestore' ist nicht installiert. "
        "Installiere die Abhängigkeiten z. B. mit einem virtuellen Umfeld:\n"
        "    python3 -m venv .venv && source .venv/bin/activate\n"
        "    python -m pip install -r requirements.txt"
    ) from exc


DEFAULT_COLLECTION = "bricklink_price_history"


JsonObject = Dict[str, Any]


def _load_json_files(directory: Path) -> List[Tuple[Path, JsonObject]]:
    """Return a list of (path, data) tuples for JSON files in *directory*."""

    json_files: List[Tuple[Path, JsonObject]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Fehler beim Lesen von {path.name}: {exc}") from exc
        json_files.append((path, data))
    return json_files


def _sanitize_document_id(item_type: str, item_no: str) -> str:
    """Return a safe Firestore document id."""

    safe_type = item_type.replace("/", "-")
    safe_no = item_no.replace("/", "-")
    return f"{safe_type}_{safe_no}".strip("_")


def _merge_sold_price_details(
    existing: Iterable[JsonObject] | None,
    new: Iterable[JsonObject] | None,
) -> List[JsonObject]:
    """Merge sold price details ensuring unique ``date_ordered`` entries."""

    merged: List[JsonObject] = []
    seen_dates = set()

    if existing:
        for entry in existing:
            date = entry.get("date_ordered")
            if date is None:
                continue
            if date not in seen_dates:
                seen_dates.add(date)
                merged.append(entry)

    if new:
        for entry in new:
            date = entry.get("date_ordered")
            if date is None or date in seen_dates:
                continue
            seen_dates.add(date)
            merged.append(entry)

    merged.sort(key=lambda item: item.get("date_ordered") or "")
    return merged


def sync_file(
    db: firestore.Client,
    path: Path,
    data: JsonObject,
    *,
    collection: str,
) -> None:
    """Synchronize the JSON payload from *path* to Firestore."""

    item_type = data.get("item_type")
    item_no = data.get("item_no")

    if not item_type or not item_no:
        raise RuntimeError(
            f"Datei {path.name} enthält keine gültigen 'item_type'/'item_no' Werte."
        )

    document_id = _sanitize_document_id(str(item_type), str(item_no))
    doc_ref = db.collection(collection).document(document_id)

    try:
        existing_snapshot = doc_ref.get()
    except google_api_exceptions.PermissionDenied as exc:
        raise SystemExit(
            "Kein Zugriff auf das Firestore-Projekt. Prüfe, ob der Service-Account "
            "für das Projekt berechtigt ist und ob die Firestore API aktiviert ist."
        ) from exc

    existing_data = existing_snapshot.to_dict() if existing_snapshot.exists else {}
    existing_results: Dict[str, JsonObject] = dict(existing_data.get("results", {}))

    merged_results: Dict[str, JsonObject] = dict(existing_results)
    new_results = data.get("results", {})
    for key, payload in new_results.items():
        new_payload = dict(payload)
        if key.startswith("sold"):
            merged_detail = _merge_sold_price_details(
                existing_results.get(key, {}).get("price_detail"),
                new_payload.get("price_detail"),
            )
            new_payload["price_detail"] = merged_detail
        merged_results[key] = new_payload

    payload_to_store: JsonObject = dict(existing_data)
    payload_to_store.update(data)
    payload_to_store["results"] = merged_results
    payload_to_store["source_file"] = str(path.resolve())

    try:
        doc_ref.set(payload_to_store, merge=True)
    except google_api_exceptions.PermissionDenied as exc:
        raise SystemExit(
            "Schreibzugriff auf Firestore verweigert. Stelle sicher, dass der Service-Account "
            "mindestens die Rolle 'Datastore User' besitzt."
        ) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Return parsed command line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Liest alle JSON Dateien im aktuellen Verzeichnis ein und synchronisiert "
            "diese mit einer Firestore Datenbank."
        )
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=(
            "Name der Firestore Collection, in der die Daten gespeichert werden. "
            f"Standard: {DEFAULT_COLLECTION}."
        ),
    )
    parser.add_argument(
        "--project",
        help="Optionaler GCP Projektname für den Firestore Client.",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        help=(
            "Pfad zur Service-Account JSON Datei. Wenn nicht angegeben, wird "
            "GOOGLE_APPLICATION_CREDENTIALS oder die Google Standard-Anmeldung verwendet."
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="Verzeichnis mit den JSON Dateien (Standard: aktuelles Arbeitsverzeichnis).",
    )
    return parser.parse_args(argv)


def _build_firestore_client(
    *, project: str | None, credentials_path: Path | None
) -> firestore.Client:
    """Create a Firestore client with additional credential validation."""

    if credentials_path is None:
        env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if env_path:
            sanitized = env_path.strip().strip('"\'')
            if sanitized:
                credentials_path = Path(sanitized)

    if credentials_path is not None:
        expanded = credentials_path.expanduser().resolve()
        if not expanded.exists():
            raise SystemExit(
                "Die angegebene Service-Account Datei wurde nicht gefunden:\n"
                f"  {expanded}\n"
                "Prüfe den Pfad oder verwende --credentials, um den korrekten Pfad zu übergeben."
            )
        if not expanded.is_file():
            raise SystemExit(
                "Der angegebene Service-Account Pfad verweist nicht auf eine Datei:\n"
                f"  {expanded}"
            )

        credentials = service_account.Credentials.from_service_account_file(
            str(expanded)
        )
        return firestore.Client(project=project, credentials=credentials)

    try:
        return firestore.Client(project=project)
    except google_auth_exceptions.DefaultCredentialsError as exc:
        raise SystemExit(
            "Es konnten keine Google-Anmeldedaten gefunden werden. Setze entweder "
            "GOOGLE_APPLICATION_CREDENTIALS auf die JSON-Datei eines Service Accounts "
            "oder verwende den Schalter --credentials."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    """Program entry point."""

    args = parse_args(argv)
    directory: Path = args.directory.resolve()

    if not directory.exists() or not directory.is_dir():
        raise SystemExit(f"Verzeichnis {directory} existiert nicht oder ist kein Ordner.")

    json_files = _load_json_files(directory)
    if not json_files:
        print("Keine JSON Dateien gefunden – nichts zu synchronisieren.")
        return 0

    db = _build_firestore_client(project=args.project, credentials_path=args.credentials)

    for path, data in json_files:
        print(f"Synchronisiere {path.name}...")
        sync_file(db, path, data, collection=args.collection)

    print("Synchronisation abgeschlossen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
