"""
Temperature-bucket parsing.

Handles the dominant Kalshi weather market format:
  "Will the highest/lowest temperature in [City] be [bucket] on [Date]?"

Bucket shapes:
  - "X°F or below"         → P(T ≤ X)
  - "between X-Y°F"        → P(X ≤ T ≤ Y)
  - "X°F or higher"        → P(T ≥ X)
  - "X°C" (exact integer)  → P(X−0.5 ≤ T ≤ X+0.5)   [Celsius degree bucket]
  - "X°C or below/higher"  → same logic, unit converted to °F internally
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

_BUCKET_CITY_RE = re.compile(
    r"Will the (highest|lowest) temperature in (.+?) be (.+?) on (.+?)(?:\?|$)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"°([FC])", re.IGNORECASE)
_BELOW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°[FC]\s+or\s+below", re.IGNORECASE)
_ABOVE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°[FC]\s+or\s+higher", re.IGNORECASE)
_RANGE_RE = re.compile(r"between\s+(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*°[FC]", re.IGNORECASE)
_EXACT_C_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*°C$", re.IGNORECASE)


@dataclass
class TempBucket:
    """Parsed temperature bucket from a Kalshi market question."""
    city: str
    is_high: bool          # True = "highest temperature", False = "lowest"
    unit: str              # "F" or "C"
    kind: str              # "below" | "range" | "exact" | "above"
    lo: Optional[float]    # lower bound in native unit (None for "below" buckets)
    hi: Optional[float]    # upper bound in native unit (None for "above" buckets)
    target_date: date


def _parse_date_text(text: str) -> Optional[date]:
    text = text.strip()
    year = date.today().year
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d", "%b %d", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if d.year == 1900:
                d = d.replace(year=year)
            # If the parsed date is in the past by more than 30 days, bump year
            if (date.today() - d).days > 30:
                d = d.replace(year=year + 1)
            return d
        except ValueError:
            continue
    return None


def _parse_event_date(question: str, market_end_date: str) -> Optional[date]:
    # Try structured bucket format first ("… on July 8")
    m = _BUCKET_CITY_RE.search(question)
    if m:
        d = _parse_date_text(m.group(4))
        if d:
            return d

    # Generic date patterns
    patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
    ]
    for pat in patterns:
        gm = re.search(pat, question)
        if gm:
            groups = [g for g in gm.groups() if g]
            d = _parse_date_text(" ".join(groups))
            if d:
                return d

    # Fall back to day before market resolution
    if market_end_date:
        try:
            end = datetime.fromisoformat(market_end_date.replace("Z", "+00:00")).date()
            return end - timedelta(days=1)
        except Exception:
            pass
    return None


def _parse_temp_bucket(question: str, target_date: date) -> Optional[TempBucket]:
    """Parse the Kalshi temperature-bucket market format."""
    m = _BUCKET_CITY_RE.search(question)
    if not m:
        return None

    is_high = m.group(1).lower() == "highest"
    city = m.group(2).strip()
    bucket_text = m.group(3).strip()

    # Determine unit
    unit_m = _UNIT_RE.search(bucket_text)
    unit = unit_m.group(1).upper() if unit_m else "F"

    # "X°F/C or below"
    below_m = _BELOW_RE.search(bucket_text)
    if below_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="below",
                          lo=None, hi=float(below_m.group(1)), target_date=target_date)

    # "X°F/C or higher"
    above_m = _ABOVE_RE.search(bucket_text)
    if above_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="above",
                          lo=float(above_m.group(1)), hi=None, target_date=target_date)

    # "between X-Y°F"
    range_m = _RANGE_RE.search(bucket_text)
    if range_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="range",
                          lo=float(range_m.group(1)), hi=float(range_m.group(2)),
                          target_date=target_date)

    # "X°C" exact integer bucket (common for Celsius markets)
    exact_m = _EXACT_C_RE.search(bucket_text)
    if exact_m:
        val = float(exact_m.group(1))
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="exact",
                          lo=val, hi=val, target_date=target_date)

    return None
