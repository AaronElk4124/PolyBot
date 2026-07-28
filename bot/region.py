"""
Event and region keys used to cap correlated exposure: one physical event
(a city's high/low on one date) and one correlated regional basket (nearby
cities' highs/lows on the same date, which move together under one weather
pattern).
"""

from __future__ import annotations

from typing import Optional

from analyzer import _parse_temp_bucket
from kalshi import CITY_REGION


def event_key(question: str, target_date) -> Optional[str]:
    """city|date|high-or-low — one physical event (e.g. 'Austin high on Jul 14')."""
    bucket = _parse_temp_bucket(question, target_date)
    if not bucket:
        return None
    return f"{bucket.city.lower()}|{bucket.target_date}|{'high' if bucket.is_high else 'low'}"


def region_key(question: str, target_date) -> Optional[str]:
    """region|date|high-or-low — the CORRELATED basket a city's event belongs to."""
    bucket = _parse_temp_bucket(question, target_date)
    if not bucket:
        return None
    region = CITY_REGION.get(bucket.city)
    if not region:
        return None
    return f"{region}|{bucket.target_date}|{'high' if bucket.is_high else 'low'}"
