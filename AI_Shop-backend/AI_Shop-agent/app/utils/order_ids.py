"""Order / order-item id extraction.

Java generates orderItemId as ``{orderId}_{n}`` (e.g. ``...QK_1``).
A bare order-id regex must not swallow the ``_n`` suffix when the user typed an item id.
"""

from __future__ import annotations

import re

# Production IDs are a long digit/alphanumeric order id plus an item suffix.
# The deterministic demo dataset deliberately uses shorter readable IDs; keep
# both contracts here so every caller (classifier, resolver and forced tools)
# agrees on what an order reference is.
_PRODUCTION_ORDER_ID = r"\d{10,}[A-Za-z0-9]{8,}"
_DEMO_ORDER_ID = r"SM\d{12,}"
_DEMO_ORDER_ITEM_ID = r"SMITEM\d{12,}"

_ORDER_ITEM_ID_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_DEMO_ORDER_ITEM_ID}|{_PRODUCTION_ORDER_ID}_\d+)(?![A-Za-z0-9_])",
    re.I,
)
_ORDER_ID_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_DEMO_ORDER_ID}|{_PRODUCTION_ORDER_ID})(?![A-Za-z0-9_])",
    re.I,
)


def extract_order_item_id(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        m = _ORDER_ITEM_ID_RE.search(str(text))
        if m:
            return m.group(1)
    return None


def extract_order_id(*texts: str | None) -> str | None:
    """Return orderId. If text has orderItemId ``xxx_1``, return ``xxx``."""
    item = extract_order_item_id(*texts)
    if item:
        if item.upper().startswith("SMITEM"):
            return "SM" + item[len("SMITEM") :]
        return item.rsplit("_", 1)[0]
    for text in texts:
        if not text:
            continue
        m = _ORDER_ID_RE.search(str(text))
        if m:
            return m.group(1)
    return None


def extract_refund_target_id(*texts: str | None) -> str | None:
    """Prefer orderItemId when present; otherwise orderId."""
    return extract_order_item_id(*texts) or extract_order_id(*texts)
