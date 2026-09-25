"""Optional netztransparenz.de (German TSOs) balancing data.

Why: ENTSO-E A24 activation data is incomplete for Germany (Amprion does not
publish), so a Germany-wide aFRR/mFRR figure is impossible from ENTSO-E alone.
The four German TSOs publish the block-wide NRV-Saldo and activated aFRR/mFRR
on netztransparenz.de, including Amprion.

Access needs free API credentials (OAuth2 client credentials) from
https://extranet.netztransparenz.de -> set NTP_CLIENT_ID / NTP_CLIENT_SECRET.
Without them the dashboard falls back to ENTSO-E and says so.

Sign convention (German): NRV-Saldo > 0 = the control block is short
(Unterdeckung, positive balancing energy used); < 0 = long (Überdeckung).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

TOKEN_URL = "https://identity.netztransparenz.de/users/connect/token"
BASE_URL = os.getenv("NTP_BASE_URL", "https://ds.netztransparenz.de/api/v1/data")
CLIENT_ID = os.getenv("NTP_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("NTP_CLIENT_SECRET", "").strip()
TIMEOUT = int(os.getenv("NTP_HTTP_TIMEOUT", "30"))

UTC = timezone.utc
LOG = logging.getLogger("de_power_desk.ntp")
_OFFSETS = {"UTC": 0, "GMT": 0, "CET": 1, "MEZ": 1, "CEST": 2, "MESZ": 2}

_token: dict[str, Any] = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()
_call_lock = threading.Lock()
_last_call = [0.0]


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def _get_token(session: requests.Session) -> str:
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        r = session.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": CLIENT_ID,
                                           "client_secret": CLIENT_SECRET}, timeout=TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"netztransparenz token: HTTP {r.status_code}")
        data = r.json()
        _token["value"] = data["access_token"]
        _token["expires"] = time.time() + float(data.get("expires_in", 3600))
        return _token["value"]


# The documentation shows the date range as ".../dateFrom={date}/dateTo={date}"
# without a concrete example. Both readings are tried; the first variant that
# answers is remembered for the rest of the process.
URL_VARIANTS = ("path", "query")
_variant = {"value": None}


def _url(path: str, start: datetime, end: datetime, variant: str) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S"
    f, t = start.astimezone(UTC).strftime(fmt), end.astimezone(UTC).strftime(fmt)
    if variant == "path":
        return f"{BASE_URL}/{path}/{f}/{t}"
    return f"{BASE_URL}/{path}?dateFrom={f}&dateTo={t}"


def _request(session: requests.Session, url: str, token: str) -> requests.Response:
    # Published limit: 2 requests/s per IP; repeated violations block the IP for 2h.
    with _call_lock:
        wait = 0.6 - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            return session.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "text/csv"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RuntimeError(f"netztransparenz network: {type(e).__name__}") from None


def _get(session: requests.Session, path: str, start: datetime, end: datetime, diag: dict | None = None) -> str:
    token = _get_token(session)
    variants = [_variant["value"]] if _variant["value"] else list(URL_VARIANTS)
    last = None
    for variant in variants:
        r = _request(session, _url(path, start, end, variant), token)
        if diag is not None:
            diag.setdefault("http", []).append(f"{variant}:{r.status_code}")
        if r.status_code in (400, 404, 405) and len(variants) > 1:
            last = r
            continue
        if r.status_code == 401:
            _token["value"] = None
            raise RuntimeError("netztransparenz: 401 unauthorized (client id/secret or API scope)")
        if r.status_code == 204 or (r.ok and not r.content.strip()):
            _variant["value"] = variant
            raise LookupError("netztransparenz: no data")
        if not r.ok:
            raise RuntimeError(f"netztransparenz {path}: HTTP {r.status_code}")
        text = r.content.decode("utf-8-sig", "replace")
        if text.lstrip().startswith(("<", "{")) and ";" not in text.splitlines()[0]:
            raise ValueError("netztransparenz: expected CSV, got HTML/JSON")
        _variant["value"] = variant
        if diag is not None:
            diag["variant"] = variant
            diag["columns"] = text.splitlines()[0][:300] if text else ""
        return text
    raise RuntimeError(f"netztransparenz {path}: HTTP {last.status_code if last is not None else '?'} for all URL variants")


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw.upper() in ("N.A.", "NA", "N/A", "-"):
        return None
    try:
        return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)
    except ValueError:
        return None


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Semicolon CSV: Datum;Zeitzone;von;bis;...;<value columns>. Returns rows
    with a UTC 'ts' (start of the quarter hour) plus the raw columns parsed as
    floats where possible."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(";")]
    idx = {h.lower(): i for i, h in enumerate(header)}
    for need in ("datum", "von"):
        if need not in idx:
            raise ValueError(f"netztransparenz CSV without column {need!r}")
    out = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(";")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        day = datetime.strptime(cells[idx["datum"]], "%d.%m.%Y")
        hh, mm = (int(x) for x in cells[idx["von"]].split(":")[:2])
        tz = cells[idx["zeitzone"]].upper() if "zeitzone" in idx else "UTC"
        if tz not in _OFFSETS:
            raise ValueError(f"netztransparenz: unknown time zone {tz!r}")
        ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC) - timedelta(hours=_OFFSETS[tz])
        row: dict[str, Any] = {"ts": ts}
        for h, i in idx.items():
            if h in ("datum", "zeitzone", "von", "bis"):
                continue
            row[h] = _num(cells[i]) if h not in ("datenkategorie", "datentyp", "einheit") else cells[i]
        out.append(row)
    return out


def _column(rows: list[dict[str, Any]], *names: str) -> dict[datetime, float]:
    for name in names:
        key = name.lower()
        series = {r["ts"]: r[key] for r in rows if r.get(key) is not None}
        if series:
            return series
    return {}


def fetch_block(start: datetime, end: datetime) -> dict[str, Any]:
    """NRV-Saldo and activated aFRR/mFRR for the German control block.

    'Betrieblich' (operational, near real time) first; if it has no data for
    the range, 'Qualitaetsgesichert' (quality-assured, later) is tried.
    """
    session = requests.Session()
    result: dict[str, Any] = {"status": {}, "diag": {}, "nrv": {}, "afrr_up": {}, "afrr_down": {}, "mfrr_up": {}, "mfrr_down": {}}
    jobs = {"nrv": "NrvSaldo/NRVSaldo", "afrr": "NrvSaldo/AktivierteSRL", "mfrr": "NrvSaldo/AktivierteMRL"}
    for name, base in jobs.items():
        diag: dict[str, Any] = {}
        result["diag"][name] = diag
        try:
            rows = []
            for flavour in ("Betrieblich", "Qualitaetsgesichert"):
                try:
                    rows = [r for r in parse_csv(_get(session, f"{base}/{flavour}", start, end, diag)) if start <= r["ts"] < end]
                except LookupError:
                    rows = []
                if rows:
                    diag["flavour"] = flavour
                    break
            if not rows:
                raise LookupError("no rows")
            if name == "nrv":
                result["nrv"] = _column(rows, "Deutschland", "NRV-Saldo")
                ok = bool(result["nrv"])
            else:
                result[f"{name}_up"] = {t: abs(v) for t, v in _column(rows, "Deutschland (Positiv)").items()}
                result[f"{name}_down"] = {t: abs(v) for t, v in _column(rows, "Deutschland (Negativ)").items()}
                ok = bool(result[f"{name}_up"] or result[f"{name}_down"])
            result["status"][name] = "ok" if ok else "parse_error"
            diag["points"] = len(rows)
        except LookupError:
            result["status"][name] = "no_data"
        except (ValueError, KeyError) as e:
            result["status"][name] = "parse_error"
            diag["error"] = str(e)[:200]
            LOG.warning("netztransparenz %s: parse error: %s", name, diag["error"])
        except Exception as e:
            result["status"][name] = "error"
            diag["error"] = str(e)[:200]
            LOG.warning("netztransparenz %s: %s: %s", name, type(e).__name__, diag["error"])
    return result
