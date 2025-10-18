"""Generate a modern HTML overview for all Firestore items."""
from __future__ import annotations

import argparse
import functools
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from google.api_core import exceptions as google_api_exceptions
    from google.auth import exceptions as google_auth_exceptions
    from google.cloud import firestore
    from google.oauth2 import service_account
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Das Paket 'google-cloud-firestore' ist nicht installiert. "
        "Installiere die Abhängigkeiten z.\u202fB. mit einem virtuellen Umfeld:\n"
        "    python3 -m venv .venv && source .venv/bin/activate\n"
        "    python -m pip install -r requirements.txt"
    ) from exc


DEFAULT_COLLECTION = "bricklink_price_history"
COLOR_PALETTE = [
    "#2563eb",  # blue-600
    "#16a34a",  # green-600
    "#f97316",  # orange-500
    "#7c3aed",  # violet-600
    "#dc2626",  # red-600
    "#0891b2",  # cyan-600
]

JsonObject = Dict[str, Any]

try:
    from bricklink_price import API_BASE_URL, _build_oauth1_header
except ModuleNotFoundError:
    API_BASE_URL = "https://api.bricklink.com/api/store/v1"


_BRICKLINK_CREDENTIALS_CACHE: Tuple[str, str, str, str] | None = None
_BRICKLINK_CREDENTIALS_CHECKED = False


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Return parsed command line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Liest alle Dokumente aus einer Firestore Collection und erzeugt eine "
            "moderne HTML-\u00dcbersicht inklusive Preisdiagrammen pro Artikel."
        )
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=(
            "Name der Firestore Collection, aus der die Daten gelesen werden. "
            f"Standard: {DEFAULT_COLLECTION}."
        ),
    )
    parser.add_argument(
        "--project",
        help="Optionaler GCP Projektname f\u00fcr den Firestore Client.",
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
        "--output",
        type=Path,
        default=Path("export.html"),
        help="Zieldatei f\u00fcr die erzeugte HTML \u00dcbersicht (Standard: export.html).",
    )
    return parser.parse_args(argv)


def _get_bricklink_credentials() -> Tuple[str, str, str, str] | None:
    """Return BrickLink API credentials from the environment if available."""

    global _BRICKLINK_CREDENTIALS_CACHE, _BRICKLINK_CREDENTIALS_CHECKED

    if _BRICKLINK_CREDENTIALS_CHECKED:
        return _BRICKLINK_CREDENTIALS_CACHE

    consumer_key = os.getenv("BRICKLINK_CONSUMER_KEY")
    consumer_secret = os.getenv("BRICKLINK_CONSUMER_SECRET")
    token_value = os.getenv("BRICKLINK_TOKEN_VALUE")
    token_secret = os.getenv("BRICKLINK_TOKEN_SECRET")

    missing = [
        name
        for name, value in [
            ("BRICKLINK_CONSUMER_KEY", consumer_key),
            ("BRICKLINK_CONSUMER_SECRET", consumer_secret),
            ("BRICKLINK_TOKEN_VALUE", token_value),
            ("BRICKLINK_TOKEN_SECRET", token_secret),
        ]
        if not value
    ]

    if missing:
        print(
            "Warnung: BrickLink API Zugangsdaten fehlen (" + ", ".join(missing) +
            "). Bilder werden im Export ausgelassen.",
        )
        _BRICKLINK_CREDENTIALS_CACHE = None
    else:
        _BRICKLINK_CREDENTIALS_CACHE = (
            consumer_key or "",
            consumer_secret or "",
            token_value or "",
            token_secret or "",
        )

    _BRICKLINK_CREDENTIALS_CHECKED = True
    return _BRICKLINK_CREDENTIALS_CACHE


