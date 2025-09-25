"""Retrieve the current average price for a BrickLink item using the official API.

This script expects four environment variables to be set with your BrickLink API
credentials:

* BRICKLINK_CONSUMER_KEY
* BRICKLINK_CONSUMER_SECRET
* BRICKLINK_TOKEN_VALUE
* BRICKLINK_TOKEN_SECRET

Example usage:

    python bricklink_price.py SET 75257

For ``SET`` items the script automatically appends the ``-1`` variant suffix when
it is omitted, matching BrickLink's catalog identifiers.

The script now fetches and prints all four average price combinations: new and
used items for both the stock and sold price guides. See ``python
bricklink_price.py --help`` for additional options.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, OrderedDict
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping


API_BASE_URL = "https://api.bricklink.com/api/store/v1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Fetch the current average price for a BrickLink catalog item.",
    )
    parser.add_argument(
        "item_type",
        help=(
            "The type of the item, e.g. PART, MINIFIG, SET, BOOK, GEAR, CATALOG, "
            "INSTRUCTION, UNSORTED_LOT, or ORIGINAL_BOX."
        ),
    )
    parser.add_argument(
        "item_no",
        help="The item number (e.g. 3001, 75257-1).",
    )
    parser.add_argument(
        "--currency-code",
        help="Optional currency code (e.g. EUR, USD). Defaults to your store currency.",
    )
    return parser.parse_args(argv)


def _percent_encode(value: Any) -> str:
    """Percent-encode a string for OAuth 1.0 signatures."""

    return urllib.parse.quote(str(value), safe="~-._")

def _build_oauth1_header(
    method: str,
    url: str,
    params: Dict[str, Any],
    consumer_key: str,
    consumer_secret: str,
    token_value: str,
    token_secret: str,
) -> str:
    """Return the OAuth1 Authorization header value for the given request."""

    oauth_params: Dict[str, Any] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token_value,
        "oauth_version": "1.0",
    }

    parsed_url = urllib.parse.urlsplit(url)
    normalized_url = urllib.parse.urlunsplit(
        (parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", "")
    )

    signature_params = []
    for key, value in params.items():
        signature_params.append((_percent_encode(key), _percent_encode(value)))
    for key, value in oauth_params.items():
        signature_params.append((_percent_encode(key), _percent_encode(value)))
    signature_params.sort()

    parameter_string = "&".join(f"{key}={value}" for key, value in signature_params)
    signature_base = "&".join(
        [
            method.upper(),
            _percent_encode(normalized_url),
            _percent_encode(parameter_string),
        ]
    )

    signing_key = "&".join(
        [_percent_encode(consumer_secret), _percent_encode(token_secret)]
    )
    digest = hmac.new(
        signing_key.encode("utf-8"), signature_base.encode("utf-8"), hashlib.sha1
    ).digest()
    oauth_signature = base64.b64encode(digest).decode("ascii")

    oauth_params["oauth_signature"] = oauth_signature
    header_params = ", ".join(
        f"{key}=\"{_percent_encode(value)}\"" for key, value in sorted(oauth_params.items())
    )
    return f"OAuth {header_params}"

def fetch_price_data(
    item_type: str,
    item_no: str,
    guide_type: str,
    condition: str,
    currency_code: str | None,
) -> Mapping[str, Any]:
    """Return the price data payload for the given item and configuration.

    Raises ``RuntimeError`` if the API response does not contain the expected data.
    """

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
        raise RuntimeError(
            "Missing BrickLink API credentials: " + ", ".join(missing)
        )

    normalized_item_type = item_type.upper()
    normalized_item_no = item_no
    if normalized_item_type == "SET" and "-" not in normalized_item_no:
        normalized_item_no = f"{normalized_item_no}-1"

    url = f"{API_BASE_URL}/items/{normalized_item_type}/{normalized_item_no}/price"
    params: Dict[str, Any] = {
        "guide_type": guide_type,
        "new_or_used": condition,
    }
    if currency_code:
        params["currency_code"] = currency_code

    query_string = urllib.parse.urlencode(sorted(params.items()))
    request_url = f"{url}?{query_string}" if query_string else url
    headers = {
        "Authorization": _build_oauth1_header(
            "GET",
            url,
            params,
            consumer_key,
            consumer_secret,
            token_value,
            token_secret,
        )
    }

    request = urllib.request.Request(request_url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        error_message = f"BrickLink API request failed with status {exc.code}."
        try:
            error_body = exc.read()
        except Exception:  # pragma: no cover - best effort error reporting
            error_body = b""

        if error_body:
            try:
                error_payload = json.loads(error_body.decode("utf-8"))
                meta = error_payload.get("meta") if isinstance(error_payload, dict) else None
                meta_message = meta.get("message") if isinstance(meta, dict) else None
                if meta_message:
                    error_message = f"{error_message} Message: {meta_message}."
            except (ValueError, UnicodeDecodeError):
                decoded_body = error_body.decode("utf-8", errors="replace").strip()
                if decoded_body:
                    error_message = f"{error_message} Response: {decoded_body}."

        raise RuntimeError(error_message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Unable to reach the BrickLink API.") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Failed to decode BrickLink API response as JSON.") from exc

    if data.get("meta", {}).get("code") != 200:
        raise RuntimeError(
            f"BrickLink API error: {data.get('meta', {}).get('message', 'Unknown error')}"
        )

    try:
        price_data = data["data"]
        if not isinstance(price_data, dict):
            raise TypeError
        return price_data
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise RuntimeError("Unexpected API response format.") from exc


def _extract_average_price(price_data: Mapping[str, Any]) -> float:
    """Return the numeric average price from the BrickLink payload."""

    try:
        avg_price = price_data.get("avg_price") or price_data["qty_avg_price"]
    except KeyError as exc:  # pragma: no cover - defensive
        raise RuntimeError("Price data missing average price information.") from exc

    try:
        return float(avg_price)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise RuntimeError("Average price value is not numeric.") from exc


def _compute_monthly_averages(
    price_detail: Iterable[Mapping[str, Any]],
    date_field: str = "date",
) -> "OrderedDict[str, float]":
    """Compute arithmetic mean of the unit prices grouped by month."""

    monthly_totals: Dict[str, list[float]] = defaultdict(list)

    for entry in price_detail:
        if not isinstance(entry, Mapping):
            continue

        date_str = entry.get(date_field)
        unit_price = entry.get("unit_price")
        if not date_str or unit_price in (None, ""):
            continue

        date_text = str(date_str)
        if date_text.endswith("Z"):
            date_text = date_text[:-1] + "+00:00"

        try:
            parsed_date = datetime.fromisoformat(date_text)
        except ValueError:
            continue

        try:
            unit_price_float = float(unit_price)
        except (TypeError, ValueError):
            continue

        month_key = parsed_date.strftime("%Y-%m")
        monthly_totals[month_key].append(unit_price_float)

    ordered_months = OrderedDict()
    for month in sorted(monthly_totals):
        prices = monthly_totals[month]
        if prices:
            ordered_months[month] = sum(prices) / len(prices)

    return ordered_months


def _sanitize_filename_part(value: str) -> str:
    """Return a filesystem-friendly representation of the given identifier."""

    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    combinations = [
        ("stock", "N"),
        ("stock", "U"),
        ("sold", "N"),
        ("sold", "U"),
    ]

    prices: Dict[tuple[str, str], Dict[str, Any]] = {}
    try:
        for guide_type, condition in combinations:
            price_data = fetch_price_data(
                args.item_type,
                args.item_no,
                guide_type,
                condition,
                args.currency_code,
            )
            average_price = _extract_average_price(price_data)
            price_detail = price_data.get("price_detail") or []
            if guide_type == "sold":
                monthly_averages = _compute_monthly_averages(
                    price_detail, date_field="date_ordered"
                )
            else:
                monthly_averages = OrderedDict()
            prices[(guide_type, condition)] = {
                "average_price": average_price,
                "price_detail": price_detail,
                "monthly_averages": monthly_averages,
            }
    except Exception as exc:  # pragma: no cover - CLI error handling
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    condition_label = {"N": "New", "U": "Used"}
    for guide_type in ("stock", "sold"):
        print(f"Average {guide_type} prices for {args.item_type} {args.item_no}:")
        for condition in ("N", "U"):
            data = prices[(guide_type, condition)]
            price = data["average_price"]
            print(f"  {condition_label[condition]}: {price:.2f}")

            if guide_type == "sold":
                monthly_averages = data["monthly_averages"]
                if monthly_averages:
                    print("    Monthly averages:")
                    for month, month_avg in monthly_averages.items():
                        print(f"      {month}: {month_avg:.2f}")
                else:
                    print("    Monthly averages: No sold price detail available.")

    sanitized_type = _sanitize_filename_part(args.item_type.upper())
    sanitized_no = _sanitize_filename_part(args.item_no)
    filename = f"{sanitized_type}_{sanitized_no}.json"

    output = {
        "item_type": args.item_type,
        "item_no": args.item_no,
        "currency_code": args.currency_code,
        "results": {
            f"{guide_type}_{condition}": {
                "average_price": data["average_price"],
                "monthly_averages": data["monthly_averages"],
                "price_detail": data["price_detail"],
            }
            for (guide_type, condition), data in prices.items()
        },
    }

    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(output, file, indent=2, ensure_ascii=False)
    except OSError as exc:  # pragma: no cover - filesystem errors
        print(f"Error writing {filename}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
