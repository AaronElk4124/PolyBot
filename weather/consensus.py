"""
Consensus forecast building — the class that owns the TTL cache + single-
flight locks that used to be weather.py module globals.
"""

from __future__ import annotations

import dataclasses
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ._locking import lock_for
from .coords import _WU_STATION_RE, _find_city_coords, _station_to_coords, _extract_city_heuristic, _geocode
from .intraday import intraday_estimate
from .models import DailyForecast
from .sources.met_norway import _met_norway_forecast
from .sources.nws import _nws_forecast
from .sources.open_meteo import _open_meteo_ensemble, _open_meteo_forecast

CONSENSUS_TTL_S = 600   # NWS/OM/MET/ensemble update at most hourly


class ForecastProvider:
    """
    Given a market question + target date, returns a consensus DailyForecast
    (NWS + Open-Meteo + MET Norway, blended) with the intraday observed-
    narrowing overlay applied fresh on every call for same-day markets.
    Results are TTL-cached by (station-or-city, date); the cache survives
    across scans (only expired entries are dropped) and a per-key lock
    prevents parallel analyzer threads from rebuilding the same key at once.
    """

    def __init__(self) -> None:
        # cache_key → (fetched_at_utc, DailyForecast | None)  [static consensus, no intraday]
        self._forecast_cache: dict[str, tuple[datetime, Optional[DailyForecast]]] = {}

    def clear_expired(self) -> None:
        """Drop only EXPIRED entries. Fresh consensus forecasts survive scans."""
        now = datetime.now(timezone.utc)
        for k in [k for k, (ts, _) in self._forecast_cache.items()
                  if (now - ts).total_seconds() > CONSENSUS_TTL_S]:
            del self._forecast_cache[k]

    def _cache_get(self, key: str) -> tuple[bool, Optional[DailyForecast]]:
        hit = self._forecast_cache.get(key)
        if hit is None:
            return False, None
        ts, fc = hit
        if (datetime.now(timezone.utc) - ts).total_seconds() > CONSENSUS_TTL_S:
            return False, None
        return True, fc

    def _cache_put(self, key: str, fc: Optional[DailyForecast]) -> None:
        self._forecast_cache[key] = (datetime.now(timezone.utc), fc)

    def _consensus_cached(self, cache_key: str, builder) -> Optional[DailyForecast]:
        """TTL cache + single-flight: only one thread builds a missing key."""
        hit, fc = self._cache_get(cache_key)
        if hit:
            return fc
        with lock_for(cache_key):
            hit, fc = self._cache_get(cache_key)   # re-check under the lock
            if hit:
                return fc
            fc = builder()
            self._cache_put(cache_key, fc)
            return fc

    def _build_consensus_forecast(
        self, lat: float, lon: float, is_us: bool, target_date: date, city_name: str,
        station_id: str = "",
    ) -> Optional[DailyForecast]:
        """
        Consensus across THREE independent forecasts plus observed reality:
          - NWS         (GFS model, US only)
          - Open-Meteo  (GFS model)
          - MET Norway  (ECMWF model — independent of GFS, the real tiebreaker)
        For same-day markets we also blend toward the OBSERVED trend (METAR), since
        a morning forecast can lag a marine-layer stall the market already sees.

        Consensus = mean of available sources; σ = max(ensemble spread, source spread).
        sources_agree = all sources within 3°F of each other.
        """
        # Fetch all independent sources concurrently — 4 network calls in parallel
        # instead of serial, cutting per-city forecast latency ~4x.
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_nws = ex.submit(_nws_forecast, lat, lon, target_date) if is_us else None
            f_om  = ex.submit(_open_meteo_forecast, lat, lon, target_date)
            f_met = ex.submit(_met_norway_forecast, lat, lon, target_date)
            f_ens = ex.submit(_open_meteo_ensemble, lat, lon, target_date)
            nws_fc   = f_nws.result() if f_nws else None
            om_fc    = f_om.result()
            met_fc   = f_met.result()
            ensemble = f_ens.result()

        highs = [f.high_f for f in (nws_fc, om_fc, met_fc) if f and f.high_f is not None]
        lows  = [f.low_f  for f in (nws_fc, om_fc, met_fc) if f and f.low_f  is not None]
        if not highs and not lows:
            return None

        high_f = sum(highs) / len(highs) if highs else None
        low_f  = sum(lows) / len(lows) if lows else None

        # Spread across the independent sources (how much they disagree)
        high_spread = (max(highs) - min(highs)) if len(highs) > 1 else 0.0
        low_spread  = (max(lows) - min(lows)) if len(lows) > 1 else 0.0
        try:
            from config import Config as _C
            _band = _C().agreement_band_f
        except Exception:
            _band = 4.5
        agrees = (high_spread <= _band) and (low_spread <= _band)

        # σ: widen with both ensemble spread and source disagreement, floor at 2°F
        high_std = max(ensemble[1] if ensemble else 3.0, high_spread / 2, 2.0)
        low_std  = max(ensemble[3] if ensemble else 3.0, low_spread / 2, 2.0)

        n = sum(1 for f in (nws_fc, om_fc, met_fc) if f)
        source = f"{n}-source"

        return DailyForecast(
            target_date=target_date, city=city_name,
            high_f=high_f, low_f=low_f,
            precip_prob=(nws_fc or om_fc).precip_prob if (nws_fc or om_fc) else 0.0,
            snow_prob=(nws_fc or om_fc).snow_prob if (nws_fc or om_fc) else 0.0,
            description=nws_fc.description if nws_fc else "",
            source=source,
            high_f_std=high_std, low_f_std=low_std,
            sources_agree=agrees,
            high_locked=False, low_locked=False,
            nws_high_f=nws_fc.high_f if nws_fc else None,
            om_high_f=om_fc.high_f   if om_fc  else None,
            nws_low_f=nws_fc.low_f   if nws_fc else None,
            om_low_f=om_fc.low_f     if om_fc  else None,
        )

    def _apply_intraday(
        self, fc: DailyForecast, lat: float, lon: float, station_id: str, target_date: date
    ) -> DailyForecast:
        """
        ── INTRADAY OVERRIDE (same-day) — THE EDGE ─────────────────────────────
        Overlay the tight observed-narrowing estimate on a (possibly cached) static
        consensus for markets resolving today. Returns a NEW DailyForecast so the
        cached consensus object is never mutated. Cheap: observed data comes from
        the batched METAR prefetch, hourly trajectory from a 15-min cache.
        """
        fresh = dataclasses.replace(fc)
        tz_off = round(lon / 15.0)
        local_today = (datetime.now(timezone.utc) + timedelta(hours=tz_off)).date()
        if not station_id or target_date != local_today:
            return fresh
        intra = intraday_estimate(lat, lon, station_id, target_date, tz_off)
        if not intra:
            return fresh
        if intra.high_f is not None:
            fresh.high_f, fresh.high_f_std, fresh.high_locked = intra.high_f, intra.high_std, intra.high_locked
        if intra.low_f is not None:
            fresh.low_f, fresh.low_f_std, fresh.low_locked = intra.low_f, intra.low_std, intra.low_locked
        fresh.description = (fc.description or "") + f" | {intra.note}"
        fresh.source = fc.source + "+intraday"
        fresh.sources_agree = True   # observed reality is our best estimate, treat as agreed
        return fresh

    def get_forecast(
        self, question: str, target_date: date, resolution_source: str = "", description: str = ""
    ) -> Optional[DailyForecast]:
        """
        Given a market question and target date, return a DailyForecast.

        If resolution_source is provided (e.g. a Weather Underground URL containing
        an ICAO station code), the forecast is fetched for that station's exact
        coordinates rather than a generic city centroid.  This matters a lot for
        coastal cities like LA where LAX airport (KLAX) is 10°F cooler than downtown.

        Results are cached by (cache_key, date).
        """
        city_name: str
        lat: float
        lon: float
        is_us: bool = False
        cache_key: str

        # 1. Try to resolve the exact station from the resolution URL first
        if resolution_source or description:
            station_result = _station_to_coords(resolution_source, description)
            if station_result:
                lat, lon, is_us = station_result
                # Extract station ID for cache key
                bare = resolution_source.strip().upper()
                if re.match(r'^[A-Z]{3,5}$', bare):
                    station_id = bare
                else:
                    sm = _WU_STATION_RE.search(resolution_source)
                    station_id = sm.group(1) if sm else resolution_source
                cache_key = f"station:{station_id}:{target_date}"
                city_name = station_id  # will be overwritten below if city found

                # Also try to get a human city name for display
                city_match = _find_city_coords(question)
                if city_match:
                    city_name = city_match[0]

                forecast = self._consensus_cached(
                    cache_key,
                    lambda: self._build_consensus_forecast(lat, lon, is_us, target_date, city_name, station_id),
                )
                if forecast is None:
                    return None
                return self._apply_intraday(forecast, lat, lon, station_id, target_date)

        # 2. Fall back to city name lookup
        match = _find_city_coords(question)
        if match:
            city_name, lat, lon, is_us = match
        else:
            city_name = _extract_city_heuristic(question)
            if not city_name:
                return None
            coords = _geocode(city_name)
            if not coords:
                return None
            lat, lon = coords
            is_us = False

        cache_key = f"city:{city_name.lower()}:{target_date}"

        def _build_city():
            fc = self._build_consensus_forecast(lat, lon, is_us, target_date, city_name)
            if fc:
                fc.city = city_name
            return fc

        forecast = self._consensus_cached(cache_key, _build_city)
        if forecast is None:
            return None
        return self._apply_intraday(forecast, lat, lon, "", target_date)