def _normalize_item_identifiers(
    item_type: Any, item_no: Any
) -> Tuple[str, str] | None:
    """Return normalized BrickLink item identifiers or ``None`` if invalid."""

    if not item_type or not item_no:
        return None

    normalized_type = str(item_type).strip().upper()
    normalized_no = str(item_no).strip()

    if not normalized_type or not normalized_no:
        return None

    if normalized_type == "SET" and "-" not in normalized_no:
        normalized_no = f"{normalized_no}-1"

    return normalized_type, normalized_no


@functools.lru_cache(maxsize=256)
def _request_bricklink_item_details(
    normalized_type: str, normalized_no: str
) -> Mapping[str, Any] | None:
    """Fetch catalog information for an item from the BrickLink API."""

    credentials = _get_bricklink_credentials()
    if credentials is None:
        return None

    consumer_key, consumer_secret, token_value, token_secret = credentials

    url = f"{API_BASE_URL}/items/{urllib.parse.quote(normalized_type)}/{urllib.parse.quote(normalized_no)}"
    headers = {
        "Authorization": _build_oauth1_header(
            "GET",
            url,
            {},
            consumer_key,
            consumer_secret,
            token_value,
            token_secret,
        )
    }

    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            print(
                "Warnung: BrickLink API Anfrage für"
                f" {normalized_type} {normalized_no} schlug fehl (Status {exc.code})."
            )
        return None
    except urllib.error.URLError as exc:
        print(
            "Warnung: BrickLink API konnte nicht erreicht werden:"
            f" {exc.reason}. Bilder werden ausgelassen."
        )
        return None

    try:
        response_data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(
            "Warnung: Unerwartete Antwort der BrickLink API für"
            f" {normalized_type} {normalized_no}."
        )
        return None

    if not isinstance(response_data, Mapping):
        return None

    meta = response_data.get("meta")
    if isinstance(meta, Mapping) and meta.get("code") not in (None, 200):
        message = meta.get("message") or "Unbekannter Fehler"
        print(
            "Warnung: BrickLink API meldet Fehler für"
            f" {normalized_type} {normalized_no}: {message}"
        )
        return None

    data = response_data.get("data")
    return data if isinstance(data, Mapping) else None


def _resolve_bricklink_image_url(item: Mapping[str, Any]) -> str | None:
    """Return the BrickLink image URL for an item if retrievable."""

    normalized = _normalize_item_identifiers(
        item.get("item_type") or item.get("type"),
        item.get("item_no") or item.get("item_id") or item.get("id"),
    )
    if not normalized:
        return None

    details = _request_bricklink_item_details(*normalized)
    if not details:
        return None

    image_url = details.get("image_url") or details.get("thumbnail_url")
    if isinstance(image_url, str) and image_url.strip():
        sanitized = image_url.strip()
        if sanitized.startswith("//"):
            sanitized = "https:" + sanitized
        return sanitized
    return None


def _validate_project_id(value: str, *, hint: str | None = None) -> str:
    import re

    pattern = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    candidate = value.strip()
    if not candidate:
        raise SystemExit("Die angegebene Projekt-ID ist leer.")
    if not pattern.match(candidate):
        extra = f" Vielleicht meinst du '{hint}'." if hint else ""
        raise SystemExit(
            "Ung\u00fcltige Firestore Projekt-ID. Verwende die Projekt-ID, nicht den Anzeigenamen."
            + extra
        )
    return candidate


