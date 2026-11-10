"""v5.9: bridge ONE control area that lags on ENTSO-E.

ENTSO-E publishes the German total (A75 generation, A65 load) only for MTUs
that all four control areas have delivered. One late TSO therefore freezes
Solar, Wind Onshore, Load and everything derived from them, while the other
three areas are an hour old (live 26.10.2026 and 10.11.2026: Amprion).

The German total is exactly the sum of the four control areas (checked
20.10.-09.11.2026, 2020 MTUs: max. deviation 0.0 MW for A75 B16/B18/B19 and
A65, actual and day-ahead). So for an MTU the total does not cover yet:

  all areas delivered   -> their sum (no estimate involved)
  exactly one missing   -> sum of the others + an ESTIMATE of the missing one
  more than one missing -> nothing

Estimate of the missing area M at MTU t, gap of h quarter-hours since its
last value at t0 (F = M's own day-ahead forecast, w = exp(-h / TAU)):

  anchored   F(t) + w * (A(t0) - F(t0)) + (1 - w) * cross(t)
             cross = day-ahead error of the reporting areas at t, scaled to
             M's size (F_M / F_others); 0 for load and whenever fewer than
             two other areas report the technology.
  scaled     solar only, needs netztransparenz: M's 15-min "Hochrechnung"
             (TSO-marketed EEG volumes, a fixed part of its fleet) times the
             ENTSO-E / Hochrechnung ratio of the 24 h before the gap.

Backtest 20.10.-09.11.2026 (scripts/gapfill_backtest.py, every area hidden
in turn, every gap length): see README. Chosen on that sample: TAU = 8 h
among 2/4/8 h, and which method each quantity gets (cross term for wind and
solar but not for load, where it made things worse; netztransparenz for
solar but not for wind). The README table is therefore in-sample.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

Q = timedelta(minutes=15)
TAU_QH = 32.0          # 8 h: weight of the last own error halves after ~5.5 h
MAX_GAP_QH = 96        # no estimate for an area silent for more than 24 h
MIN_OTHERS_MW = 50.0   # below this the others' forecast cannot scale an error
RATIO_WINDOW_QH = 96   # netztransparenz ratio: the 24 h before the gap
RATIO_MIN_POINTS = 8   # ... with at least 2 h of daylight in common
RATIO_MIN_MW = 20.0

Series = dict[datetime, float]


def last_time(s: Series, before: datetime | None = None) -> datetime | None:
    keys = [t for t in s if before is None or t <= before]
    return max(keys) if keys else None


def anchor_of(actual: Series, forecast: Series, t: datetime) -> tuple[datetime, float] | None:
    """Last MTU before t with both a value and a forecast, and its error."""
    known = [k for k in actual if k < t and k in forecast]
    if not known:
        return None
    k = max(known)
    return k, actual[k] - forecast[k]


def anchored(t: datetime, f: float | None, anchor: tuple[datetime, float] | None,
             others_actual: float | None = None, others_forecast: float | None = None) -> float | None:
    """Estimate for one MTU of the lagging area; None without its forecast."""
    if f is None:
        return None
    w, err0 = 0.0, 0.0
    if anchor is not None:
        h = (t - anchor[0]) / Q
        if h > MAX_GAP_QH:
            return None
        w, err0 = math.exp(-h / TAU_QH), anchor[1]
    cross = 0.0
    if others_actual is not None and others_forecast is not None and others_forecast > MIN_OTHERS_MW:
        cross = (others_actual - others_forecast) * min(3.0, max(0.0, f / others_forecast))
    elif anchor is None:
        return None  # neither an own anchor nor the other areas: that would just be the forecast
    return f + w * err0 + (1.0 - w) * cross


def level_ratio(actual: Series, proxy: Series, gap_start: datetime) -> float | None:
    """ENTSO-E / proxy level of one area over the 24 h before its gap."""
    num = den = 0.0
    n = 0
    for i in range(RATIO_WINDOW_QH):
        t = gap_start - i * Q
        a, p = actual.get(t), proxy.get(t)
        if a is None or p is None or p < RATIO_MIN_MW:
            continue
        num, den, n = num + a, den + p, n + 1
    return num / den if n >= RATIO_MIN_POINTS and den > 0 else None


def verified(official: Series, by_area: dict[str, Series], reporters: list[str]) -> bool:
    """The areas must add up to the official total at the newest MTU all of
    them cover. Otherwise an area that has been silent for longer than the
    window (and is therefore not among `reporters`) would be dropped silently."""
    common = [t for t in official if all(t in by_area[a] for a in reporters)]
    if not common:
        return False
    t = max(common)
    return abs(sum(by_area[a][t] for a in reporters) - official[t]) <= max(1.0, 0.002 * abs(official[t]))


def fill(official: Series, by_area: dict[str, Series], forecast_by_area: dict[str, Series], forecast_total: Series,
         start: datetime, end: datetime, *, cross: bool = True, proxy_by_area: dict[str, Series] | None = None,
         floor: float | None = 0.0) -> dict:
    """MTUs in [start, end) that the official total lacks, as far as the areas allow.

    official, by_area and the lagging area's forecast should reach back before
    `start` (verification, anchor, ratio). forecast_total is the German
    day-ahead forecast; minus the lagging area's own forecast it is the
    forecast of the reporting areas. Returns values (MW per filled MTU),
    estimated (the MW of it that is an estimate; absent = plain area sum),
    missing (area per estimated MTU), methods and through-times per area.
    """
    reporters = sorted(a for a, s in by_area.items() if s)
    out: dict = {"values": {}, "estimated": {}, "missing": {}, "methods": {}, "verified": False,
                 "area_through": {a: last_time(by_area[a]) for a in reporters}}
    if len(reporters) < 2 or not verified(official, by_area, reporters):
        return out
    out["verified"] = True
    horizon = max(out["area_through"].values())
    ratios: dict[tuple, float | None] = {}   # per (area, last MTU before its gap)
    anchors: dict[tuple, tuple[datetime, float] | None] = {}
    t = max(start, (last_time(official) or start - Q) + Q)
    while t < end and t <= horizon:
        missing = [a for a in reporters if t not in by_area[a]]
        if t in official or len(missing) > 1:
            t += Q
            continue
        if not missing:
            out["values"][t] = sum(by_area[a][t] for a in reporters)
            t += Q
            continue
        m = missing[0]
        others = [a for a in reporters if a != m]
        proxy = (proxy_by_area or {}).get(m) or {}
        own = forecast_by_area.get(m, {})
        gap_start = last_time(by_area[m], t)
        if gap_start is not None and (t - gap_start) / Q > MAX_GAP_QH:
            t += Q
            continue
        key = (m, gap_start)
        if key not in ratios:
            ratios[key] = level_ratio(by_area[m], proxy, gap_start) if proxy and gap_start is not None else None
            anchors[key] = anchor_of(by_area[m], own, t)
        if ratios[key] is not None:
            # One method per gap: the estimate ends where the proxy ends.
            est, method = (proxy[t] * ratios[key] if t in proxy else None), "scaled"
        else:
            use_cross = cross and len(others) >= 2 and t in forecast_total and t in own
            est, method = anchored(t, own.get(t), anchors[key], sum(by_area[a][t] for a in others) if use_cross else None,
                                   forecast_total[t] - own[t] if use_cross else None), "anchored"
        if est is not None:
            if floor is not None:
                est = max(floor, est)
            out["values"][t] = sum(by_area[a][t] for a in others) + est
            out["estimated"][t] = est
            out["missing"][t] = m
            out["methods"][m] = method
        t += Q
    return out


def lagging_areas(area_through: dict[str, datetime | None], tolerance: timedelta = 2 * Q) -> dict[str, datetime | None]:
    """Areas clearly behind the freshest one (None = nothing at all)."""
    known = [t for t in area_through.values() if t is not None]
    if not known:
        return {}
    newest = max(known)
    return {a: t for a, t in area_through.items() if t is None or newest - t > tolerance}
