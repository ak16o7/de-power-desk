from __future__ import annotations

import base64
import hmac
import io
import json
import logging
import math
import os
import re
import statistics
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import ntp
from app.quality import classify_notice, outage_breakdown, panel_quality

VERSION = "5.1.0"
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

# Upstream failures are turned into per-source states for the UI. Without a log
# line they are invisible in production, so every swallowed exception is logged
# once (API key masked) through this logger.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("de_power_desk")

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
ENDPOINT = os.getenv("ENTSOE_ENDPOINT_URL", "https://web-api.tp.entsoe.eu/api")
API_KEY = os.getenv("ENTSOE_API_KEY", "").strip()
HTTP_TIMEOUT = int(os.getenv("ENTSOE_HTTP_TIMEOUT", "35"))
REFRESH_SECONDS = int(os.getenv("ENTSOE_REFRESH_SECONDS", "300"))
CACHE_SECONDS = int(os.getenv("ENTSOE_CACHE_SECONDS", "240"))
CACHE_MAX_ENTRIES = max(32, min(2048, int(os.getenv("ENTSOE_CACHE_MAX_ENTRIES", "256"))))
UPSTREAM_MAX_REQUESTS_PER_MINUTE = max(30, min(380, int(os.getenv("ENTSOE_MAX_REQUESTS_PER_MINUTE", "300"))))
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
AUTH_ENABLED = bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)
# Set DASHBOARD_PUBLIC_OK=true to run deliberately without login and hide the UI warning.
PUBLIC_OK = os.getenv("DASHBOARD_PUBLIC_OK", "").strip().lower() in ("1", "true", "yes")

AREAS = {
    "DE": "10Y1001A1001A83F",
    "DE_LU": "10Y1001A1001A82H",
    "AT": "10YAT-APG------L",
    "BE": "10YBE----------2",
    "CH": "10YCH-SWISSGRIDZ",
    "CZ": "10YCZ-CEPS-----N",
    "DK_1": "10YDK-1--------W",
    "DK_2": "10YDK-2--------M",
    "FR": "10YFR-RTE------C",
    "NL": "10YNL----------L",
    "NO_2": "10YNO-2--------T",
    "PL": "10YPL-AREA-----S",
    "SE_4": "10Y1001A1001A47J",
    # German control areas, useful for balancing
    "50HERTZ": "10YDE-VE-------2",
    "AMPRION": "10YDE-RWENET---I",
    "TENNET_DE": "10YDE-EON------1",
    "TRANSNETBW": "10YDE-ENBW-----N",
}

PSR = {"B16": "Solar", "B18": "Wind Offshore", "B19": "Wind Onshore"}
PSR_NAMES = {
    "B01": "Biomass", "B02": "Lignite", "B03": "Coal gas", "B04": "Gas", "B05": "Hard coal", "B06": "Oil",
    "B07": "Oil shale", "B08": "Peat", "B09": "Geothermal", "B10": "Pumped storage", "B11": "Run-of-river",
    "B12": "Hydro reservoir", "B13": "Marine", "B14": "Nuclear", "B15": "Other RES", "B16": "Solar",
    "B17": "Waste", "B18": "Wind offshore", "B19": "Wind onshore", "B20": "Other", "B25": "Battery",
}
ALL_NEIGHBORS = ["FR", "NL", "BE", "DK_1", "DK_2", "AT", "CH", "CZ", "PL", "SE_4", "NO_2"]
DEFAULT_NEIGHBORS = list(ALL_NEIGHBORS)
BALANCING_AREAS = ["50HERTZ", "AMPRION", "TENNET_DE", "TRANSNETBW"]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "entsoe-desk/5.1 (+desk dashboard)"})
# The UI loads all panels at once (up to ~50 concurrent upstream calls: borders 12,
# outages 8, balancing 2x8 + 4 ...); the default pool of 10 made
# urllib3 discard and re-handshake connections under load.
SESSION.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=64, max_retries=Retry(
    total=2, connect=2, read=2, status=2, backoff_factor=0.35,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True, raise_on_status=False,
)))


class TTLCache:
    def __init__(self, max_entries: int = 256) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def _purge_expired(self, now: float) -> None:
        for key, (expires, _) in list(self._d.items()):
            if now >= expires:
                self._d.pop(key, None)

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._d.get(key)
            if not item:
                return None
            expires, value = item
            if time.time() >= expires:
                self._d.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: int) -> Any:
        with self._lock:
            now = time.time()
            self._purge_expired(now)
            if key not in self._d and len(self._d) >= self._max_entries:
                # Evict the entry that expires first. The cache is a protection
                # against upstream fan-out, not a historical database.
                oldest = min(self._d, key=lambda k: self._d[k][0])
                self._d.pop(oldest, None)
            self._d[key] = (now + ttl, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = TTLCache(CACHE_MAX_ENTRIES)
CACHE_LOCKS: dict[str, list[Any]] = {}  # key -> [Lock, refcount]
CACHE_LOCKS_GUARD = threading.Lock()


class SlidingWindowLimiter:
    """Process-local guardrail for the public dashboard's ENTSO-E fan-out."""
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float = 5.0) -> None:
        deadline = time.monotonic() + max_wait
        while True:
            now = time.monotonic()
            with self._lock:
                while self._hits and now - self._hits[0] >= self.window:
                    self._hits.popleft()
                if len(self._hits) < self.limit:
                    self._hits.append(now)
                    return
                wait_for = max(0.05, self.window - (now - self._hits[0]))
            if now + wait_for > deadline:
                raise RuntimeError("ENTSO-E upstream request budget temporarily exhausted; retry shortly")
            time.sleep(min(wait_for, 0.5))


UPSTREAM_LIMITER = SlidingWindowLimiter(UPSTREAM_MAX_REQUESTS_PER_MINUTE)


def local_name(tag: str) -> str:
    return tag.split("}")[-1].lower()