def _build_firestore_client(
    *, project: str | None, credentials_path: Path | None
) -> firestore.Client:
    """Create a Firestore client with additional credential validation."""

    credentials_project: str | None = None

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
                "Pr\u00fcfe den Pfad oder verwende --credentials, um den korrekten Pfad zu \u00fcbergeben."
            )
        if not expanded.is_file():
            raise SystemExit(
                "Der angegebene Service-Account Pfad verweist nicht auf eine Datei:\n"
                f"  {expanded}"
            )

        with expanded.open("r", encoding="utf-8") as credentials_file:
            credentials_info = json.load(credentials_file)
        credentials_project = credentials_info.get("project_id")
        if credentials_project:
            credentials_project = _validate_project_id(credentials_project)

        credentials = service_account.Credentials.from_service_account_file(
            str(expanded)
        )
        normalized_project = (
            _validate_project_id(project, hint=credentials_project)
            if project
            else credentials_project
        )
        if credentials_project and normalized_project and normalized_project != credentials_project:
            print(
                "Hinweis: Die angegebene Projekt-ID weicht von der project_id der Service-Account-Datei ab."
            )
        return firestore.Client(project=normalized_project, credentials=credentials)

    normalized_project = _validate_project_id(project) if project else None
    try:
        return firestore.Client(project=normalized_project)
    except google_auth_exceptions.DefaultCredentialsError as exc:
        raise SystemExit(
            "Es konnten keine Google-Anmeldedaten gefunden werden. Setze entweder "
            "GOOGLE_APPLICATION_CREDENTIALS auf die JSON-Datei eines Service Accounts "
            "oder verwende den Schalter --credentials."
        ) from exc


