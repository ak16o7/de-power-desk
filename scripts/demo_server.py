"""Offline demo: runs the real app with a SYNTHETIC ENTSO-E upstream.

Every parser, aggregation and UI path runs exactly as in production; only
entsoe_request() is replaced by a generator of plausible XML. Numbers are
invented. Use it to review the UI without an API key:

    python scripts/demo_server.py --port 8000
"""
from __future__ import annotations

import argparse
import io
import math
import os
import random
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENTSOE_API_KEY", "demo-not-a-real-key")
# Synthetic upstream only: never mix in live netztransparenz data from a local .env.
os.environ["NTP_CLIENT_ID"] = ""
os.environ["NTP_CLIENT_SECRET"] = ""

from app import main as m  # noqa: E402

UTC = timezone.utc
FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _rng(*parts) -> random.Random:
    return random.Random("|".join(map(str, parts)))


def _grid(start: datetime, end: datetime):
    t = start
    while t < end:
        yield t
        t += timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=30)


def ts_doc(series: list[dict], start: datetime, end: datetime, root="GL_MarketDocument") -> bytes:
    body = []
    for s in series:
        pts = "".join(
            f"<Point><position>{i + 1}</position>{s.get('value_tag', '<quantity>{v}</quantity>').format(v=round(v, 3))}"
            f"{s.get('extra_point', '')}</Point>"
            for i, v in enumerate(s["values"]) if v is not None
        )
        if not pts:
            continue
        head = "".join(f"<{k}>{v}</{k}>" for k, v in s.get("head", {}).items())
        body.append(f"<TimeSeries>{head}<curveType>A01</curveType><Period><timeInterval><start>{start:%Y-%m-%dT%H:%MZ}</start>"
                     f"<end>{end:%Y-%m-%dT%H:%MZ}</end></timeInterval><resolution>PT15M</resolution>{pts}</Period></TimeSeries>")
    if not body:
        raise LookupError("No matching data found")
    return (f'<?xml version="1.0"?><{root} xmlns="urn:demo"><revisionNumber>1</revisionNumber>'
            f"<createdDateTime>{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}</createdDateTime>{''.join(body)}</{root}>").encode()


def solar_shape(t: datetime) -> float:
    h = t.astimezone(m.BERLIN).hour + t.astimezone(m.BERLIN).minute / 60
    return max(0.0, math.sin(math.pi * (h - 7) / 12)) ** 1.4 if 7 <= h <= 19 else 0.0


def res_values(psr: str, kind: str, start: datetime, end: datetime) -> list[float | None]:
    cut = _now()
    out = []
    for i, t in enumerate(_grid(start, end)):
        base = {"B16": 42000 * solar_shape(t), "B19": 9000 + 3500 * math.sin(i / 17), "B18": 2200 + 900 * math.sin(i / 23 + 1)}[psr]
        bias = {"A16": 1.0, "A01": 0.93 if psr == "B16" else 1.08, "A40": 0.97 if psr == "B16" else 1.04, "A18": 0.99}[kind]
        if kind == "A16" and t > cut:
            out.append(None)
            continue
        noise = _rng(psr, kind, i).gauss(0, 0.015 if kind == "A16" else 0.01)
        out.append(max(0.0, base * bias * (1 + noise)))
    return out


def load_values(kind: str, start: datetime, end: datetime):
    cut = _now()
    out = []
    for i, t in enumerate(_grid(start, end)):
        h = t.astimezone(m.BERLIN).hour
        base = 47000 + 11000 * math.sin(math.pi * (h - 5) / 16) if 5 <= h <= 21 else 45000
        if kind == "A16":
            out.append(None if t > cut else base * (1 + _rng("load", i).gauss(0.01, 0.01)))
        else:
            out.append(base)
    return out


BORDER_BASE = {"FR": 1800, "NL": -900, "BE": 200, "DK_1": -150, "DK_2": -550, "AT": 150, "CH": 900, "CZ": 1100, "PL": 900, "SE_4": 100, "NO_2": 0}