def text_of(elem: ET.Element, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for node in elem.iter():
        if local_name(node.tag) in wanted and node.text:
            return node.text.strip()
    return None


def elements(elem: ET.Element, name: str):
    target = name.lower()
    for node in elem.iter():
        if local_name(node.tag) == target:
            yield node


def parse_iso_duration(value: str | None) -> timedelta:
    """Fixed ISO 8601 durations only; never silently reinterpret an unknown unit."""
    match = re.fullmatch(r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?", (value or "").upper())
    if not match or not any(match.groups()):
        raise ValueError(f"Unsupported resolution: {value!r}")
    days, hours, minutes, seconds = (float(x or 0) for x in match.groups())
    step = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    if step <= timedelta(0):
        raise ValueError("Resolution must be positive")
    return step


def parse_dt(value: str) -> datetime:
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def xml_documents(content: bytes) -> list[bytes]:
    if content[:2] == b"PK":
        out: list[bytes] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    out.append(zf.read(name))
        return out
    return [content]


def mask_secret(text: str) -> str:
    """Never let the ENTSO-E token reach logs or HTTP responses."""
    return text.replace(API_KEY, "***") if API_KEY else text


def log_upstream(context: str, exc: BaseException, level: int = logging.WARNING) -> None:
    LOG.log(level, "%s: %s: %s", context, type(exc).__name__, mask_secret(str(exc))[:300])


def safe_error_text(content: bytes) -> str:
    try:
        root = ET.fromstring(content)
        for node in root.iter():
            if local_name(node.tag) == "text" and node.text:
                return node.text.strip()
    except Exception:
        pass
    return content[:500].decode("utf-8", "ignore").strip()


def day_bounds(day: str | None) -> tuple[datetime, datetime, Date]:
    d = Date.fromisoformat(day) if day else datetime.now(BERLIN).date()
    start = datetime(d.year, d.month, d.day, tzinfo=BERLIN)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC), d


def entsoe_request(params: dict[str, Any], start: datetime, end: datetime) -> bytes:
    if not API_KEY:
        raise RuntimeError("ENTSOE_API_KEY fehlt. Lege ihn in .env ab.")
    payload = dict(params)
    payload.update(
        {
            "securityToken": API_KEY,
            "periodStart": start.astimezone(UTC).strftime("%Y%m%d%H%M"),
            "periodEnd": end.astimezone(UTC).strftime("%Y%m%d%H%M"),
        }
    )
    # The public UI can generate many fan-out calls. Keep a margin below the
    # Transparency Platform's published per-IP request ceiling and fail cleanly
    # instead of letting arbitrary visitors hammer the upstream service.
    UPSTREAM_LIMITER.acquire()
    try:
        r = SESSION.get(ENDPOINT, params=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"ENTSO-E Netzwerkfehler: {type(e).__name__}") from None
    if r.content[:2] != b"PK" and b"No matching data found" in r.content:
        raise LookupError("No matching data found")
    if r.status_code >= 400:
        msg = safe_error_text(r.content) or f"HTTP {r.status_code}"
        msg = msg.replace(API_KEY, "***")
        raise RuntimeError(f"ENTSO-E API: {msg}")
    # ENTSO-E can return HTTP 200 for a no-data acknowledgement. ZIP endpoints
    # start with PK; XML/text responses can be checked cheaply without parsing.
    if r.content[:2] != b"PK" and b"No matching data found" in r.content:
        raise LookupError("No matching data found")
    if r.content[:2] != b"PK":
        root = ET.fromstring(r.content)
        if local_name(root.tag) == "acknowledgement_marketdocument":
            raise RuntimeError("ENTSO-E acknowledgement: " + safe_error_text(r.content).replace(API_KEY, "***"))
    return r.content


def parse_timeseries(content: bytes) -> list[dict[str, Any]]:
    """Parse common ENTSO-E point time series, including R3 A03 block curves.

    Since the R3 migrations many datasets use curveType A03: omitted positions
    mean "repeat the previous value". Treating those positions as missing makes
    current data look artificially jumpy, especially cross-border flows.
    """
    rows: list[dict[str, Any]] = []
    for doc in xml_documents(content):
        try:
            root = ET.fromstring(doc)
        except ET.ParseError as exc:
            raise ValueError("Malformed ENTSO-E XML") from exc
        doc_created = text_of(root, "createdDateTime", "createddatetime")
        revision = text_of(root, "revisionNumber", "revisionnumber")
        # Document-level docStatus (A01 intermediate, A02 final, X01 estimated).
        # Only direct children of the root: TimeSeries may carry their own status.
        doc_status = next((text_of(n, "value") for n in root if local_name(n.tag) == "docstatus"), None)
        for ts in elements(root, "timeseries"):
            psr_type = text_of(ts, "psrType", "psrtype")
            business_type = text_of(ts, "businessType", "businesstype")
            flow_direction = text_of(ts, "flowDirection.direction", "flowdirection.direction")
            curve_type = text_of(ts, "curveType", "curvetype")
            unit = text_of(ts, "quantity_Measure_Unit.name", "quantity_measure_unit.name")
            in_domain = text_of(ts, "in_Domain.mRID", "in_domain.mrid", "inBiddingZone_Domain.mRID", "inbiddingzone_domain.mrid")
            out_domain = text_of(ts, "out_Domain.mRID", "out_domain.mrid", "outBiddingZone_Domain.mRID", "outbiddingzone_domain.mrid")
            is_consumption = any(local_name(n.tag) == "outbiddingzone_domain.mrid" for n in ts.iter())
            for period in [n for n in ts.iter() if local_name(n.tag) == "period"]:
                start_txt = text_of(period, "start", "timeInterval.start", "timeinterval.start")
                end_txt = text_of(period, "end", "timeInterval.end", "timeinterval.end")
                resolution_txt = text_of(period, "resolution")
                if not start_txt:
                    continue
                pstart = parse_dt(start_txt)
                pend = parse_dt(end_txt) if end_txt else None
                step = parse_iso_duration(resolution_txt)
                parsed_points: list[tuple[int, float, str | None]] = []
                for point in [n for n in period.iter() if local_name(n.tag) == "point"]:
                    pos_txt = text_of(point, "position")
                    value_txt = text_of(
                        point,
                        "quantity",
                        "price.amount",
                        "imbalance_Price.amount",
                        "imbalance_price.amount",
                        "activation_Price.amount",
                        "activation_price.amount",
                    )
                    if not pos_txt or value_txt is None:
                        continue
                    try:
                        pos = int(pos_txt)
                        value = float(value_txt)
                        if pos < 1 or not math.isfinite(value):
                            raise ValueError("Invalid point")
                    except ValueError:
                        continue
                    category = text_of(point, "imbalance_Price.category", "imbalance_price.category", "price.category")
                    parsed_points.append((pos, value, category))
                parsed_points.sort(key=lambda x: x[0])
                for idx, (pos, value, category) in enumerate(parsed_points):
                    repeat = 1
                    if curve_type == "A03":
                        if idx + 1 < len(parsed_points):
                            repeat = max(1, parsed_points[idx + 1][0] - pos)
                        elif pend:
                            repeat = max(1, int((pend - (pstart + (pos - 1) * step)) / step))
                    for offset in range(repeat):
                        ts_utc = pstart + (pos - 1 + offset) * step
                        if pend and ts_utc >= pend:
                            break
                        rows.append({
                            "ts": ts_utc.astimezone(UTC), "value": value, "psr": psr_type,
                            "business": business_type, "direction": flow_direction, "category": category,
                            "in_domain": in_domain, "out_domain": out_domain, "resolution": resolution_txt,
                            "curve_type": curve_type, "created": doc_created, "revision": revision,
                            "consumption": is_consumption, "unit": unit, "doc_status": doc_status,
                        })
    return rows

def _row_rank(r: dict[str, Any]) -> tuple[int, datetime]:
    try:
        rev = int(r.get("revision") or 0)
    except (TypeError, ValueError):
        rev = 0
    try:
        created = parse_dt(r.get("created")) if r.get("created") else datetime.min.replace(tzinfo=UTC)
    except Exception:
        created = datetime.min.replace(tzinfo=UTC)
    return rev, created


def series(rows: list[dict[str, Any]], psr: str | None = None, consumption: bool | None = None) -> dict[datetime, float]:
    chosen: dict[datetime, tuple[tuple[int, datetime], float]] = {}
    for r in rows:
        if psr and r.get("psr") != psr:
            continue
        if consumption is not None and bool(r.get("consumption")) != consumption:
            continue
        rank = _row_rank(r)
        t = r["ts"]
        if t not in chosen or rank >= chosen[t][0]:
            chosen[t] = (rank, float(r["value"]))
    return {t: rv[1] for t, rv in chosen.items()}


def flow_series(rows: list[dict[str, Any]]) -> dict[datetime, float]:
    return series(rows)

def as_points(s: dict[datetime, float]) -> list[dict[str, Any]]:
    return [{"t": k.astimezone(BERLIN).isoformat(), "v": round(v, 3)} for k, v in sorted(s.items())]


def add_series(*items: dict[datetime, float]) -> dict[datetime, float]:
    keys = set().union(*(x.keys() for x in items)) if items else set()
    out: dict[datetime, float] = {}
    for k in keys:
        vals = [x.get(k) for x in items]
        if any(v is not None for v in vals):
            out[k] = sum(v or 0.0 for v in vals)
    return out


def add_series_complete(*items: dict[datetime, float]) -> dict[datetime, float]:
    """Sum only timestamps present in every input series.

    For the consolidated RES chart we do not want a missing Solar/Onshore/Offshore
    component to be silently treated as zero.
    """
    if not items or any(not item for item in items):
        return {}
    keys = set(items[0])
    for item in items[1:]:
        keys &= set(item)
    return {k: sum(item[k] for item in items) for k in keys}


def subtract_series(a: dict[datetime, float], b: dict[datetime, float]) -> dict[datetime, float]:
    keys = set(a) & set(b)
    return {k: a[k] - b[k] for k in keys}


def latest_point(s: dict[datetime, float], now: datetime | None = None) -> tuple[datetime, float] | None:
    if not s:
        return None
    now = now or datetime.now(BERLIN)
    candidates = [(k, v) for k, v in s.items() if k <= now]
    if not candidates:
        return None
    return max(candidates, key=lambda kv: kv[0])


def latest_before(s: dict[datetime, float], now: datetime | None = None) -> float | None:
    point = latest_point(s, now)
    return point[1] if point else None


def max_created(rows: list[dict[str, Any]]) -> str | None:
    """Latest document creation timestamp exposed by ENTSO-E, if present."""
    vals: list[datetime] = []
    for r in rows:
        raw = r.get("created")
        if not raw:
            continue
        try:
            vals.append(parse_dt(raw))
        except Exception:
            continue
    return max(vals).astimezone(BERLIN).isoformat() if vals else None


def latest_timestamp(s: dict[datetime, float], now: datetime | None = None) -> str | None:
    point = latest_point(s, now)
    return point[0].isoformat() if point else None


def point_at_or_before(
    s: dict[datetime, float], target: datetime, tolerance: timedelta = timedelta(minutes=20)
) -> tuple[datetime, float] | None:
    candidates = [(k, v) for k, v in s.items() if k <= target]
    if not candidates:
        return None
    point = max(candidates, key=lambda kv: kv[0])
    if target - point[0] > tolerance:
        return None
    return point


def change_windows(
    s: dict[datetime, float], as_of: datetime | None = None, windows: tuple[int, ...] = (15, 30, 60)
) -> dict[str, float | None]:
    """Change versus approximately 15/30/60 minutes earlier.

    We require a nearby historical MTU instead of silently comparing a 15-minute
    metric with a point several hours old when a source is sparse or delayed.
    """
    ref = latest_point(s, as_of)
    if not ref:
        return {f"d{m}_mw": None for m in windows}
    ref_t, ref_v = ref
    out: dict[str, float | None] = {}
    for minutes in windows:
        prev = point_at_or_before(s, ref_t - timedelta(minutes=minutes))
        out[f"d{minutes}_mw"] = round(ref_v - prev[1], 3) if prev else None
    return out


def mean_series(rows: list[dict[str, Any]], category: str | None = None) -> dict[datetime, float]:
    buckets: dict[datetime, list[float]] = {}
    for r in rows:
        if category is not None and r.get("category") != category:
            continue
        buckets.setdefault(r["ts"], []).append(float(r["value"]))
    return {t: float(statistics.mean(vals)) for t, vals in buckets.items()
            if vals and max(vals) - min(vals) < 0.00001}


def latest_revision_rows(rows: list[dict[str, Any]], identity_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Keep only the latest ENTSO-E revision for each logical time-series point."""
    chosen: dict[tuple[Any, ...], tuple[tuple[int, datetime], dict[str, Any]]] = {}
    for r in rows:
        try:
            rev = int(r.get("revision") or 0)
        except (TypeError, ValueError):
            rev = 0
        try:
            created = parse_dt(r.get("created")) if r.get("created") else datetime.min.replace(tzinfo=UTC)
        except Exception:
            created = datetime.min.replace(tzinfo=UTC)
        key = tuple(r.get(field) for field in identity_fields)
        rank = (rev, created)
        if key not in chosen or rank >= chosen[key][0]:
            chosen[key] = (rank, r)
    return [item[1] for item in chosen.values()]


def latest_revision_groups(rows: list[dict[str, Any]], identity_fields: tuple[str, ...],
                           member_field: str = "direction") -> list[dict[str, Any]]:
    """Newest revision per logical point, keeping all members of that revision.

    A86 publishes one MTU as separate A01/A02 series. If a revision flips the
    direction (intermediate: surplus, final: deficit), keying the revision by
    direction would keep BOTH and net them. Here the newest revision per
    identity wins as a whole; duplicates inside it are collapsed per member.
    """
    best: dict[tuple[Any, ...], tuple[int, datetime]] = {}
    for r in rows:
        key = tuple(r.get(f) for f in identity_fields)
        rank = _row_rank(r)
        if key not in best or rank > best[key]:
            best[key] = rank
    chosen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = tuple(r.get(f) for f in identity_fields)
        if _row_rank(r) == best[key]:
            chosen[(*key, r.get(member_field))] = r
    return list(chosen.values())


# ENTSO-E DDD v3r4, TR 17.1.H: the total imbalance volume D is published per ISP
# as an absolute MWh value plus an explicit indicator (surplus/deficit/balance);
# D > 0 = surplus (system long), D < 0 = deficit (system short). The TP encodes
# the indicator as flowDirection: A01 = surplus, A02 = deficit, A03 = balanced
# (same mapping as entsoe-py; verified against the NRV-Saldo sign). businessType
# A19 = "Balance energy deviation". Other business types (e.g. the separately
# published MV-SV value) must never be summed into D.
IMBALANCE_SIGN = {"A01": 1.0, "A02": -1.0, "A03": 0.0}
IMBALANCE_BUSINESS = (None, "A19")


def imbalance_volume_series(rows: list[dict[str, Any]]) -> tuple[dict[datetime, float], dict[str, list[str]]]:
    """Signed A86 volume in MWh per MTU for ONE area. Unknown codes are dropped
    and reported instead of being guessed as positive."""
    out: dict[datetime, float] = {}
    ignored: dict[str, set[str]] = {"business": set(), "direction": set(), "unit": set()}
    for r in rows:
        business = r.get("business")
        if business not in IMBALANCE_BUSINESS:
            ignored["business"].add(str(business))
            continue
        sign = IMBALANCE_SIGN.get(r.get("direction"))
        if sign is None:
            ignored["direction"].add(str(r.get("direction")))
            continue
        value = float(r["value"])
        unit = (r.get("unit") or "MWH").upper()
        if unit == "MAW":  # average MW over the ISP -> MWh
            value *= parse_iso_duration(r.get("resolution") or "PT15M").total_seconds() / 3600
        elif unit != "MWH":
            ignored["unit"].add(unit)
            continue
        out[r["ts"]] = out.get(r["ts"], 0.0) + sign * value
    return out, {k: sorted(v) for k, v in ignored.items() if v}


DOC_STATUS = {"A01": "intermediate", "A02": "final", "X01": "estimated"}


def publication_status(rows: list[dict[str, Any]], t: datetime | None) -> str | None:
    """'final' only if every contributing document at MTU t says so; any explicit
    intermediate/estimated flag -> 'preliminary'; no flag at all -> None."""
    if t is None:
        return None
    states = {DOC_STATUS.get(r.get("doc_status")) for r in rows if r.get("ts") == t}
    if not states:
        return None
    if states == {"final"}:
        return "final"
    if states & {"intermediate", "estimated"}:
        return "preliminary"
    return None


def trailing_mean(s: dict[datetime, float], as_of: datetime | None, n: int = 4,
                  step: timedelta = timedelta(minutes=15)) -> float | None:
    """Mean over the last n contiguous MTUs ending at as_of; None if any is missing.

    The newest MTU is the least reliable one (TR 16.1.B/C: estimated first,
    updated with measured values later). A 1-hour mean is shown next to it."""
    if as_of is None:
        return None
    vals = [s.get(as_of - i * step) for i in range(n)]
    if any(v is None for v in vals):
        return None
    return statistics.mean(vals)


PAST_DAY_CACHE_SECONDS = int(os.getenv("ENTSOE_PAST_DAY_CACHE_SECONDS", "3600"))


def ttl_for(d: Date, base: int) -> int:
    """Past delivery days change rarely (late revisions only); cache them longer
    so date browsing does not burn ~115 upstream calls per 4 minutes."""
    return max(base, PAST_DAY_CACHE_SECONDS) if d < datetime.now(BERLIN).date() else base


def cached(key: str, ttl: int, force: bool, fn):
    if not force:
        hit = CACHE.get(key)
        if hit is not None:
            return hit
    with CACHE_LOCKS_GUARD:
        entry = CACHE_LOCKS.get(key)
        if entry is None:
            entry = [threading.Lock(), 0]
            CACHE_LOCKS[key] = entry
        entry[1] += 1
        lock = entry[0]
    lock.acquire()
    try:
        if not force:
            hit = CACHE.get(key)
            if hit is not None:
                return hit
        value = fn()
        return CACHE.set(key, value, ttl)
    finally:
        lock.release()
        with CACHE_LOCKS_GUARD:
            current = CACHE_LOCKS.get(key)
            if current is entry:
                current[1] -= 1
                if current[1] <= 0:
                    CACHE_LOCKS.pop(key, None)


def fetch_renewables(day: str | None, force: bool = False) -> dict[str, Any]:
    start, end, d = day_bounds(day)
    key = f"renewables:v5:{d.isoformat()}"

    def maybe_forecast(process_type: str) -> list[dict[str, Any]]:
        try:
            content = entsoe_request(
                {"documentType": "A69", "processType": process_type, "in_Domain": AREAS["DE"]},
                start,
                end,
            )
            return parse_timeseries(content)
        except LookupError:
            return []
        except RuntimeError as e:
            log_upstream(f"A69/{process_type}", e)
            return []

    def work():
        # Actuals do not exist yet for tomorrow; forecasts alone are still useful.
        try:
            actual_rows = parse_timeseries(entsoe_request(
                {"documentType": "A75", "processType": "A16", "in_Domain": AREAS["DE"]}, start, end))
        except LookupError:
            actual_rows = []

        # ENTSO-E 14.1.D process types. A01 is the fixed D-1 18:00
        # day-ahead snapshot, A40 the fixed D 08:00 intraday snapshot and
        # A18 the current/latest forecast. We also expose document creation
        # times as metadata because transport/revision timestamps can differ
        # slightly from the regulatory snapshot clock.
        da_rows = maybe_forecast("A01")
        intraday_rows = maybe_forecast("A40")
        current_rows = maybe_forecast("A18")

        payload: dict[str, Any] = {
            "date": d.isoformat(),
            "series": {},
            "updated": datetime.now(BERLIN).isoformat(),
            "scope": {"area": "DE", "type": "Member State", "eic": AREAS["DE"]},
            "forecast_definitions": {
                "day_ahead": "A01 · fixed D-1 18:00 day-ahead snapshot",
                "intraday": "A40 · fixed D 08:00 intraday snapshot",
                "current": "A18 · current / latest forecast",
            },
        }

        actual_parts: list[dict[datetime, float]] = []
        da_parts: list[dict[datetime, float]] = []
        intraday_parts: list[dict[datetime, float]] = []
        current_parts: list[dict[datetime, float]] = []

        for code, name in PSR.items():
            actual = series(actual_rows, code, consumption=False)
            day_ahead = series(da_rows, code)
            intraday = series(intraday_rows, code)
            current = series(current_rows, code)

            payload["series"][f"{name} Actual"] = as_points(actual)
            payload["series"][f"{name} Current"] = as_points(current)
            payload["series"][f"{name} Intraday"] = as_points(intraday)
            payload["series"][f"{name} Day-ahead"] = as_points(day_ahead)

            live_forecast = current or intraday or day_ahead
            payload["series"][f"{name} Forecast"] = as_points(live_forecast)

            actual_parts.append(actual)
            da_parts.append(day_ahead)
            intraday_parts.append(intraday)
            current_parts.append(current)

        res_actual = add_series_complete(*actual_parts)
        res_da = add_series_complete(*da_parts)
        res_intraday = add_series_complete(*intraday_parts)
        res_current = add_series_complete(*current_parts)
        res_live = res_current or res_intraday or res_da
        # v5: the headline error has ONE fixed basis all day: day-ahead A01.
        # That is the forecast the DA auction priced. A18 "current" is updated
        # close to delivery and behaves like a nowcast for past MTUs, so an
        # error against it understates the surprise and changes meaning when
        # the basis switches during the day. The A40 intraday error is shown
        # separately, never mixed into the headline.
        miss_da = subtract_series(res_actual, res_da)
        miss_id = subtract_series(res_actual, res_intraday)
        forecast_revision = subtract_series(res_current, res_da)

        payload["series"].update(
            {
                "RES Actual": as_points(res_actual),
                "RES Current": as_points(res_current),
                "RES Intraday": as_points(res_intraday),
                "RES Day-ahead": as_points(res_da),
                "RES Forecast": as_points(res_live),
                "RES Forecast Error": as_points(miss_da),
                "RES Forecast Error ID": as_points(miss_id),
                "RES Forecast Revision": as_points(forecast_revision),
            }
        )
        miss_point = latest_point(miss_da)
        id_point = point_at_or_before(miss_id, miss_point[0], timedelta(0)) if miss_point else None
        tech_error: dict[str, float | None] = {}
        for (code, name), actual, day_ahead in zip(PSR.items(), actual_parts, da_parts):
            if miss_point and miss_point[0] in actual and miss_point[0] in day_ahead:
                tech_error[name] = round(actual[miss_point[0]] - day_ahead[miss_point[0]], 1)
            else:
                tech_error[name] = None
        day_so_far = [v for t, v in miss_da.items() if not miss_point or t <= miss_point[0]]

        now = datetime.now(BERLIN)
        forward: dict[datetime, float] = {}
        if res_current and res_da:
            if d == now.date():
                forward = {t: v for t, v in forecast_revision.items() if now <= t < min(end, now + timedelta(hours=4))}
        rest_of_day = {t: v for t, v in forecast_revision.items() if t >= now} if d == now.date() else {}
        revision_avg = statistics.mean(forward.values()) if forward else None

        err_1h = trailing_mean(miss_da, miss_point[0]) if miss_point else None
        payload["kpi"] = {
            "res_error_mw": round(miss_point[1], 1) if miss_point else None,
            "res_error_1h_mw": round(err_1h, 1) if err_1h is not None else None,
            "res_error_id_mw": round(id_point[1], 1) if id_point else None,
            "as_of": miss_point[0].isoformat() if miss_point else None,
            "forecast_basis": "day-ahead",
            "tech_error_mw": tech_error,
            "day_avg_error_mw": round(statistics.mean(day_so_far), 1) if day_so_far else None,
            "day_mae_mw": round(statistics.mean(abs(v) for v in day_so_far), 1) if day_so_far else None,
            "changes": change_windows(miss_da),
            "next4h_revision_avg_mw": round(revision_avg, 1) if revision_avg is not None else None,
            "next4h_revision_min_mw": round(min(forward.values()), 1) if forward else None,
            "next4h_revision_max_mw": round(max(forward.values()), 1) if forward else None,
            "next4h_points": len(forward),
            "next4h_window_start": min(forward).isoformat() if forward else None,
            "next4h_window_end": (max(forward) + timedelta(minutes=15)).isoformat() if forward else None,
            "rest_of_day_revision_avg_mw": round(statistics.mean(rest_of_day.values()), 1) if rest_of_day else None,
        }
        payload["freshness"] = {"actual_through": latest_timestamp(res_actual)}
        return payload

    return cached(key, ttl_for(d, CACHE_SECONDS), force, work)


def fetch_load(day: str | None, force: bool = False) -> dict[str, Any]:
    start, end, d = day_bounds(day)
    key = f"load:v5:{d.isoformat()}"

    def work():
        try:
            actual = series(parse_timeseries(entsoe_request(
                {"documentType": "A65", "processType": "A16", "outBiddingZone_Domain": AREAS["DE"]}, start, end)))
        except LookupError:
            actual = {}
        forecast = series(parse_timeseries(entsoe_request(
            {"documentType": "A65", "processType": "A01", "outBiddingZone_Domain": AREAS["DE"]}, start, end)))
        ren = fetch_renewables(d.isoformat(), force=force)
        rs = ren["series"]
        res_actual = {parse_dt(p["t"]): p["v"] for p in rs.get("RES Actual", [])}
        res_da = {parse_dt(p["t"]): p["v"] for p in rs.get("RES Day-ahead", [])}
        residual_actual = subtract_series(actual, res_actual)
        residual_da = subtract_series(forecast, res_da)
        surprise = subtract_series(residual_actual, residual_da)
        rp = latest_point(residual_actual)
        sp = latest_point(surprise)
        surprise_1h = trailing_mean(surprise, sp[0]) if sp else None
        lp = latest_point(subtract_series(actual, forecast))
        return {
            "date": d.isoformat(),
            "updated": datetime.now(BERLIN).isoformat(),
            "scope": {"area": "DE", "type": "Member State", "eic": AREAS["DE"]},
            "series": {
                "Load Actual": as_points(actual),
                "Load Forecast": as_points(forecast),
                "Residual Load Actual": as_points(residual_actual),
                "Residual Load Forecast": as_points(residual_da),
                "Residual Load Surprise": as_points(surprise),
            },
            "kpi": {
                "residual_load_mw": round(rp[1], 1) if rp else None,
                "as_of": rp[0].isoformat() if rp else None,
                "residual_surprise_mw": round(sp[1], 1) if sp else None,
                "residual_surprise_1h_mw": round(surprise_1h, 1) if surprise_1h is not None else None,
                "surprise_as_of": sp[0].isoformat() if sp else None,
                "forecast_basis": "day-ahead load − day-ahead RES",
                "load_error_mw": round(lp[1], 1) if lp else None,
                "day_avg_surprise_mw": round(statistics.mean(v for t, v in surprise.items() if t <= sp[0]), 1) if sp else None,
                "changes": change_windows(residual_actual),
            },
            "freshness": {"actual_through": latest_timestamp(actual)},
        }

    return cached(key, ttl_for(d, CACHE_SECONDS), force, work)


def _one_flow(doc_type: str, source: str, dest: str, start: datetime, end: datetime, contract: str | None = None) -> tuple[dict[datetime, float], str]:
    params = {"documentType": doc_type, "in_Domain": AREAS[dest], "out_Domain": AREAS[source]}
    if contract:
        params["contract_MarketAgreement.Type"] = contract
    try:
        values = flow_series(parse_timeseries(entsoe_request(params, start, end)))
        return values, "ok" if values else "empty"
    except LookupError:
        return {}, "no_data"
    except Exception as e:
        log_upstream(f"{doc_type}{'/' + contract if contract else ''} {source}->{dest}", e)
        return {}, "error"


def _directional_net(imports: dict[datetime, float], import_state: str, exports: dict[datetime, float], export_state: str) -> tuple[dict[datetime, float], str]:
    """Build import-minus-export without turning request errors/gaps into zero."""
    if "error" in (import_state, export_state):
        return {}, "error"
    if import_state in ("no_data", "empty") and export_state in ("no_data", "empty"):
        return {}, "no_data"
    if imports and exports:
        keys = set(imports) & set(exports)
        return {t: imports[t] - exports[t] for t in keys}, "ok" if keys else "partial"
    if imports and export_state in ("no_data", "empty"):
        return dict(imports), "single_direction"
    if exports and import_state in ("no_data", "empty"):
        return {t: -v for t, v in exports.items()}, "single_direction"
    return {}, "partial"


def _value_at(s: dict[datetime, float], t: datetime) -> float | None:
    return s.get(t)


def fetch_borders(day: str | None, neighbors: list[str], force: bool = False) -> dict[str, Any]:
    start, end, d = day_bounds(day)
    neighbors = list(dict.fromkeys(n for n in neighbors if n in ALL_NEIGHBORS))
    if not neighbors:
        neighbors = DEFAULT_NEIGHBORS
    key = f"borders:v5:{d.isoformat()}:{','.join(neighbors)}"

    # A09 contract types: A01 = day-ahead, A05 = total (all horizons incl.
    # intraday). Total − DA = cross-border intraday trade.
    # Physical − total ("unscheduled") must be read differently per level
    # (ENTSO-E DDD v3r4, TR 12.1.F/12.1.G). Commercial schedules exclude by
    # definition: remedial actions (redispatch/countertrading), balancing-energy
    # exchange, emergency assistance and unintended flows.
    # * Per border: loop/transit flows dominate; on flow-based (Core) borders the
    #   scheduled exchange is itself a computed decomposition of net positions
    #   (Euphemia bilateral topology), so part of it is an allocation artefact.
    # * Sum over ALL borders: loop flows cancel. Live check 23./24.09.2026: the
    #   remainder swung by +-1.1-1.4 GW (sd) and correlated only weakly with the
    #   NRV-Saldo (r 0.13-0.21); countertrading DE->DK1 reached 2.2 GW. It is a
    #   mix of remedial actions, balancing exchange and publication
    #   inconsistencies - context, not a trading signal.
    metrics = (("physical", "A11", None), ("scheduled", "A09", "A01"), ("total", "A09", "A05"))

    def work():
        result: dict[str, Any] = {
            "date": d.isoformat(), "updated": datetime.now(BERLIN).isoformat(),
            "scope": {"hub": "DE_LU", "type": "Bidding Zone", "eic": AREAS["DE_LU"]},
            "borders": {}, "series": {}, "coverage": {}, "flags": [],
        }
        tasks = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            for n in neighbors:
                for metric, doc, contract in metrics:
                    tasks[pool.submit(_one_flow, doc, n, "DE_LU", start, end, contract)] = (n, metric, "import")
                    tasks[pool.submit(_one_flow, doc, "DE_LU", n, start, end, contract)] = (n, metric, "export")
            temp: dict[tuple[str, str, str], tuple[dict[datetime, float], str]] = {}
            for fut in as_completed(tasks):
                temp[tasks[fut]] = fut.result()

        nets: dict[str, dict[str, dict[datetime, float]]] = {m: {} for m, _, _ in metrics}
        for n in neighbors:
            status: dict[str, str] = {}
            border: dict[str, Any] = {}
            for metric, _, _ in metrics:
                imp, si = temp.get((n, metric, "import"), ({}, "error"))
                exp, se = temp.get((n, metric, "export"), ({}, "error"))
                net, state = _directional_net(imp, si, exp, se)
                status[metric] = state
                status[f"{metric}_import"] = si
                status[f"{metric}_export"] = se
                border[metric] = as_points(net)
                if net:
                    nets[metric][n] = net
            phys = nets["physical"].get(n, {})
            flag = None
            if len(phys) >= 8 and all(abs(v) < 0.5 for v in phys.values()):
                sched_vals = list(nets["total"].get(n, {}).values()) or list(nets["scheduled"].get(n, {}).values())
                flag = "zero_flow_all_day" if not sched_vals or all(abs(v) < 0.5 for v in sched_vals) else "zero_physical_with_schedule"
                result["flags"].append({"border": n, "flag": flag})
            border["status"] = status
            border["flag"] = flag
            result["borders"][n] = border

        def total_of(metric: str) -> dict[datetime, float]:
            # Only valid when every selected border has a series; no border is
            # silently treated as 0 MW.
            if len(nets[metric]) != len(neighbors):
                return {}
            return add_series_complete(*(nets[metric][n] for n in neighbors))

        totals_phys = total_of("physical")
        totals_da = total_of("scheduled")
        totals_total = total_of("total")
        id_trade = subtract_series(totals_total, totals_da)
        unscheduled = subtract_series(totals_phys, totals_total)
        result["series"]["Net Physical Import"] = as_points(totals_phys)
        result["series"]["Net DA Schedule"] = as_points(totals_da)
        result["series"]["Net Total Schedule"] = as_points(totals_total)
        result["series"]["Intraday Cross-border"] = as_points(id_trade)
        result["series"]["Unscheduled Flow"] = as_points(unscheduled)

        fp = latest_point(totals_phys)
        ref_t = fp[0] if fp else None

        def at_ref(series_: dict[datetime, float]) -> float | None:
            return series_.get(ref_t) if ref_t else None

        da_at = at_ref(totals_da)
        total_at = at_ref(totals_total)
        table = []
        for n in neighbors:
            row = {"border": n, "flag": result["borders"][n]["flag"]}
            for metric, _, _ in metrics:
                v = nets[metric].get(n, {}).get(ref_t) if ref_t else None
                row[metric] = round(v, 1) if v is not None else None
            row["unscheduled"] = round(row["physical"] - row["total"], 1) if row["physical"] is not None and row["total"] is not None else None
            row["intraday"] = round(row["total"] - row["scheduled"], 1) if row["total"] is not None and row["scheduled"] is not None else None
            table.append(row)
        result["table"] = table
        result["kpi"] = {
            "net_import_mw": round(fp[1], 1) if fp else None,
            "as_of": ref_t.isoformat() if ref_t else None,
            "da_schedule_mw": round(da_at, 1) if da_at is not None else None,
            "total_schedule_mw": round(total_at, 1) if total_at is not None else None,
            "intraday_xb_mw": round(total_at - da_at, 1) if total_at is not None and da_at is not None else None,
            "unscheduled_mw": round(fp[1] - total_at, 1) if fp and total_at is not None else None,
            "vs_da_schedule_mw": round(fp[1] - da_at, 1) if fp and da_at is not None else None,
            "selected_borders": neighbors,
            "complete_borders": len(nets["physical"]),
            "changes": change_windows(totals_phys, ref_t),
        }
        result["coverage"] = {
            "expected": len(neighbors), "physical_series": len(nets["physical"]),
            "scheduled_series": len(nets["scheduled"]), "total_series": len(nets["total"]),
            "physical_total_complete": bool(totals_phys),
            "scheduled_total_complete": bool(totals_da),
            "total_schedule_complete": bool(totals_total),
            "physical_borders": [n for n in neighbors if n in nets["physical"]],
            "scheduled_borders": [n for n in neighbors if n in nets["scheduled"]],
            "total_borders": [n for n in neighbors if n in nets["total"]],
        }
        result["freshness"] = {"physical_through": latest_timestamp(totals_phys)}
        result["methodology"] = (
            "Positive = import into DE-LU. Intraday-XB = total schedule (A09/A05) - day-ahead schedule (A09/A01). "
            "Physical - total schedule: commercial schedules exclude remedial actions (redispatch/countertrading), "
            "balancing-energy exchange, emergency assistance and unintended flows (TR 12.1.F). Per border it is "
            "mostly loop/transit flow; on flow-based Core borders the bilateral schedule is a computed decomposition "
            "of net positions. Summed over all borders loop flows cancel; the remainder (live 23./24.09.2026: "
            "sd 1.1-1.4 GW, r 0.13-0.21 with the NRV-Saldo) mixes remedial actions, balancing exchange and "
            "publication inconsistencies and is not a trading signal.")
        return result

    return cached(key, ttl_for(d, max(CACHE_SECONDS, 600)), force, work)

def _parse_outage_docs(content: bytes, zone: str, document_type: str) -> list[dict[str, Any]]:
    """Outage 3:0/4:x: dotted XML names are literal tags, not XPath paths."""
    out = []
    for doc in xml_documents(content):
        root = ET.fromstring(doc)
        meta = {"zone": zone, "document_type": document_type,
                "doc_mrid": text_of(root, "mRID"), "revision": text_of(root, "revisionNumber"),
                "created": text_of(root, "createdDateTime"), "docstatus": None}
        for node in root:
            if local_name(node.tag) == "docstatus":
                meta["docstatus"] = text_of(node, "value")
        # Preserve even a cancellation without points so it supersedes older revisions.
        if meta["docstatus"] in ("A09", "A13"):
            out.append({**meta, "tombstone": True})
            continue
        count_before = len(out)
        for ts in elements(root, "timeseries"):
            production_id = text_of(ts, "production_RegisteredResource.mRID", "registeredResource.mRID")
            generation_id = text_of(ts, "production_RegisteredResource.pSRType.powerSystemResources.mRID")
            production_name = text_of(ts, "production_RegisteredResource.name", "registeredResource.name")
            generation_name = text_of(ts, "production_RegisteredResource.pSRType.powerSystemResources.name")
            resource_id = (generation_id or production_id) if document_type == "A80" else production_id
            plant = (generation_name or production_name) if document_type == "A80" else production_name
            nominal_txt = text_of(ts, "production_RegisteredResource.pSRType.powerSystemResources.nominalP", "nominalP")
            nominal = float(nominal_txt) if nominal_txt is not None else None
            unit = text_of(ts, "quantity_Measure_Unit.name")
            if nominal is not None and (not math.isfinite(nominal) or nominal < 0):
                nominal = None
            if unit not in (None, "MAW"):
                nominal = None
            def event_time(prefix):
                date = text_of(ts, prefix + "_DateAndOrTime.date")
                clock = text_of(ts, prefix + "_DateAndOrTime.time")
                return parse_dt(date + "T" + clock) if date and clock else None
            event_start, event_end = event_time("start"), event_time("end")
            base = {**meta, "ts_mrid": text_of(ts, "mRID"), "resource_id": resource_id,
                    "production_id": production_id, "generation_id": generation_id,
                    "production_name": production_name, "plant": plant or resource_id or "Unknown unit",
                    "location": text_of(ts, "production_RegisteredResource.location.name"),
                    "psr": text_of(ts, "production_RegisteredResource.pSRType.psrType", "psrType"),
                    "business": text_of(ts, "businessType"), "nominal": nominal, "unit": unit,
                    "event_start": event_start, "event_end": event_end,
                    "reason_code": next((text_of(n, "code") for n in elements(root, "Reason") if text_of(n, "code")), None),
                    "reason_text": next((text_of(n, "text") for n in elements(root, "Reason") if text_of(n, "text")), None)}
            ts_before = len(out)
            curve = text_of(ts, "curveType") or "A03"
            for period in elements(ts, "Available_Period"):
                pstart = parse_dt(text_of(period, "start"))
                pend = parse_dt(text_of(period, "end"))
                step = parse_iso_duration(text_of(period, "resolution"))
                points = sorted((int(text_of(p, "position")), float(text_of(p, "quantity"))) for p in elements(period, "Point"))
                for i, (pos, available) in enumerate(points):
                    if pos < 1 or not math.isfinite(available) or available < 0:
                        raise ValueError("Invalid outage availability point")
                    seg_start = pstart + (pos - 1) * step
                    seg_end = min(pend, pstart + (points[i+1][0] - 1) * step) if i+1 < len(points) else pend
                    if curve == "A01":
                        seg_end = min(seg_end, seg_start + step)
                    if event_start: seg_start = max(seg_start, event_start)
                    if event_end: seg_end = min(seg_end, event_end)
                    if seg_start >= seg_end: continue
                    unavailable = nominal - available if nominal is not None and available <= nominal else None
                    out.append({**base, "start": seg_start, "end": seg_end, "available": available,
                                "unavailable": unavailable, "quality": "ok" if unavailable is not None else "unknown_capacity"})
            if len(out) == ts_before:
                raise ValueError("Active outage TimeSeries has no usable Available_Period")
        if len(out) == count_before:
            raise ValueError("Active outage document has no usable TimeSeries")
    return out


def _outage_rank(r: dict[str, Any]) -> tuple[int, datetime]:
    return _row_rank(r)


def _latest_outage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep latest document revision, then remove cancelled/withdrawn events."""
    docs: dict[tuple[Any, ...], tuple[tuple[int, datetime], str | None]] = {}
    for r in rows:
        doc_key = (r.get("zone"), r.get("document_type"), r.get("doc_mrid"))
        rank = _outage_rank(r)
        if doc_key not in docs or rank >= docs[doc_key][0]:
            docs[doc_key] = (rank, r.get("docstatus"))
    inactive_statuses = {"A09", "A13", "Cancelled", "Withdrawn", "cancelled", "withdrawn"}
    chosen: dict[tuple[Any, ...], tuple[tuple[int, datetime], dict[str, Any]]] = {}
    for r in rows:
        doc_key = (r.get("zone"), r.get("document_type"), r.get("doc_mrid"))
        latest_rank, latest_status = docs[doc_key]
        if _outage_rank(r) != latest_rank or latest_status in inactive_statuses or r.get("tombstone"):
            continue
        seg_key = (doc_key, r.get("resource_id"), r.get("ts_mrid"), r.get("start"), r.get("end"))
        if seg_key not in chosen or _outage_rank(r) >= chosen[seg_key][0]:
            chosen[seg_key] = (_outage_rank(r), r)
    return [x[1] for x in chosen.values()]


def _fetch_outage_pages(zone: str, document_type: str, start: datetime, end: datetime, max_pages: int = 25) -> tuple[list[dict[str, Any]], str, int]:
    """ENTSO-E outage endpoints are limited to 200 documents per request."""
    rows: list[dict[str, Any]] = []
    pages = 0
    for offset in range(0, max_pages * 200, 200):
        try:
            content = entsoe_request({"documentType": document_type, "biddingZone_domain": AREAS[zone], "offset": offset}, start, end)
        except LookupError:
            return rows, ("ok" if rows else "no_data"), pages
        except Exception as e:
            log_upstream(f"{document_type} {zone} offset={offset}", e)
            return rows, "error", pages
        pages += 1
        docs = xml_documents(content)
        try:
            parsed = _parse_outage_docs(content, zone, document_type)
        except (ValueError, TypeError, ET.ParseError) as e:
            log_upstream(f"{document_type} {zone} parse", e)
            return rows, "parse_error", pages
        rows.extend(parsed)
        # A page below the API's 200-document cap is terminal.
        if len(docs) < 200:
            return rows, ("ok" if rows else "empty"), pages
    LOG.warning("%s %s: outage result truncated after %d pages", document_type, zone, pages)
    return rows, "truncated", pages


OUTAGE_ZONES = ["DE_LU", "FR", "NL", "BE"]
OK_STATES = ("ok", "no_data", "empty")


def select_outage_rows(zone_rows: dict[str, list[dict[str, Any]]], states: dict[str, str]) -> tuple[list[dict[str, Any]], str | None, bool, int]:
    """Combine A80 unit notices with A77 plant notices without double counting.

    A80 (generation unit) is primary. An A77 (production unit) notice is added
    only when no A80 notice in the fetched window references the same
    production unit. Plants that report exclusively at plant level (typical
    for wind parks, many CCGTs) are therefore no longer dropped.
    Returns rows, source label, completeness flag and the number of A77-only
    production units that were added.
    """
    a80, a77 = zone_rows.get("A80", []), zone_rows.get("A77", [])
    a80_state, a77_state = states.get("A80"), states.get("A77")
    if a80_state in OK_STATES or a80:
        covered = {r.get("production_id") for r in a80 if r.get("production_id")}
        extra = [r for r in a77 if r.get("production_id") and r.get("production_id") not in covered] if a80 else list(a77)
        complete = a80_state == "ok" or (a80_state in ("no_data", "empty") and not a80)
        complete = complete and a77_state in OK_STATES
        added = len({r.get("production_id") or r.get("doc_mrid") for r in extra})
        if not a80 and not extra:
            return [], ("no events" if complete else None), complete, 0
        label = "A80" + (" + A77 plant-only" if extra else "") if a80 else "A77"
        return a80 + extra, label, complete, added
    if a77 and a77_state in OK_STATES:
        return list(a77), "A77 only (A80 failed)", False, 0
    return [], None, False, 0


def fetch_outages(day: str | None, force: bool = False) -> dict[str, Any]:
    start, end, d = day_bounds(day)
    zones = OUTAGE_ZONES
    key = f"outages:v5:{d.isoformat()}"
    # One extra day of history so that "change vs 24h earlier" can be derived.
    qstart = start - timedelta(days=1)

    def work():
        raw_by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
        source_status: dict[str, dict[str, str]] = {z: {"A77": "pending", "A80": "pending"} for z in zones}
        page_counts: dict[str, dict[str, int]] = {z: {"A77": 0, "A80": 0} for z in zones}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_fetch_outage_pages, z, doc, qstart, end): (z, doc) for z in zones for doc in ("A80", "A77")}
            for fut in as_completed(futs):
                z, doc = futs[fut]
                rows, status, pages = fut.result()
                raw_by_source[(z, doc)] = _latest_outage_rows(rows)
                source_status[z][doc] = status
                page_counts[z][doc] = pages

        all_rows: list[dict[str, Any]] = []
        selected_source: dict[str, str | None] = {}
        zone_complete: dict[str, bool] = {}
        a77_added: dict[str, int] = {}
        for z in zones:
            rows, label, ok, added = select_outage_rows(
                {"A80": raw_by_source.get((z, "A80"), []), "A77": raw_by_source.get((z, "A77"), [])}, source_status[z])
            all_rows.extend(rows)
            selected_source[z] = label
            a77_added[z] = added
            zone_complete[z] = ok and not any(r.get("unavailable") is None for r in rows if r["start"] < end and r["end"] > start)
        complete = all(zone_complete.values())
        all_rows = [r for r in all_rows if r["start"] < end and r["end"] > qstart]
        selected_rows = [r for r in all_rows if r["start"] < end and r["end"] > start]
        unknown_rows = [r for r in selected_rows if r.get("unavailable") is None]

        def event_key(r: dict[str, Any]) -> tuple[Any, ...]:
            resource = r.get("resource_id") or r.get("ts_mrid") or r.get("plant")
            document = r.get("doc_mrid") or r.get("ts_mrid") or resource
            return (r.get("zone"), r.get("document_type"), document, resource)

        def active_event_rows(rows: list[dict[str, Any]], t: datetime) -> list[dict[str, Any]]:
            chosen: dict[tuple[Any, ...], dict[str, Any]] = {}
            for r in rows:
                if not (r["start"] <= t < r["end"]):
                    continue
                if r.get("unavailable") is not None and float(r["unavailable"]) <= 0:
                    continue
                k = event_key(r)
                if k not in chosen or float(r.get("unavailable") or 0) > float(chosen[k].get("unavailable") or 0):
                    chosen[k] = r
            return list(chosen.values())

        def capacity_total(rows):
            # Overlapping notices for one resource contribute their maximum.
            units = {}
            for r in rows:
                k = (r["zone"], r.get("resource_id") or event_key(r))
                units[k] = max(units.get(k, 0.0), float(r.get("unavailable") or 0))
            return sum(units.values())

        grid = [start + timedelta(minutes=15 * i) for i in range(int((end - start).total_seconds() // 900))]
        by_zone: dict[str, dict[datetime, float]] = {z: {} for z in zones}
        for z in zones:
            zr = [r for r in selected_rows if r["zone"] == z and r.get("unavailable") is not None]
            for t in grid:
                by_zone[z][t] = capacity_total(active_event_rows(zr, t))
        total_series = add_series_complete(*(by_zone[z] for z in zones)) if complete else {}

        now = datetime.now(BERLIN)
        ref = end - timedelta(minutes=15) if d < now.date() else start if d > now.date() else now
        ref24 = ref - timedelta(hours=24)
        active_rows = active_event_rows(selected_rows, ref)

        def zone_stats(z: str) -> dict[str, Any]:
            known = [r for r in all_rows if r["zone"] == z and r.get("unavailable") is not None]
            now_rows = active_event_rows(known, ref)
            before_rows = active_event_rows(known, ref24)
            forced_now = capacity_total([r for r in now_rows if r.get("business") == "A54"])
            forced_before = capacity_total([r for r in before_rows if r.get("business") == "A54"])
            total_now, total_before = capacity_total(now_rows), capacity_total(before_rows)
            ok = zone_complete[z]
            has_source = selected_source[z] is not None
            val = (lambda x: round(x, 1) if has_source else None)
            # Numbers stay visible when a zone is incomplete; the flag says so.
            return {
                "unavailable_mw": val(total_now),
                "forced_mw": val(forced_now),
                "planned_mw": val(capacity_total([r for r in now_rows if r.get("business") == "A53"])),
                "delta_24h_mw": val(total_now - total_before),
                "forced_delta_24h_mw": val(forced_now - forced_before),
                "active_events": len(now_rows), "complete": ok, "source": selected_source[z],
                "a77_plant_only_units": a77_added[z],
                "breakdown": outage_breakdown(now_rows, has_source),
            }
        zone_kpis = {z: zone_stats(z) for z in zones}

        upcoming_candidates = [r for r in selected_rows if ref < (r.get("event_start") or r["start"]) < end and (r.get("unavailable") is None or float(r["unavailable"]) > 0)]
        reportable_by_event: dict[tuple[Any, ...], dict[str, Any]] = {}
        upcoming_by_event: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in selected_rows:
            if r.get("unavailable") is not None and float(r["unavailable"]) <= 0:
                continue
            k = event_key(r)
            if k not in reportable_by_event or r["start"] < reportable_by_event[k]["start"]:
                reportable_by_event[k] = r
        for r in upcoming_candidates:
            k = event_key(r)
            if k not in upcoming_by_event or r["start"] < upcoming_by_event[k]["start"]:
                upcoming_by_event[k] = r
        reportable_rows = list(reportable_by_event.values())
        upcoming_rows = list(upcoming_by_event.values())
        display_rows = sorted(active_rows + upcoming_rows, key=lambda r: (0 if r in active_rows else 1, r["start"], -float(r.get("unavailable") or 0)))

        current_total = capacity_total(active_rows) if complete else None
        largest_active = max(active_rows, key=lambda r: float(r.get("unavailable") or 0), default=None)
        next_event = min(upcoming_rows, key=lambda r: r["start"], default=None)
        errors = [f"{z}:{doc}:{state}" for z, docs in source_status.items() for doc, state in docs.items() if state in ("error", "truncated", "parse_error")]
        no_data = [f"{z}:{doc}" for z, docs in source_status.items() for doc, state in docs.items() if state in ("no_data", "empty")]

        def published(r) -> datetime | None:
            try:
                return parse_dt(r["created"]) if r.get("created") else None
            except Exception:
                return None

        def notice(r):
            pub = published(r)
            try:
                rev = int(r.get("revision") or 1)
            except (TypeError, ValueError):
                rev = 1
            return {
                "zone": r["zone"], "plant": r["plant"], "psr": r["psr"], "fuel": PSR_NAMES.get(r.get("psr"), r.get("psr")),
                "source": r["document_type"],
                "unavailable_mw": round(r["unavailable"], 1) if r["unavailable"] is not None else None,
                "nominal_mw": r.get("nominal"), "available_mw": r.get("available"),
                "resource_id": r.get("resource_id"), "production_id": r.get("production_id"),
                "reason_code": r.get("reason_code"), "reason_text": r.get("reason_text"),
                "start": r["start"].isoformat(), "end": r["end"].isoformat(), "business": r["business"],
                "event_start": (r.get("event_start") or r["start"]).isoformat(),
                "event_end": (r.get("event_end") or r["end"]).isoformat(),
                "published": pub.isoformat() if pub else None, "revision": rev,
                "recent": bool(pub and ref24 <= pub <= max(ref, now)),
                "active": r in active_rows,
                "state": "active" if r in active_rows else "upcoming" if r["start"] > ref else "ended",
                **classify_notice(r),
            }
        representatives = {event_key(r): r for r in reportable_rows}
        representatives.update({event_key(r): r for r in upcoming_rows})
        representatives.update({event_key(r): r for r in active_rows})
        all_notices = sorted((notice(r) for r in representatives.values()),
            key=lambda r: ({"active": 0, "upcoming": 1, "ended": 2}[r["state"]], -(r["unavailable_mw"] or 0), r["start"], r["plant"]))
        recent = sorted((n for n in all_notices if n["recent"]), key=lambda n: n["published"] or "", reverse=True)

        return {
            "date": d.isoformat(), "updated": datetime.now(BERLIN).isoformat(),
            "series": {z: as_points(by_zone[z]) if selected_source[z] is not None else [] for z in zones},
            "total_series": as_points(total_series),
            "top": [notice(r) for r in display_rows[:12]],
            "notices": all_notices,
            "recent": recent,
            "zones": zone_kpis,
            "breakdown": outage_breakdown(active_rows, complete),
            "kpi": {
                "de_unavailable_mw": zone_kpis["DE_LU"]["unavailable_mw"],
                "de_forced_mw": zone_kpis["DE_LU"]["forced_mw"],
                "de_delta_24h_mw": zone_kpis["DE_LU"]["delta_24h_mw"],
                "de_forced_delta_24h_mw": zone_kpis["DE_LU"]["forced_delta_24h_mw"],
                "unavailable_mw": round(current_total, 1) if current_total is not None else None,
                "as_of": ref.isoformat(), "compare_to": ref24.isoformat(),
                "scope": "A80 generation units + A77 plants without unit notices · DE-LU + FR + NL + BE",
                "coverage_complete": complete,
                "unknown_capacity_events": len({event_key(r) for r in unknown_rows}),
                "reportable_events": len(reportable_rows), "active_events": len(active_rows),
                "recent_notices": len(recent),
                "recent_forced_notices": sum(1 for n in recent if n["notice_type"] == "forced"),
                "largest_active_mw": round(largest_active["unavailable"], 1) if largest_active and largest_active["unavailable"] is not None else None,
                "largest_active_unit": largest_active["plant"] if largest_active else None,
                "next_event_at": next_event["start"].isoformat() if next_event else None,
                "next_event_mw": round(next_event["unavailable"], 1) if next_event and next_event["unavailable"] is not None else None,
                "next_event_unit": next_event["plant"] if next_event else None,
                "changes": change_windows(total_series, ref) if complete else {"d15_mw": None, "d30_mw": None, "d60_mw": None},
            },
            "source_status": source_status, "selected_source": selected_source,
            "page_counts": page_counts, "errors": errors, "no_data": no_data,
            "methodology": "A80 unit notices are primary. A77 plant notices are added only for production units without any A80 notice in the window, so plant-only reporters (wind parks, many CCGTs) are included without double counting. Overlapping notices for one resource contribute their maximum. Change vs 24h uses today's knowledge of both times; 'recent' uses the document's publication timestamp.",
        }

    return cached(key, ttl_for(d, max(CACHE_SECONDS, 900)), force, work)


def _query_control_area_document(document_type: str, area: str, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], str]:
    params: dict[str, Any] = {"documentType": document_type, "controlArea_Domain": AREAS[area]}
    try:
        rows = parse_timeseries(entsoe_request(params, start, end))
        for r in rows:
            r["source_area"] = area
        return rows, "ok" if rows else "empty"
    except LookupError:
        return [], "no_data"
    except Exception as e:
        log_upstream(f"{document_type} {area}", e)
        return [], "error"


def _query_balancing_doc_with_fallback(document_type: str, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    """Prefer a complete four-control-area set; otherwise try aggregate scopes.

    Returning a partial Germany aggregate as if it were complete can create a
    plausible but wrong A86 volume. We therefore try DE-LU/DE before exposing a
    partial control-area result, and callers can suppress partial numerics.
    """
    statuses: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_query_control_area_document, document_type, area, start, end): area for area in BALANCING_AREAS}
        for fut in as_completed(futs):
            area = futs[fut]
            r, status = fut.result(); statuses[area] = status; rows.extend(r)
    control_complete = bool(rows) and all(statuses.get(area) == "ok" for area in BALANCING_AREAS)
    if control_complete:
        return rows, statuses, "German control areas"
    partial_rows = rows
    for area in ("DE_LU", "DE"):
        r, status = _query_control_area_document(document_type, area, start, end)
        statuses[area] = status
        if r:
            return r, statuses, area
    if partial_rows:
        return partial_rows, statuses, "German control areas (partial)"
    return [], statuses, "none"


def parse_aggregated_bids(content: bytes, process_type: str) -> list[dict[str, Any]]:
    """A24: offered, activated and unavailable quantities are independent MW fields."""
    out = []
    for doc in xml_documents(content):
        root = ET.fromstring(doc)
        process = text_of(root, "process.processType") or process_type
        if process != process_type:
            raise ValueError("Unexpected A24 process type")
        meta = {"created": text_of(root, "createdDateTime"), "revision": text_of(root, "revisionNumber"),
                "doc_mrid": text_of(root, "mRID"), "area_eic": text_of(root, "area_Domain.mRID"), "process": process}
        for ts in elements(root, "TimeSeries"):
            product = text_of(ts, "standard_MarketProduct.marketProductType", "original_MarketProduct.marketProductType", "marketProductType")
            base = {**meta, "mrid": text_of(ts, "mRID"), "product": product or "unspecified",
                    "direction": text_of(ts, "flowDirection.direction"), "unit": text_of(ts, "quantity_Measure_Unit.name"),
                    "cancelled": text_of(ts, "cancelledTS") == "A01"}
            if base["unit"] not in (None, "MAW"):
                raise ValueError("A24 activation must be in MAW")
            if base["direction"] not in ("A01", "A02"):
                raise ValueError("Unknown A24 direction")
            if base["cancelled"]:
                out.append({**base, "ts": None, "activated": None})
                continue
            for period in elements(ts, "Period"):
                pstart, pend = parse_dt(text_of(period, "start")), parse_dt(text_of(period, "end"))
                step = parse_iso_duration(text_of(period, "resolution"))
                points = []
                for point in elements(period, "Point"):
                    pos = int(text_of(point, "position"))
                    values = {}
                    for key, tag in (("offered", "quantity"), ("activated", "secondaryQuantity"), ("unavailable", "unavailable_Quantity.quantity")):
                        raw = text_of(point, tag)
                        values[key] = float(raw) if raw is not None else None
                        if values[key] is not None and (not math.isfinite(values[key]) or values[key] < 0):
                            raise ValueError("Invalid A24 quantity")
                    if pos < 1: raise ValueError("Invalid A24 position")
                    points.append((pos, values))
                points.sort(key=lambda x: x[0])
                for i, (pos, values) in enumerate(points):
                    stop = pstart + pos * step
                    if text_of(ts, "curveType") == "A03":
                        stop = pstart + (points[i+1][0]-1)*step if i+1 < len(points) else pend
                    t = pstart + (pos-1)*step
                    while t < min(stop, pend):
                        out.append({**base, **values, "ts": t, "resolution": text_of(period, "resolution")})
                        t += step
    return out


def _query_aggregated_bids(process_type: str, start: datetime, end: datetime, area: str) -> tuple[list[dict[str, Any]], str]:
    try:
        rows = parse_aggregated_bids(entsoe_request({"documentType": "A24", "area_Domain": AREAS[area], "processType": process_type}, start, end), process_type)
        for r in rows:
            if r.get("area_eic") and r["area_eic"] != AREAS[area]:
                raise ValueError("A24 response area mismatch")
            r["source_area"] = area
        rows = [r for r in rows if r["ts"] is None or start <= r["ts"] < end]
        return rows, "ok" if rows else "empty"
    except LookupError:
        return [], "no_data"
    except (ValueError, ET.ParseError) as e:
        log_upstream(f"A24/{process_type} {area} parse", e)
        return [], "parse_error"
    except Exception as e:
        log_upstream(f"A24/{process_type} {area}", e)
        return [], "error"


def _dedupe_bids(rows):
    # TimeSeries mRID is document-local and frequently just 1/2. It cannot
    # identify a country-wide series or separate products/areas.
    docs = {}
    for r in rows:
        key = (r.get("source_area"), r.get("process"), r.get("doc_mrid"))
        if r.get("doc_mrid"):
            docs[key] = max(docs.get(key, _row_rank(r)), _row_rank(r))
    chosen = {}
    for r in rows:
        dk = (r.get("source_area"), r.get("process"), r.get("doc_mrid"))
        if r.get("doc_mrid") and _row_rank(r) != docs[dk]: continue
        if r.get("cancelled") or r.get("ts") is None: continue
        key = (r.get("source_area"), r.get("process"), r.get("product"), r.get("direction"), r["ts"])
        # Revision numbers from unrelated documents are not comparable.
        rank = (_row_rank(r)[1], _row_rank(r)[0])
        if key not in chosen or rank >= chosen[key][0]: chosen[key] = (rank, r)
    return [r for _, r in chosen.values()]


def _activation_series(rows: list[dict[str, Any]]) -> tuple[dict[datetime, float], dict[datetime, float], dict[datetime, float]]:
    rows = _dedupe_bids(rows)
    # Each published area/process/product channel must cover a timestamp.
    # Missing activated quantities and absent opposite directions are unknown.
    parts = {"A01": {}, "A02": {}}
    for r in rows:
        if r.get("activated") is None: continue
        key = (r.get("source_area"), r.get("process"), r.get("product"))
        parts[r["direction"]].setdefault(key, {})[r["ts"]] = r["activated"]
    up = add_series_complete(*parts["A01"].values())
    down = add_series_complete(*parts["A02"].values())
    return up, down, subtract_series(up, down)


def _select_activation_rows(primary, fallback, processes):
    """Choose split processes OR generic fallback per area/product/direction/MTU.

    A51/A47 never supplement a split value, including a published zero.
    Offered-only generic points never stand in for activated MW.
    """
    primary, fallback = _dedupe_bids(primary), _dedupe_bids(fallback)
    channels = {}
    groups = {}
    for r in primary:
        if r.get("activated") is None: continue
        channel = (r.get("source_area"), r.get("product"), r.get("direction"))
        channels.setdefault(channel, set()).add(r["process"])
        groups.setdefault((*channel, r["ts"]), []).append(r)
    selected = {}
    for key, group in groups.items():
        if {r["process"] for r in group} == channels[key[:3]]:
            selected[key] = group
    for r in fallback:
        key = (r.get("source_area"), r.get("product"), r.get("direction"), r["ts"])
        # An unspecified-product aggregate may cover standard/specific products.
        # Never add it to an already selected product at the same area/direction/MTU.
        overlaps = any(k[0] == key[0] and k[2:] == key[2:] and
                       (key[1] in (None, "unspecified") or k[1] in (None, "unspecified")) for k in selected)
        if r.get("activated") is not None and key not in selected and not overlaps:
            selected[key] = [r]
    # Collapse split channels after choosing, retaining product and geography.
    return [{**group[0], "process": "selected", "activated": sum(r["activated"] for r in group),
             "selected_processes": sorted({r["process"] for r in group})} for group in selected.values()]


def _fetch_activation_family(family, start, end):
    primary_codes, fallback_code = (("A67", "A68"), "A51") if family == "aFRR" else (("A60", "A61"), "A47")
    status = {area: {} for area in BALANCING_AREAS}
    raw = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_query_aggregated_bids, code, start, end, area): (area, code)
                   for area in BALANCING_AREAS for code in primary_codes}
        for fut in as_completed(futures):
            area, code = futures[fut]
            raw[area, code], status[area][code] = fut.result()
    per_area = {}; all_selected = []
    for area in BALANCING_AREAS:
        primary = [r for code in primary_codes for r in raw[area, code]]
        # Generic publications can fill gaps, but cannot be added to split data.
        provisional = _select_activation_rows(primary, [], primary_codes)
        _, _, net = _activation_series(provisional)
        expected = int((end-start).total_seconds() / 900)
        fallback = []
        if len(net) < expected:
            fallback, status[area][fallback_code] = _query_aggregated_bids(fallback_code, start, end, area)
        else:
            status[area][fallback_code] = "not_needed"
        selected = _select_activation_rows(primary, fallback, primary_codes)
        up, down, net = _activation_series(selected)
        failed = any(v in ("error", "parse_error") for v in status[area].values())
        # A failed split source cannot be inferred to be zero.
        if failed: up, down, net = {}, {}, {}
        all_selected.extend(selected)
        per_area[area] = {"up": up, "down": down, "net": net,
                          "state": "error" if failed else "ok" if net else "partial" if selected else "no_data",
                          "selected_processes": sorted({c for r in selected for c in r["selected_processes"]})}
    up = add_series_complete(*(per_area[a]["up"] for a in BALANCING_AREAS))
    down = add_series_complete(*(per_area[a]["down"] for a in BALANCING_AREAS))
    net = subtract_series(up, down)
    state = "ok" if net else "partial" if any(v["net"] for v in per_area.values()) else "error" if any(v["state"] == "error" for v in per_area.values()) else "no_data"
    return {"up": up, "down": down, "net": net, "state": state, "areas": per_area, "sources": status}