def _escape(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _parse_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _normalize_month(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%Y/%m"):
        try:
            dt = datetime.strptime(text[: len(fmt)], fmt)
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.strftime("%Y-%m")


def _aggregate_price_details(details: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    monthly_values: MutableMapping[str, List[float]] = defaultdict(list)
    for entry in details:
        month = _normalize_month(entry.get("date_ordered"))
        if not month:
            continue
        unit_price = _parse_float(entry.get("unit_price"))
        if unit_price is None:
            unit_price = _parse_float(entry.get("price"))
        if unit_price is None:
            unit_price = _parse_float(entry.get("unit_sale_price"))
        if unit_price is None:
            continue
        monthly_values[month].append(unit_price)

    aggregated: Dict[str, float] = {}
    for month, values in monthly_values.items():
        if values:
            aggregated[month] = sum(values) / len(values)
    return dict(sorted(aggregated.items()))


def _build_chart_series(results: Mapping[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    series_data: List[Tuple[str, Dict[str, float], str]] = []
    palette_cycle = iter(COLOR_PALETTE)

    for key, payload in sorted(results.items()):
        if not isinstance(payload, Mapping):
            continue
        price_detail = payload.get("price_detail")
        if not isinstance(price_detail, list):
            continue
        aggregated = _aggregate_price_details(price_detail)
        if not aggregated:
            continue
        try:
            color = next(palette_cycle)
        except StopIteration:
            palette_cycle = iter(COLOR_PALETTE)
            color = next(palette_cycle)
        label = key.replace("_", " ").title()
        series_data.append((label, aggregated, color))

    if not series_data:
        return [], []

    all_months = sorted({month for _, data, _ in series_data for month in data})
    datasets: List[Dict[str, Any]] = []
    for label, data, color in series_data:
        datasets.append(
            {
                "label": label,
                "data": [data.get(month) for month in all_months],
                "borderColor": color,
                "backgroundColor": color + "33",
                "tension": 0.3,
                "spanGaps": True,
                "fill": False,
            }
        )
    return all_months, datasets


def _format_result_summary(key: str, payload: Mapping[str, Any]) -> str | None:
    summary_fields = []
    avg_price = payload.get("avg_price") or payload.get("avg_sale_price")
    currency = payload.get("currency_code")
    if avg_price is not None:
        price_value = _parse_float(avg_price)
        if price_value is not None and currency:
            summary_fields.append(f"Durchschnitt: {price_value:.2f} {currency}")
        elif price_value is not None:
            summary_fields.append(f"Durchschnitt: {price_value:.2f}")
        else:
            summary_fields.append(f"Durchschnitt: {_escape(avg_price)}")

    total_qty = payload.get("total_qty") or payload.get("total_quantity")
    if total_qty is not None:
        summary_fields.append(f"Menge: {_escape(total_qty)}")

    if payload.get("qty_avg_price") is not None:
        qty_price = _parse_float(payload.get("qty_avg_price"))
        if qty_price is not None and currency:
            summary_fields.append(f"Durchschnitt (Menge): {qty_price:.2f} {currency}")
        elif qty_price is not None:
            summary_fields.append(f"Durchschnitt (Menge): {qty_price:.2f}")

    if not summary_fields:
        return None

    return (
        "<li class=\"summary-item\">"
        f"<span class=\"summary-key\">{_escape(key.replace('_', ' ').title())}:</span> "
        f"<span class=\"summary-values\">{', '.join(summary_fields)}</span>"
        "</li>"
    )


def _render_item_section(item: Mapping[str, Any], index: int) -> Tuple[str, Dict[str, Any] | None]:
    item_no = item.get("item_no") or item.get("item_id") or item.get("id")
    item_name = item.get("item_name") or item.get("name")
    item_type = item.get("item_type") or "Unbekannt"
    last_updated = item.get("last_updated") or item.get("updated_at")
    results = item.get("results") if isinstance(item.get("results"), Mapping) else {}
    image_url = _resolve_bricklink_image_url(item)

    labels, datasets = _build_chart_series(results)
    chart_config: Dict[str, Any] | None = None
    chart_placeholder = (
        "<p class=\"chart-empty\">Keine historischen Preisdaten vorhanden.</p>"
    )
    if labels and datasets:
        element_id = f"chart-{index}"
        chart_config = {
            "elementId": element_id,
            "labels": labels,
            "datasets": datasets,
        }
        chart_placeholder = (
            f"<canvas id=\"{element_id}\" class=\"chart-canvas\"></canvas>"
        )

    summary_html = ""
    if results:
        summary_items = []
        for key, payload in sorted(results.items()):
            if not isinstance(payload, Mapping):
                continue
            summary_item = _format_result_summary(key, payload)
            if summary_item:
                summary_items.append(summary_item)
        if summary_items:
            summary_html = (
                "<ul class=\"summary-list\">"
                + "".join(summary_items)
                + "</ul>"
            )
    if not summary_html:
        summary_html = (
            "<p class=\"summary-empty\">Keine zusammengefassten Preisdaten verf\u00fcgbar.</p>"
        )

    info_rows = [
        "<div class=\"info-row\">"
        f"<span class=\"info-label\">Nummer:</span><span>{_escape(item_no)}</span>"
        "</div>",
        "<div class=\"info-row\">"
        f"<span class=\"info-label\">Typ:</span><span>{_escape(item_type)}</span>"
        "</div>",
    ]
    if item_name:
        info_rows.append(
            "<div class=\"info-row\">"
            f"<span class=\"info-label\">Name:</span><span>{_escape(item_name)}</span>"
            "</div>"
        )
    if last_updated:
        info_rows.append(
            "<div class=\"info-row\">"
            f"<span class=\"info-label\">Zuletzt aktualisiert:</span><span>{_escape(last_updated)}</span>"
            "</div>"
        )

    info_html = (
        "<div class=\"info-grid\">" + "".join(info_rows) + "</div>"
    )

    image_html = ""
    if image_url:
        alt_parts = [str(item_no or "").strip()]
        if item_name:
            alt_parts.append(str(item_name))
        alt_text = " - ".join(part for part in alt_parts if part)
        image_html = (
            "<div class=\"item-card__media\">"
            f"<img src=\"{_escape(image_url)}\" alt=\"{_escape(alt_text or 'Artikelbild')}\" "
            "class=\"item-card__image\" loading=\"lazy\"></div>"
        )

    section_html = (
        "<section class=\"item-card\">"
        "<div class=\"item-card__header\">"
        f"<h2 class=\"item-card__title\">{_escape(item_no)}"
        f"{f' - {_escape(item_name)}' if item_name else ''}</h2>"
        f"<span class=\"item-card__subtitle\">{_escape(item_type)}</span>"
        "</div>"
        "<div class=\"item-card__content\">"
        f"<div class=\"card-column\">{image_html}{info_html}{summary_html}</div>"
        f"<div class=\"chart-container\">{chart_placeholder}</div>"
        "</div>"
        "</section>"
    )
    return section_html, chart_config


def _render_documentation_notice() -> str:
    return (
        "<div class=\"notice\">"
        "Die dargestellten Werte basieren auf den in Firestore gespeicherten "
        "Durchschnittspreisen. Die Grafiken stellen gemittelte Einheitspreise pro Monat dar."
        "</div>"
    )


def render_html(items: Sequence[Mapping[str, Any]]) -> str:
    sections: List[str] = []
    chart_configs: List[Dict[str, Any]] = []

    for index, item in enumerate(items):
        section_html, chart_config = _render_item_section(item, index)
        sections.append(section_html)
        if chart_config:
            chart_configs.append(chart_config)

    charts_json = json.dumps(chart_configs, ensure_ascii=False)
    chart_data_json = charts_json.replace("</", "<\\/")

    sections_html = "".join(sections) if sections else (
        "<p class=\"empty-message\">Keine Dokumente in der Collection gefunden.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>BrickLink Preis\u00fcbersicht</title>
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap\" rel=\"stylesheet\">
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js\" defer></script>
  <style>
    :root {{
      color-scheme: light;
      font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}
    *, *::before, *::after {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: #0f172a;
      background: linear-gradient(135deg, #f1f5f9 0%, #ffffff 55%, #e2e8f0 100%);
    }}
    .page-main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 3.5rem 1.5rem 4rem;
      display: flex;
      flex-direction: column;
      gap: 2.5rem;
    }}
    .page-header {{
      text-align: center;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    .page-title {{
      margin: 0;
      font-size: clamp(2rem, 3vw, 2.75rem);
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #0f172a;
    }}
    .page-subtitle {{
      margin: 0 auto;
      max-width: 640px;
      font-size: 1rem;
      line-height: 1.6;
      color: #475569;
    }}
    .notice {{
      max-width: 720px;
      margin: -0.5rem auto 0;
      font-size: 0.85rem;
      line-height: 1.5;
      color: #64748b;
      text-align: center;
    }}
    .item-grid {{
      display: grid;
      gap: 1.75rem;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      justify-content: center;
    }}
    .empty-message {{
      grid-column: 1 / -1;
      text-align: center;
      font-size: 1rem;
      color: #94a3b8;
      margin: 2rem 0;
    }}
    .item-card {{
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid rgba(148, 163, 184, 0.35);
      border-radius: 1.25rem;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
      box-shadow: 0 18px 40px rgba(15, 23, 42, 0.1);
      backdrop-filter: blur(6px);
      transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
    }}
    .item-card:hover {{
      transform: translateY(-4px);
      border-color: rgba(148, 163, 184, 0.55);
      box-shadow: 0 24px 48px rgba(15, 23, 42, 0.15);
    }}
    .item-card__header {{
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }}
    .item-card__title {{
      margin: 0;
      font-size: 1.25rem;
      font-weight: 600;
      letter-spacing: -0.01em;
      color: #1e293b;
    }}
    .item-card__subtitle {{
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #64748b;
    }}
    .item-card__content {{
      display: grid;
      gap: 1.75rem;
      grid-template-columns: minmax(0, 1fr);
      align-items: start;
    }}
    @media (min-width: 768px) {{
      .item-card__content {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    .card-column {{
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }}
    .item-card__media {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0.75rem;
      background: linear-gradient(135deg, rgba(248, 250, 252, 0.9), rgba(226, 232, 240, 0.6));
      border: 1px solid rgba(203, 213, 225, 0.8);
      border-radius: 0.9rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
      min-height: 140px;
    }}
    .item-card__image {{
      width: 100%;
      max-width: 160px;
      max-height: 140px;
      height: auto;
      object-fit: contain;
    }}
    .info-grid {{
      display: grid;
      gap: 0.65rem;
      font-size: 0.95rem;
      color: #475569;
    }}
    .info-row {{
      display: flex;
      gap: 0.35rem;
      flex-wrap: wrap;
    }}
    .info-label {{
      font-weight: 600;
      color: #1f2937;
    }}
    .summary-list {{
      list-style: none;
      margin: 0;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      background: linear-gradient(135deg, rgba(248, 250, 252, 0.95), rgba(226, 232, 240, 0.7));
      border: 1px solid rgba(203, 213, 225, 0.7);
      border-radius: 1rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
    }}
    .summary-item {{
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }}
    .summary-key {{
      font-weight: 600;
      color: #1e293b;
    }}
    .summary-values {{
      color: #475569;
    }}
    .summary-empty {{
      margin: 0;
      font-size: 0.95rem;
      color: #94a3b8;
    }}
    .chart-container {{
      background: linear-gradient(135deg, rgba(248, 250, 252, 0.85), rgba(226, 232, 240, 0.65));
      border: 1px solid rgba(203, 213, 225, 0.8);
      border-radius: 1rem;
      padding: 1.25rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
      min-height: 260px;
      display: flex;
      align-items: center;
      justify-content: center;
      grid-column: 1 / -1;
    }}
    .chart-canvas {{
      width: 100%;
      height: 320px;
    }}
    .chart-empty {{
      margin: 0;
      font-size: 0.9rem;
      color: #94a3b8;
      text-align: center;
    }}
  </style>
</head>
<body>
  <main class=\"page-main\">
    <header class=\"page-header\">
      <h1 class=\"page-title\">BrickLink Preis\u00fcbersicht</h1>
      <p class=\"page-subtitle\">Automatisch generierte HTML-Auswertung aller in Firestore gespeicherten Artikel.</p>
    </header>
    {_render_documentation_notice()}
    <div class=\"item-grid\">{sections_html}</div>
  </main>
  <script>
    document.addEventListener('DOMContentLoaded', () => {{
      const charts = {chart_data_json};
      charts.forEach((config) => {{
        const canvas = document.getElementById(config.elementId);
        if (!canvas) {{
          return;
        }}
        new window.Chart(canvas, {{
          type: 'line',
          data: {{
            labels: config.labels,
            datasets: config.datasets.map((dataset) => ({{
              ...dataset,
              data: dataset.data.map((value) => (value === null || value === undefined) ? null : Number(value)),
            }})),
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{ mode: 'index', intersect: false }},
            plugins: {{
              legend: {{ display: config.datasets.length > 1 }},
              tooltip: {{
                callbacks: {{
                  label: (context) => {{
                    const value = context.parsed.y;
                    if (value === null || value === undefined) {{
                      return `${{context.dataset.label}}: keine Daten`;
                    }}
                    return `${{context.dataset.label}}: ${{value.toFixed(2)}}`;
                  }},
                }},
              }},
            }},
            scales: {{
              y: {{
                ticks: {{ callback: (value) => value.toFixed ? value.toFixed(2) : value }},
                beginAtZero: false,
              }},
            }},
          }},
        }});
      }});
    }});
  </script>
</body>
</html>
"""


def _fetch_items(db: firestore.Client, collection: str) -> List[JsonObject]:
    try:
        documents = list(db.collection(collection).stream())
    except google_api_exceptions.PermissionDenied as exc:
        raise SystemExit(
            "Kein Zugriff auf das Firestore-Projekt. Pr\u00fcfe Berechtigungen und Projekt-ID."
        ) from exc

    items: List[JsonObject] = []
    for doc in documents:
        data = doc.to_dict() or {}
        data.setdefault("id", doc.id)
        items.append(data)
    items.sort(key=lambda item: (str(item.get("item_type") or ""), str(item.get("item_no") or item.get("id") or "")))
    return items


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    db = _build_firestore_client(project=args.project, credentials_path=args.credentials)
    items = _fetch_items(db, args.collection)
    html_output = render_html(items)

    output_path = args.output.expanduser().resolve()
    output_path.write_text(html_output, encoding="utf-8")
    print(f"HTML-\u00dcbersicht in {output_path} gespeichert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