def flow_values(doc: str, contract: str | None, border: str, direction: str, start: datetime, end: datetime):
    cut = _now()
    out = []
    for i, t in enumerate(_grid(start, end)):
        net = BORDER_BASE[border] * (1 + 0.3 * math.sin(i / 12))
        if doc == "A11":
            if t > cut:
                out.append(None)
                continue
            net += {"PL": -900, "CZ": -250, "CH": 200}.get(border, 0) + _rng(border, i).gauss(0, 60)
        elif contract == "A05":
            net += 120 * math.sin(i / 7)
        if border == "NO_2":
            net = 0.0
        out.append(max(net, 0.0) if direction == "import" else max(-net, 0.0))
    return out


def outage_xml(doc_type, mrid, rev, created, business, prod_id, prod_name, gen_id, gen_name, psr, nominal, available, start, end):
    unit = (f"<production_RegisteredResource.pSRType.powerSystemResources.mRID>{gen_id}</production_RegisteredResource.pSRType.powerSystemResources.mRID>"
            f"<production_RegisteredResource.pSRType.powerSystemResources.name>{gen_name}</production_RegisteredResource.pSRType.powerSystemResources.name>") if gen_id else ""
    return f"""<?xml version="1.0"?><Unavailability_MarketDocument xmlns="urn:demo"><mRID>{mrid}</mRID><revisionNumber>{rev}</revisionNumber><type>{doc_type}</type>
<createdDateTime>{created:%Y-%m-%dT%H:%M:%SZ}</createdDateTime><TimeSeries><mRID>1</mRID><businessType>{business}</businessType>
<start_DateAndOrTime.date>{start:%Y-%m-%d}</start_DateAndOrTime.date><start_DateAndOrTime.time>{start:%H:%M:%S}Z</start_DateAndOrTime.time>
<end_DateAndOrTime.date>{end:%Y-%m-%d}</end_DateAndOrTime.date><end_DateAndOrTime.time>{end:%H:%M:%S}Z</end_DateAndOrTime.time>
<quantity_Measure_Unit.name>MAW</quantity_Measure_Unit.name><curveType>A03</curveType>
<production_RegisteredResource.mRID>{prod_id}</production_RegisteredResource.mRID><production_RegisteredResource.name>{prod_name}</production_RegisteredResource.name>
<production_RegisteredResource.pSRType.psrType>{psr}</production_RegisteredResource.pSRType.psrType>{unit}
<production_RegisteredResource.pSRType.powerSystemResources.nominalP unit="MAW">{nominal}</production_RegisteredResource.pSRType.powerSystemResources.nominalP>
<Available_Period><timeInterval><start>{start:%Y-%m-%dT%H:%MZ}</start><end>{end:%Y-%m-%dT%H:%MZ}</end></timeInterval><resolution>PT1M</resolution>
<Point><position>1</position><quantity>{available}</quantity></Point></Available_Period></TimeSeries></Unavailability_MarketDocument>""".encode()


PLANTS = {
    "DE_LU": [("Lippendorf R", "B02", 891), ("Knapsack 1", "B04", 784), ("Lünen 1", "B05", 746), ("Weiher C", "B05", 656),
              ("Herne 6", "B04", 608), ("Mittelsbüren GuD", "B04", 450), ("Hamm-Uentrop 10", "B04", 425), ("Emsland C", "B04", 417),
              ("Goldisthal A", "B10", 265), ("Huntorf GT", "B04", 321), ("Isar Pumpspeicher", "B10", 120), ("Datteln 4", "B05", 1052)],
    "FR": [("CHOOZ 2", "B14", 1500), ("CHOOZ 1", "B14", 1500), ("PALUEL 4", "B14", 1330), ("BELLEVILLE 2", "B14", 1310), ("CRUAS 4", "B14", 915)],
    "NL": [("Eemshaven 6", "B04", 360), ("Amer 9", "B05", 631), ("Hemweg 9", "B04", 440)],
    "BE": [("Tihange 3", "B14", 1030), ("Doel 4", "B14", 1026), ("Seraing GT3", "B04", 587)],
}