def fetch_balancing(day: str | None, force: bool = False) -> dict[str, Any]:
    start, end, d = day_bounds(day)
    key = f"balancing:v5:{d.isoformat()}"

    def work():
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(_fetch_activation_family, "aFRR", start, end)
            fm = pool.submit(_fetch_activation_family, "mFRR", start, end)
            afrr, mfrr = fa.result(), fm.result()
        afrr_up, afrr_down, afrr_net = afrr["up"], afrr["down"], afrr["net"]
        mfrr_up, mfrr_down, mfrr_net = mfrr["up"], mfrr["down"], mfrr["net"]
        afrr_state, mfrr_state = afrr["state"], mfrr["state"]
        activation_net = add_series_complete(afrr_net, mfrr_net)

        price_rows_raw, a85_status, a85_scope = _query_balancing_doc_with_fallback("A85", start, end)
        volume_rows_raw, a86_status, a86_scope = _query_balancing_doc_with_fallback("A86", start, end)
        # A partial subset of the four German control areas is diagnostic data,
        # not a Germany-wide total/price. Suppress the numeric series unless an
        # aggregate DE-LU/DE fallback was available.
        price_partial = a85_scope.endswith("(partial)")
        volume_partial = a86_scope.endswith("(partial)")
        price_rows = [] if price_partial else latest_revision_rows(price_rows_raw, ("source_area", "category", "ts"))
        # Direction is NOT part of the revision identity: the newest revision of
        # an MTU replaces all older directions (see latest_revision_groups).
        volume_rows = [] if volume_partial else latest_revision_groups(volume_rows_raw, ("source_area", "business", "ts"))
        if a85_scope == "German control areas":
            times = set.intersection(*({r["ts"] for r in price_rows if r.get("source_area") == a} for a in BALANCING_AREAS))
            price_rows = [r for r in price_rows if r["ts"] in times]
        a86_ignored: dict[str, dict[str, list[str]]] = {}
        if a86_scope == "German control areas":
            per_area = {}
            for a in BALANCING_AREAS:
                per_area[a], ignored = imbalance_volume_series([r for r in volume_rows if r.get("source_area") == a])
                if ignored:
                    a86_ignored[a] = ignored
            imbalance_volume = add_series_complete(*(per_area[a] for a in BALANCING_AREAS))
        else:
            imbalance_volume, ignored = imbalance_volume_series(volume_rows)
            if ignored:
                a86_ignored[a86_scope] = ignored
        if a86_ignored:
            LOG.warning("A86: ignored unexpected codes %s", a86_ignored)
        categories = {r.get("category") for r in price_rows}
        price_long = mean_series(price_rows, "A04") if "A04" in categories else {}
        price_short = mean_series(price_rows, "A05") if "A05" in categories else {}
        uncategorized_rows = [r for r in price_rows if not r.get("category")]
        price_single = mean_series(uncategorized_rows) if uncategorized_rows else {}

        # Germany settles imbalance at one price (reBAP). When the A04/A05
        # categories are identical at every MTU, show one line, not two.
        common_price_t = set(price_long) & set(price_short)
        if not price_single and common_price_t and all(abs(price_long[t] - price_short[t]) < 1e-6 for t in common_price_t) \
                and set(price_long) == set(price_short):
            price_single = dict(price_long)
            price_mode = "single"
        else:
            price_mode = "single" if price_single else "dual" if (price_long or price_short) else "none"

        # D > 0 surplus (long), D < 0 deficit (short); DDD v3r4 17.1.H, and
        # cross-checked live against aFRR direction and NRV-Saldo.
        ivp = latest_point(imbalance_volume)
        imbalance_state = None
        if ivp:
            imbalance_state = "surplus" if ivp[1] > 0 else "deficit" if ivp[1] < 0 else "balanced"
        imbalance_1h = trailing_mean(imbalance_volume, ivp[0]) if ivp else None
        price_series_for_status = price_single or price_short or price_long
        price_point = latest_point(price_series_for_status)
        # Same-day reBAP/imbalance values are operational estimates; the settled
        # reBAP follows days to weeks later as quality-assured data
        # (netztransparenz.de). Only an explicit docStatus A02 counts as final.
        price_status = publication_status(price_rows, price_point[0] if price_point else None)
        volume_status = publication_status(volume_rows, ivp[0] if ivp else None)

        # Germany-wide activation from ENTSO-E needs all four LFAs. When one is
        # missing, expose the sum of the publishing areas explicitly labelled
        # as partial instead of showing nothing at all.
        def partial_family(fam: dict[str, Any]) -> dict[str, Any]:
            have = [a for a in BALANCING_AREAS if fam["areas"].get(a, {}).get("net")]
            missing = [a for a in BALANCING_AREAS if a not in have]
            net = add_series_complete(*(fam["areas"][a]["net"] for a in have)) if have else {}
            return {"net": net, "areas": have, "missing": missing}
        afrr_partial, mfrr_partial = partial_family(afrr), partial_family(mfrr)

        # Optional: complete German block data from netztransparenz.de.
        ntp_data: dict[str, Any] | None = ntp.fetch_block(start, end) if ntp.configured() else None
        nrv = ntp_data["nrv"] if ntp_data else {}
        ntp_afrr_net = subtract_series(ntp_data["afrr_up"], ntp_data["afrr_down"]) if ntp_data else {}
        ntp_mfrr_net = subtract_series(ntp_data["mfrr_up"], ntp_data["mfrr_down"]) if ntp_data else {}
        nrvp = latest_point(nrv)

        def latest_val(ss: dict[datetime, float]) -> float | None:
            pp = latest_point(ss); return round(pp[1], 3) if pp else None
        def doc_state(rows: list[dict[str, Any]], statuses: dict[str, str], scope: str) -> str:
            if scope.endswith("(partial)"): return "partial"
            if rows: return "ok"
            if any(v == "error" for v in statuses.values()): return "error"
            return "no_data"

        freshness_candidates = [latest_point(activation_net), latest_point(afrr_net), latest_point(mfrr_net), latest_point(imbalance_volume), latest_point(price_single), latest_point(price_long), latest_point(price_short), nrvp]
        fresh = [p for p in freshness_candidates if p]
        freshest = max(fresh, key=lambda p:p[0]) if fresh else None
        activation_state = "ok" if activation_net else "partial" if (afrr_net or mfrr_net or afrr_partial["net"]) else "partial" if "partial" in (afrr_state, mfrr_state) else "error" if "error" in (afrr_state, mfrr_state) else "no_data"
        de_afrr = ntp_afrr_net or afrr_net
        de_mfrr = ntp_mfrr_net or mfrr_net
        activation_source = "netztransparenz.de (German block, all 4 TSOs)" if ntp_afrr_net else "ENTSO-E A24 (all 4 LFAs)" if afrr_net else "ENTSO-E A24 partial" if afrr_partial["net"] else None

        sources = {
            "12.3.E": {"state": activation_state, "label": "ENTSO-E activated balancing energy", "scope": "4 German LFA/SCA · A67/A68 aFRR · A60/A61 mFRR"},
            "A85": {"state": doc_state(price_rows_raw, a85_status, a85_scope), "label": "Imbalance price (reBAP)", "scope": a85_scope},
            "A86": {"state": doc_state(volume_rows_raw, a86_status, a86_scope), "label": "Total imbalance volume", "scope": a86_scope,
                    **({"ignored": a86_ignored} if a86_ignored else {})},
        }
        if ntp_data is not None:
            st = ntp_data["status"]
            state = "ok" if all(v == "ok" for v in st.values()) else "partial" if any(v == "ok" for v in st.values()) else "error"
            sources["NTP"] = {"state": state, "label": "netztransparenz.de NRV-Saldo + aFRR/mFRR", "scope": "German control block", "detail": st, "diag": ntp_data.get("diag", {})}
        else:
            sources["NTP"] = {"state": "not_configured", "label": "netztransparenz.de", "scope": "set NTP_CLIENT_ID / NTP_CLIENT_SECRET"}

        return {
            "date": d.isoformat(), "updated": datetime.now(BERLIN).isoformat(),
            "series": {
                "aFRR activated up": as_points(afrr_up), "aFRR activated down": as_points(afrr_down), "aFRR net": as_points(afrr_net),
                "mFRR activated up": as_points(mfrr_up), "mFRR activated down": as_points(mfrr_down), "mFRR net": as_points(mfrr_net),
                "aFRR net partial": as_points(afrr_partial["net"]), "mFRR net partial": as_points(mfrr_partial["net"]),
                "Net activation": as_points(activation_net), "Net imbalance volume": as_points(imbalance_volume),
                "Imbalance price": as_points(price_single), "Imbalance price long": as_points(price_long), "Imbalance price short": as_points(price_short),
                "NRV-Saldo": as_points(nrv), "aFRR net DE": as_points(de_afrr), "mFRR net DE": as_points(de_mfrr),
            },
            "kpi": {
                "imbalance_volume_mwh": round(ivp[1], 3) if ivp else None, "imbalance_state": imbalance_state,
                "imbalance_avg_mw": round(ivp[1] * 4, 1) if ivp else None,
                "imbalance_1h_avg_mw": round(imbalance_1h * 4, 1) if imbalance_1h is not None else None,
                "imbalance_status": volume_status, "price_status": price_status,
                "as_of": ivp[0].isoformat() if ivp else None, "basis": "A86 total imbalance volume",
                "changes": change_windows(imbalance_volume),
                "net_activation_mw": latest_val(activation_net), "afrr_net_mw": latest_val(afrr_net), "mfrr_net_mw": latest_val(mfrr_net),
                "afrr_de_mw": latest_val(de_afrr), "mfrr_de_mw": latest_val(de_mfrr), "activation_source": activation_source,
                "afrr_partial_mw": latest_val(afrr_partial["net"]), "afrr_partial_areas": afrr_partial["areas"], "afrr_missing_areas": afrr_partial["missing"],
                "mfrr_partial_mw": latest_val(mfrr_partial["net"]), "mfrr_missing_areas": mfrr_partial["missing"],
                "nrv_saldo_mw": round(nrvp[1], 1) if nrvp else None, "nrv_as_of": nrvp[0].isoformat() if nrvp else None,
                "nrv_state": ("deficit" if nrvp[1] > 0 else "surplus" if nrvp[1] < 0 else "balanced") if nrvp else None,
                "price_mode": price_mode,
                "imbalance_price_eur_mwh": latest_val(price_single),
                "imbalance_price_long_eur_mwh": latest_val(price_long), "imbalance_price_short_eur_mwh": latest_val(price_short),
                "price_as_of": latest_timestamp(price_single or price_short or price_long),
                "day_avg_price_eur_mwh": round(statistics.mean(price_single.values()), 2) if price_single else None,
                "day_max_price_eur_mwh": round(max(price_single.values()), 2) if price_single else None,
                "day_min_price_eur_mwh": round(min(price_single.values()), 2) if price_single else None,
            },
            "freshness": {
                "latest_through": freshest[0].isoformat() if freshest else None,
                "activation_through": latest_timestamp(de_afrr or afrr_partial["net"]),
                "volume_through": latest_timestamp(imbalance_volume),
                "price_through": latest_timestamp(price_single or price_short or price_long),
                "nrv_through": latest_timestamp(nrv),
            },
            "sources": sources,
            "activation_sources": {"aFRR": afrr["sources"], "mFRR": mfrr["sources"]},
            "activation_areas": {family: {area: {"state": v["state"], "selected_processes": v["selected_processes"],
                "up": as_points(v["up"]), "down": as_points(v["down"]), "net": as_points(v["net"])}
                for area, v in data["areas"].items()} for family, data in (("aFRR", afrr), ("mFRR", mfrr))},
            "note": "Imbalance: A86 businessType A19 (total imbalance volume D), MWh per 15 min; flowDirection A01 = surplus (+, long), A02 = deficit (-, short), A03 = balanced (0); unknown codes are dropped and listed under sources.A86.ignored. reBAP: A85; same-day values are preliminary (identical to the TSOs' AEP-Schaetzer on netztransparenz.de, checked 24.09.2026), final only if docStatus = A02. Germany-wide aFRR/mFRR comes from netztransparenz.de when configured; ENTSO-E A24 lacks Amprion, so its sum is shown only as an explicitly partial figure.",
        }

    return cached(key, ttl_for(d, max(CACHE_SECONDS, 600)), force, work)

