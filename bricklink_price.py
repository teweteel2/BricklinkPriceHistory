"""Retrieve the current average price for a BrickLink item using the official API.

This script expects four environment variables to be set with your BrickLink API
credentials:

* BRICKLINK_CONSUMER_KEY
* BRICKLINK_CONSUMER_SECRET
* BRICKLINK_TOKEN_VALUE
* BRICKLINK_TOKEN_SECRET

Example usage:

    python bricklink_price.py SET 75257

By default the script requests the average *sold* price, but this can be changed
with the ``--guide-type`` option. See ``python bricklink_price.py --help`` for
additional options.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict

try:
    import requests
except ModuleNotFoundError as exc:  # pragma: no cover - defensive import guard
    raise SystemExit(
        "The 'requests' package is required to run this script. "
        "Install it with 'pip install requests'."
    ) from exc

try:
    from requests_oauthlib import OAuth1
except ModuleNotFoundError as exc:  # pragma: no cover - defensive import guard
    raise SystemExit(
        "The 'requests_oauthlib' package is required to run this script. "
        "Install it with 'pip install requests_oauthlib'."
    ) from exc


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
        )
    )
    parser.add_argument(
        "item_no",
        help="The item number (e.g. 3001, 75257-1).",
    )
    parser.add_argument(
        "--new-or-used",
        choices=["N", "U"],
        default="N",
        help="Filter by condition: 'N' for new items, 'U' for used items (default: %(default)s).",
    )
    parser.add_argument(
        "--guide-type",
        choices=["stock", "sold"],
        default="sold",
        help=(
            "Which price guide to use: 'sold' for the sold lot price guide or 'stock' for the "
            "current stock price guide (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--currency-code",
        help="Optional currency code (e.g. EUR, USD). Defaults to your store currency.",
    )
    return parser.parse_args(argv)


def fetch_average_price(args: argparse.Namespace) -> float:
    """Call the BrickLink API and return the requested average price.

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

    auth = OAuth1(consumer_key, consumer_secret, token_value, token_secret)

    url = f"{API_BASE_URL}/items/{args.item_type}/{args.item_no}/price"
    params: Dict[str, Any] = {
        "guide_type": args.guide_type,
        "new_or_used": args.new_or_used,
    }
    if args.currency_code:
        params["currency_code"] = args.currency_code

    response = requests.get(url, params=params, auth=auth, timeout=30)
    response.raise_for_status()

    payload = response.json()
    if payload.get("meta", {}).get("code") != 200:
        raise RuntimeError(
            f"BrickLink API error: {payload.get('meta', {}).get('message', 'Unknown error')}"
        )

    try:
        data = payload["data"]
        # Prefer avg_price if available; fall back to qty_avg_price (average per lot) otherwise.
        return float(data.get("avg_price") or data["qty_avg_price"])
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise RuntimeError("Unexpected API response format.") from exc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        avg_price = fetch_average_price(args)
    except Exception as exc:  # pragma: no cover - CLI error handling
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Average {args.guide_type} price for {args.item_type} {args.item_no} "
        f"({args.new_or_used}): {avg_price:.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
