"""Generate a Tailwind CSS HTML overview for all Firestore items."""
from __future__ import annotations

import argparse
import html
import json
import os
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Return parsed command line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Liest alle Dokumente aus einer Firestore Collection und erzeugt eine "
            "Tailwind HTML-\u00dcbersicht inklusive Preisdiagrammen pro Artikel."
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


def _format_result_summary(key: str, payload: Mapping[str, Any]) -> str:
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
        summary_fields.append("Keine Kennzahlen verf\u00fcgbar")

    return (
        f"<li class=\"py-1\"><span class=\"font-medium text-slate-700\">"
        f"{_escape(key.replace('_', ' ').title())}:</span> "
        f"<span class=\"text-slate-600\">{', '.join(summary_fields)}</span></li>"
    )


def _render_item_section(item: Mapping[str, Any], index: int) -> Tuple[str, Dict[str, Any] | None]:
    item_no = item.get("item_no") or item.get("item_id") or item.get("id")
    item_name = item.get("item_name") or item.get("name")
    item_type = item.get("item_type") or "Unbekannt"
    last_updated = item.get("last_updated") or item.get("updated_at")
    results = item.get("results") if isinstance(item.get("results"), Mapping) else {}

    labels, datasets = _build_chart_series(results)
    chart_config: Dict[str, Any] | None = None
    chart_placeholder = (
        f"<p class=\"text-sm text-slate-500\">Keine historischen Preisdaten vorhanden.</p>"
    )
    if labels and datasets:
        element_id = f"chart-{index}"
        chart_config = {
            "elementId": element_id,
            "labels": labels,
            "datasets": datasets,
        }
        chart_placeholder = f"<canvas id=\"{element_id}\" class=\"w-full h-64\"></canvas>"

    summary_html = ""
    if results:
        summary_items = [
            _format_result_summary(key, payload)
            for key, payload in sorted(results.items())
            if isinstance(payload, Mapping)
        ]
        if summary_items:
            summary_html = (
                "<ul class=\"text-sm divide-y divide-slate-100 border border-slate-200 "
                "rounded-md bg-slate-50\">"
                + "".join(summary_items)
                + "</ul>"
            )
    if not summary_html:
        summary_html = (
            "<p class=\"text-sm text-slate-500\">Keine zusammengefassten Preisdaten verf\u00fcgbar.</p>"
        )

    info_rows = [
        f"<div><span class=\"font-medium text-slate-700\">Nummer:</span> {_escape(item_no)}</div>",
        f"<div><span class=\"font-medium text-slate-700\">Typ:</span> {_escape(item_type)}</div>",
    ]
    if item_name:
        info_rows.append(
            f"<div><span class=\"font-medium text-slate-700\">Name:</span> {_escape(item_name)}</div>"
        )
    if last_updated:
        info_rows.append(
            f"<div><span class=\"font-medium text-slate-700\">Zuletzt aktualisiert:</span> {_escape(last_updated)}</div>"
        )

    info_html = (
        "<div class=\"grid gap-2 text-sm text-slate-600\">" + "".join(info_rows) + "</div>"
    )

    section_html = (
        "<section class=\"bg-white border border-slate-200 rounded-lg shadow-sm p-6 "
        "space-y-4\">"
        f"<div class=\"flex flex-col gap-1\">"
        f"<h2 class=\"text-xl font-semibold text-slate-800\">{_escape(item_no)}"
        f"{f' - {_escape(item_name)}' if item_name else ''}</h2>"
        f"<span class=\"text-sm text-slate-500\">{_escape(item_type)}</span>"
        "</div>"
        "<div class=\"grid gap-6 md:grid-cols-2\">"
        f"<div class=\"space-y-4\">{info_html}{summary_html}</div>"
        f"<div class=\"bg-slate-50 border border-slate-200 rounded-md p-4\">{chart_placeholder}</div>"
        "</div>"
        "</section>"
    )
    return section_html, chart_config


def _render_documentation_notice() -> str:
    return (
        "<div class=\"max-w-5xl mx-auto my-6 text-sm text-slate-500\">"
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
        "<p class=\"text-center text-slate-500\">Keine Dokumente in der Collection gefunden.</p>"
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
  <link href=\"https://cdn.jsdelivr.net/npm/tailwindcss@3.4.4/dist/tailwind.min.css\" rel=\"stylesheet\">
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js\" defer></script>
  <style>
    body {{ font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  </style>
</head>
<body class=\"bg-slate-100 min-h-screen py-10\">
  <main class=\"max-w-6xl mx-auto px-4 space-y-6\">
    <header class=\"text-center space-y-2\">
      <h1 class=\"text-3xl font-bold text-slate-900\">BrickLink Preis\u00fcbersicht</h1>
      <p class=\"text-slate-600\">Automatisch generierte HTML-Auswertung aller in Firestore gespeicherten Artikel.</p>
    </header>
    {_render_documentation_notice()}
    <div class=\"space-y-6\">{sections_html}</div>
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
