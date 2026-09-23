"""Check netztransparenz.de credentials and parsing end to end.

    NTP_CLIENT_ID=... NTP_CLIENT_SECRET=... python scripts/ntp_check.py [--day 2026-09-22]

Or put both values in .env. Prints token status, which URL form works, the
CSV header and the parsed Germany values. Never prints the secret.
"""
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from app import ntp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--day", default=(date.today() - timedelta(days=1)).isoformat())
a = ap.parse_args()
if not ntp.configured():
    raise SystemExit("NTP_CLIENT_ID / NTP_CLIENT_SECRET not set")

berlin = ZoneInfo("Europe/Berlin")
d = date.fromisoformat(a.day)
start = datetime(d.year, d.month, d.day, tzinfo=berlin)
end = start + timedelta(days=1)

import requests  # noqa: E402

s = requests.Session()
try:
    ntp._get_token(s)
    print("token: OK")
except Exception as e:
    raise SystemExit(f"token: FAILED - {e}")
r = s.get("https://ds.netztransparenz.de/api/v1/health", headers={"Authorization": f"Bearer {ntp._token['value']}"}, timeout=30)
print("health:", r.status_code, r.text[:120].replace("\n", " "))

block = ntp.fetch_block(start, end)
ok = True
for name in ("nrv", "afrr", "mfrr"):
    print(f"\n[{name}] status={block['status'][name]} diag={block['diag'][name]}")
    ok &= block["status"][name] == "ok"
for key in ("nrv", "afrr_up", "afrr_down", "mfrr_up", "mfrr_down"):
    series = block[key]
    if series:
        first = min(series)
        vals = list(series.values())
        print(f"{key:10s} points={len(series):3d} first={first.astimezone(berlin):%H:%M} value={series[first]:.1f} "
              f"min={min(vals):.1f} max={max(vals):.1f}")
    else:
        print(f"{key:10s} no data")
print("\nRESULT:", "OK" if ok else "CHECK THE LINES ABOVE")
sys.exit(0 if ok else 1)