app = FastAPI(
    title="ENTSO-E Desk",
    version=VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def optional_basic_auth(request, call_next):
    """Optional Basic Auth plus conservative browser security headers."""
    if AUTH_ENABLED and request.url.path != "/health":
        auth = request.headers.get("Authorization", "")
        valid = False
        if auth.startswith("Basic "):
            try:
                raw = base64.b64decode(auth[6:], validate=True).decode("utf-8")
                username, password = raw.split(":", 1)
                valid = hmac.compare_digest(username, DASHBOARD_USERNAME) and hmac.compare_digest(
                    password, DASHBOARD_PASSWORD
                )
            except (ValueError, UnicodeDecodeError):
                valid = False
        if not valid:
            response = PlainTextResponse(
                "Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="ENTSO-E Desk"'},
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; worker-src 'self' blob:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    return response


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__REFRESH_SECONDS__", str(REFRESH_SECONDS)))


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION, "configured": bool(API_KEY), "auth_enabled": AUTH_ENABLED, "public_ok": PUBLIC_OK,
            "netztransparenz": ntp.configured(), "time": datetime.now(BERLIN).isoformat()}


def panel_response(name, fn):
    data = fn()
    return {**data, "quality": panel_quality(name, data)}


MIN_DAY = Date(2015, 1, 5)


def check_day(day: Date | None) -> None:
    # Arbitrary dates are cache misses that each cost ~150 upstream calls.
    if day is None:
        return
    today = datetime.now(BERLIN).date()
    if not (MIN_DAY <= day <= today + timedelta(days=2)):
        raise HTTPException(status_code=422, detail=f"day must be between {MIN_DAY} and {today + timedelta(days=2)}")


def endpoint_guard(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=f"ENTSO-E: {e}")
    except Exception as e:
        # No traceback: chained upstream exceptions may carry request URLs.
        log_upstream("panel failed", e, logging.ERROR)
        raise HTTPException(status_code=502, detail=mask_secret(str(e)))


@app.get("/api/renewables")
def api_renewables(day: Date | None = None):
    check_day(day)
    return endpoint_guard(lambda: panel_response("renewables", lambda: fetch_renewables(day.isoformat() if day else None, False)))


@app.get("/api/load")
def api_load(day: Date | None = None):
    check_day(day)
    return endpoint_guard(lambda: panel_response("load", lambda: fetch_load(day.isoformat() if day else None, False)))


@app.get("/api/borders")
def api_borders(day: Date | None = None, neighbors: str = Query(default=",".join(DEFAULT_NEIGHBORS))):
    check_day(day)
    selected = [x.strip().upper() for x in neighbors.split(",") if x.strip()]
    return endpoint_guard(lambda: panel_response("borders", lambda: fetch_borders(day.isoformat() if day else None, selected, False)))


@app.get("/api/outages")
def api_outages(day: Date | None = None):
    check_day(day)
    return endpoint_guard(lambda: panel_response("outages", lambda: fetch_outages(day.isoformat() if day else None, False)))


@app.get("/api/balancing")
def api_balancing(day: Date | None = None):
    check_day(day)
    return endpoint_guard(lambda: panel_response("balancing", lambda: fetch_balancing(day.isoformat() if day else None, False)))




if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