def outages(zone: str, doc_type: str, start: datetime, end: datetime) -> bytes:
    now = datetime.now(UTC)
    docs = []
    if doc_type == "A80":
        for i, (name, psr, nom) in enumerate(PLANTS.get(zone, [])):
            r = _rng(zone, name)
            s = start + timedelta(hours=r.randint(-400, 30))
            e = s + timedelta(hours=r.randint(20, 900))
            if e <= start:
                e = start + timedelta(hours=r.randint(30, 200))
            forced = r.random() < 0.3
            created = now - timedelta(hours=r.randint(1, 20)) if r.random() < 0.35 else s - timedelta(days=r.randint(1, 30))
            docs.append(outage_xml("A80", f"{zone}{i}", 1 if created < now - timedelta(days=1) else 2, created,
                                   "A54" if forced else "A53", f"P-{zone}-{i}", name, f"G-{zone}-{i}", name, psr, nom,
                                   0 if r.random() < 0.7 else int(nom * 0.4), s, e))
        if zone == "DE_LU":
            docs.append((FIX / "live_A80.xml").read_bytes())
    elif zone == "DE_LU":
        docs.append((FIX / "live_A77.xml").read_bytes().replace(b"2026-09-09", f"{start:%Y-%m-%d}".encode()).replace(b"2026-09-10", f"{end:%Y-%m-%d}".encode()))
        docs.append(outage_xml("A77", "wind77", 1, now - timedelta(hours=3), "A54", "WINDPARK-X", "Windpark Nordsee X", None, None,
                               "B18", 332, 0, start + timedelta(hours=10), end + timedelta(days=2)))
    if not docs:
        raise LookupError("No matching data found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, d in enumerate(docs):
            z.writestr(f"{i}.xml", d)
    return buf.getvalue()


def a24(process: str, area: str, start: datetime, end: datetime) -> bytes:
    if area == "AMPRION" or process not in ("A67", "A60", "A61"):
        raise LookupError("No matching data found")
    cut = _now()
    out = []
    for direction in ("A01", "A02"):
        pts = []
        for i, t in enumerate(_grid(start, end)):
            if t > cut:
                break
            base = 180 * math.sin(i / 9 + hash(area) % 5) if process == "A67" else 0.0
            val = max(base, 0) if direction == "A01" else max(-base, 0)
            pts.append(f"<Point><position>{i + 1}</position><quantity>900</quantity><secondaryQuantity>{val:.3f}</secondaryQuantity></Point>")
        out.append(f"<TimeSeries><mRID>{direction}</mRID><flowDirection.direction>{direction}</flowDirection.direction><quantity_Measure_Unit.name>MAW</quantity_Measure_Unit.name>"
                   f"<standard_MarketProduct.marketProductType>A01</standard_MarketProduct.marketProductType><curveType>A01</curveType><Period><timeInterval>"
                   f"<start>{start:%Y-%m-%dT%H:%MZ}</start><end>{end:%Y-%m-%dT%H:%MZ}</end></timeInterval><resolution>PT15M</resolution>{''.join(pts)}</Period></TimeSeries>")
    return (f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="urn:demo"><mRID>{area}{process}</mRID><revisionNumber>1</revisionNumber>'
            f"<createdDateTime>{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}</createdDateTime><process.processType>{process}</process.processType>"
            f"<area_Domain.mRID>{m.AREAS[area]}</area_Domain.mRID>{''.join(out)}</Balancing_MarketDocument>").encode()


def imbalance(doc: str, area_eic: str, start: datetime, end: datetime) -> bytes:
    cut = _now()
    areas = {m.AREAS[a]: a for a in m.BALANCING_AREAS}
    if doc == "A85":
        if area_eic != m.AREAS["DE"]:
            raise LookupError("No matching data found")
        vals = [None if t > cut else 95 + 140 * math.sin(i / 11) + _rng("p", i).gauss(0, 25) for i, t in enumerate(_grid(start, end))]
        series = [{"values": vals, "value_tag": "<imbalance_Price.amount>{v}</imbalance_Price.amount>",
                   "extra_point": f"<imbalance_Price.category>{c}</imbalance_Price.category>"} for c in ("A04", "A05")]
        return ts_doc(series, start, end)
    if area_eic not in areas:
        raise LookupError("No matching data found")
    share = {"50HERTZ": 0.2, "AMPRION": 0.35, "TENNET_DE": 0.3, "TRANSNETBW": 0.15}[areas[area_eic]]
    raw = [None if t > cut else share * (-110 * math.sin(i / 11) + _rng("v", i).gauss(0, 20)) for i, t in enumerate(_grid(start, end))]
    return ts_doc([{"head": {"flowDirection.direction": "A01"}, "values": [v if v is not None and v >= 0 else None for v in raw]},
                   {"head": {"flowDirection.direction": "A02"}, "values": [-v if v is not None and v < 0 else None for v in raw]}], start, end)


def fake_request(params: dict, start: datetime, end: datetime) -> bytes:
    doc = params["documentType"]
    if doc == "A75":
        return ts_doc([{"head": {"MktPSRType": f"<psrType>{p}</psrType>", "inBiddingZone_Domain.mRID": m.AREAS["DE"]},
                        "values": res_values(p, "A16", start, end)} for p in ("B16", "B18", "B19")], start, end)
    if doc == "A69":
        return ts_doc([{"head": {"MktPSRType": f"<psrType>{p}</psrType>"}, "values": res_values(p, params["processType"], start, end)}
                       for p in ("B16", "B18", "B19")], start, end)
    if doc == "A65":
        return ts_doc([{"values": load_values(params["processType"], start, end)}], start, end)
    if doc in ("A11", "A09"):
        inv = {v: k for k, v in m.AREAS.items()}
        src, dst = inv[params["out_Domain"]], inv[params["in_Domain"]]
        border, direction = (src, "import") if dst == "DE_LU" else (dst, "export")
        return ts_doc([{"values": flow_values(doc, params.get("contract_MarketAgreement.Type"), border, direction, start, end)}], start, end)
    if doc in ("A80", "A77"):
        if params.get("offset", 0):
            raise LookupError("No matching data found")
        inv = {v: k for k, v in m.AREAS.items()}
        return outages(inv[params["biddingZone_domain"]], doc, start, end)
    if doc == "A24":
        inv = {v: k for k, v in m.AREAS.items()}
        return a24(params["processType"], inv[params["area_Domain"]], start, end)
    if doc in ("A85", "A86"):
        return imbalance(doc, params["controlArea_Domain"], start, end)
    raise LookupError("No matching data found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    m.entsoe_request = fake_request
    if os.getenv("DEMO_NTP"):
        from app import ntp

        def fake_block(start, end):
            cut = _now()
            ts = [t for t in _grid(start, end) if t <= cut]
            nrv = {t: 110 * 4 * math.sin(i / 11) for i, t in enumerate(ts)}
            return {"status": {"nrv": "ok", "afrr": "ok", "mfrr": "ok"}, "diag": {"nrv": {"variant": "demo"}},
                    "nrv": nrv, "afrr_up": {t: max(v, 0) * 0.8 for t, v in nrv.items()},
                    "afrr_down": {t: max(-v, 0) * 0.8 for t, v in nrv.items()},
                    "mfrr_up": {t: 0.0 for t in ts}, "mfrr_down": {t: 0.0 for t in ts}}
        ntp.configured = lambda: True
        ntp.fetch_block = fake_block
    import uvicorn
    uvicorn.run(m.app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
