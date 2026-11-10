"""Backtest of app/gapfill.py on real data: hide one control area, compare.

For every control area M, every technology and a gap starting every --step
quarter-hours, M's values after the gap start are hidden and the German
total is rebuilt with gapfill.fill() exactly as the dashboard does. The
error is rebuilt total - true total (= error of the estimate of M), grouped
by how long M has been silent.

  ENTSOE_API_KEY=... [NTP_CLIENT_ID=... NTP_CLIENT_SECRET=...] \
      python scripts/gapfill_backtest.py --start 2026-10-20 --end 2026-11-10 --cache /tmp/gapfill

--cache keeps the raw answers (about 20 requests; ENTSO-E can take 30 s each).
Without netztransparenz credentials the solar rows "scaled" are skipped.
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BACKGROUND_REFRESH", "0")

from app import gapfill, ntp  # noqa: E402
from app import main as m  # noqa: E402

Q = gapfill.Q
AREAS = m.BALANCING_AREAS
GAPS_H = (1, 2, 4, 6, 12, 24)
TECHS = ("Solar", "Wind Onshore", "Wind Offshore", "Load")
PSR = {name: code for code, name in m.PSR.items()}


def get(cache: Path | None, name: str, params: dict, start: datetime, end: datetime) -> bytes:
    path = cache / f"{name}.xml" if cache else None
    if path and path.exists():
        return path.read_bytes()
    last: Exception | None = None
    for _ in range(4):
        try:
            content = m.entsoe_request(params, start, end)
            if path:
                path.write_bytes(content)
            return content
        except LookupError:
            raise
        except Exception as e:  # slow days: retry
            last = e
    raise RuntimeError(f"{name}: {last}")


def load_data(start: datetime, end: datetime, cache: Path | None) -> dict:
    data: dict = {"act": {}, "da": {}}
    for area in ("DE", *AREAS):
        eic = m.AREAS[area]
        gen = m.parse_timeseries(get(cache, f"gen_{area}", {"documentType": "A75", "processType": "A16", "in_Domain": eic}, start, end))
        fc = m.parse_timeseries(get(cache, f"fc_{area}", {"documentType": "A69", "processType": "A01", "in_Domain": eic}, start, end))
        la = m.parse_timeseries(get(cache, f"load_{area}", {"documentType": "A65", "processType": "A16", "outBiddingZone_Domain": eic}, start, end))
        lf = m.parse_timeseries(get(cache, f"loadfc_{area}", {"documentType": "A65", "processType": "A01", "outBiddingZone_Domain": eic}, start, end))
        for tech, code in PSR.items():
            data["act"][(area, tech)] = m.series(gen, code, consumption=False)
            data["da"][(area, tech)] = m.series(fc, code)
        data["act"][(area, "Load")] = m.series(la)
        data["da"][(area, "Load")] = m.series(lf)
    data["proxy"] = {}
    if ntp.configured():
        path = cache / "ntp_solar.csv" if cache else None
        if path and path.exists():
            rows = ntp.parse_csv(path.read_text(encoding="utf-8"))
            data["proxy"] = {a: ntp._column(rows, c) for a, c in ntp.HOCHRECHNUNG_COLUMNS.items()}
        else:
            import requests
            text = ntp._get(requests.Session(), "hochrechnung/Solar", start, end)
            if path:
                path.write_text(text, encoding="utf-8")
            rows = ntp.parse_csv(text)
            data["proxy"] = {a: ntp._column(rows, c) for a, c in ntp.HOCHRECHNUNG_COLUMNS.items()}
    return data


def window(s: dict, lo: datetime, hi: datetime) -> dict:
    return {t: v for t, v in s.items() if lo <= t < hi}


def hide(data: dict, tech: str, area: str, t0: datetime, use_proxy: bool) -> dict:
    """gapfill.fill() for the 24 h after t0 with `area` silent since t0."""
    lo, hi = t0 - timedelta(hours=26), t0 + Q + timedelta(hours=24)
    reporters = [a for a in AREAS if data["act"][(a, tech)]]
    by_area = {a: window(data["act"][(a, tech)], lo, hi) for a in reporters}
    by_area[area] = {t: v for t, v in by_area[area].items() if t <= t0}
    official = {t: v for t, v in window(data["act"][("DE", tech)], lo, hi).items() if t <= t0}
    proxy = {a: window(s, lo, hi) for a, s in data["proxy"].items()} if use_proxy and tech == "Solar" else None
    return gapfill.fill(official, by_area, {area: window(data["da"][(area, tech)], lo, hi)}, window(data["da"][("DE", tech)], lo, hi),
                        t0 + Q, hi, cross=tech != "Load", proxy_by_area=proxy, floor=None if tech == "Load" else 0.0)


def rmse(xs: list[float]) -> float:
    return math.sqrt(statistics.fmean(x * x for x in xs)) if xs else float("nan")


def run(data: dict, start: datetime, end: datetime, step: int) -> list[str]:
    starts = []
    t = start + timedelta(hours=26)
    while t < end - timedelta(hours=24):
        starts.append(t)
        t += step * Q
    lines = [f"Gap starts: {len(starts)} per area and technology ({starts[0]:%d.%m.%Y} to {starts[-1]:%d.%m.%Y}, every {step * 15} min).",
             "RMSE in MW of the rebuilt German total, by hours the area has been silent. sd = standard deviation of the true",
             "German day-ahead error (the signal the tiles show). Solar: MTUs with more than 100 MW German solar only.", ""]
    head = "| hidden area | technology | method | " + " | ".join(f"{h} h" for h in GAPS_H) + " | sd |"
    lines += [head, "|---|---|---|" + "---:|" * (len(GAPS_H) + 1)]
    combined: dict[tuple[str, int], dict[datetime, dict[str, float]]] = {}
    for tech in TECHS:
        truth, da = data["act"][("DE", tech)], data["da"][("DE", tech)]
        day = (lambda t: data["act"][("DE", "Solar")].get(t, 0.0) > 100.0) if tech == "Solar" else (lambda t: True)
        signal = [truth[t] - da[t] for t in truth if t in da and day(t)]
        for area in AREAS:
            if not data["act"][(area, tech)]:
                continue
            for use_proxy in ((True, False) if tech == "Solar" and data["proxy"] else (False,)):
                errs: dict[int, list[float]] = {h: [] for h in GAPS_H}
                method = "?"
                for t0 in starts:
                    res = hide(data, tech, area, t0, use_proxy)
                    method = res["methods"].get(area, method)
                    for h in GAPS_H:
                        t = t0 + 4 * h * Q
                        if t in res["values"] and t in truth and day(t):
                            errs[h].append(res["values"][t] - truth[t])
                        if t in res["values"] and t in truth and (use_proxy or tech != "Solar" or not data["proxy"]):
                            combined.setdefault((area, h), {}).setdefault(t0, {})[tech] = res["values"][t] - truth[t]
                lines.append(f"| {area} | {tech} | {method} | " + " | ".join(f"{rmse(errs[h]):.0f}" for h in GAPS_H) + f" | {statistics.pstdev(signal):.0f} |")
    lines += ["", "All technologies of one area silent at once (what the tiles show): RMSE in MW.", "",
              "| hidden area | figure | " + " | ".join(f"{h} h" for h in GAPS_H) + " |", "|---|---|" + "---:|" * len(GAPS_H)]
    for area in AREAS:
        res_row, resid_row = [], []
        for h in GAPS_H:
            cells = combined.get((area, h), {}).values()
            res_techs = [tech for tech in ("Solar", "Wind Onshore", "Wind Offshore") if data["act"][(area, tech)]]
            res_err = [sum(c[tech] for tech in res_techs) for c in cells if all(tech in c for tech in res_techs)]
            resid = [c["Load"] - sum(c[tech] for tech in res_techs) for c in cells if "Load" in c and all(tech in c for tech in res_techs)]
            res_row.append(f"{rmse(res_err):.0f}")
            resid_row.append(f"{rmse(resid):.0f}")
        lines.append(f"| {area} | wind + solar | " + " | ".join(res_row) + " |")
        lines.append(f"| {area} | residual load | " + " | ".join(resid_row) + " |")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="first delivery day (Europe/Berlin)")
    ap.add_argument("--end", required=True, help="first day after the sample")
    ap.add_argument("--cache", help="directory for the raw answers")
    ap.add_argument("--step", type=int, default=4, help="quarter-hours between gap starts (default 4)")
    a = ap.parse_args()
    start = m.day_bounds(date.fromisoformat(a.start).isoformat())[0]
    end = m.day_bounds(date.fromisoformat(a.end).isoformat())[0]
    cache = Path(a.cache) if a.cache else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    data = load_data(start, end, cache)
    print("\n".join(run(data, start, end, a.step)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
