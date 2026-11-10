"""v5.9: one late control area must not freeze the German totals, and stale
official actuals must not look "complete" (10.11.2026: Amprion silent since
09:45, tiles 287 min old, status bar green)."""
import math
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import gapfill
from app import main as m
from app import ntp

Q = timedelta(minutes=15)
T0 = datetime(2026, 11, 10, 8, 30, tzinfo=m.UTC)   # last MTU the late area delivered
AREAS = ("50HERTZ", "AMPRION", "TENNET_DE", "TRANSNETBW")
ROOT = Path(__file__).resolve().parents[1]


def flat(start, n, value):
    return {start + i * Q: value for i in range(n)}


def scene(late="AMPRION", gap=8):
    """Four areas at 100/200/300/400 MW; every forecast is 95 % of the actual,
    so all areas share one relative error and the bridge is exact. `late`
    stops delivering at T0."""
    start = T0 - 20 * Q
    level = dict(zip(AREAS, (100.0, 200.0, 300.0, 400.0)))
    by_area = {a: flat(start, 21 + gap, v) for a, v in level.items()}
    by_area[late] = {t: v for t, v in by_area[late].items() if t <= T0}
    forecast = {a: flat(start, 21 + gap, v * 0.95) for a, v in level.items()}
    official = flat(start, 21, 1000.0)
    total_fc = flat(start, 21 + gap, 950.0)
    return official, by_area, forecast, total_fc


class AnchoredTests(unittest.TestCase):
    def test_last_error_fades_into_the_error_of_the_other_areas(self):
        # Own error at the anchor +40 MW; the others run 50 % above forecast.
        t = T0 + 32 * Q
        est = gapfill.anchored(t, 200.0, (T0, 40.0), others_actual=900.0, others_forecast=600.0)
        w = 2.718281828459045 ** -1  # 32 quarter-hours = one TAU
        self.assertAlmostEqual(est, 200.0 + w * 40.0 + (1 - w) * 300.0 * (200.0 / 600.0), places=6)

    def test_one_quarter_hour_after_the_gap_it_is_almost_the_last_error(self):
        est = gapfill.anchored(T0 + Q, 200.0, (T0, 40.0), others_actual=600.0, others_forecast=600.0)
        self.assertAlmostEqual(est, 200.0 + 40.0 * 2.718281828459045 ** (-1 / 32), places=6)

    def test_no_forecast_or_no_information_means_no_estimate(self):
        self.assertIsNone(gapfill.anchored(T0 + Q, None, (T0, 40.0)))
        self.assertIsNone(gapfill.anchored(T0 + Q, 200.0, None))            # would just repeat the forecast
        self.assertIsNone(gapfill.anchored(T0 + 97 * Q, 200.0, (T0, 40.0)))  # silent for more than 24 h

    def test_tiny_forecast_of_the_others_cannot_scale_an_error(self):
        est = gapfill.anchored(T0 + Q, 5.0, (T0, 0.0), others_actual=40.0, others_forecast=10.0)
        self.assertEqual(est, 5.0)


class FillTests(unittest.TestCase):
    def test_one_late_area_is_estimated_and_named(self):
        official, by_area, forecast, total_fc = scene()
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, T0 - 20 * Q, T0 + 9 * Q)
        self.assertTrue(res["verified"])
        self.assertEqual(sorted(res["values"]), [T0 + i * Q for i in range(1, 9)])
        self.assertEqual(set(res["missing"].values()), {"AMPRION"})
        self.assertEqual(res["methods"], {"AMPRION": "anchored"})
        for t, v in res["values"].items():
            self.assertAlmostEqual(v, 1000.0, places=6)
            self.assertAlmostEqual(res["estimated"][t], 200.0, places=6)

    def test_all_areas_present_is_a_plain_sum_not_an_estimate(self):
        official, by_area, forecast, total_fc = scene(gap=4)
        by_area["AMPRION"].update(flat(T0 + Q, 4, 200.0))   # delivered, total not published yet
        res = gapfill.fill(official, by_area, {}, total_fc, T0 - 20 * Q, T0 + 5 * Q)
        self.assertEqual(len(res["values"]), 4)
        self.assertEqual(res["estimated"], {})
        self.assertEqual(res["missing"], {})

    def test_two_late_areas_are_never_estimated(self):
        official, by_area, forecast, total_fc = scene()
        by_area["TENNET_DE"] = {t: v for t, v in by_area["TENNET_DE"].items() if t <= T0}
        res = gapfill.fill(official, by_area, forecast, total_fc, T0 - 20 * Q, T0 + 9 * Q)
        self.assertEqual(res["values"], {})

    def test_official_values_are_never_replaced(self):
        official, by_area, forecast, total_fc = scene()
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, T0 - 20 * Q, T0 + 9 * Q)
        self.assertFalse(set(res["values"]) & set(official))

    def test_an_area_silent_for_longer_than_the_window_blocks_the_fill(self):
        # Amprion absent from the whole window: the other three sum to 800,
        # the official total says 1000 -> must not be passed off as Germany.
        official, by_area, forecast, total_fc = scene()
        by_area["AMPRION"] = {}
        res = gapfill.fill(official, by_area, forecast, total_fc, T0 - 20 * Q, T0 + 9 * Q)
        self.assertFalse(res["verified"])
        self.assertEqual(res["values"], {})

    def test_without_any_official_value_nothing_is_verified(self):
        _, by_area, forecast, total_fc = scene()
        self.assertEqual(gapfill.fill({}, by_area, forecast, total_fc, T0 - 20 * Q, T0 + 9 * Q)["values"], {})

    def test_load_uses_no_cross_term_and_may_not_be_floored(self):
        official, by_area, forecast, total_fc = scene()
        for a in ("50HERTZ", "TENNET_DE", "TRANSNETBW"):   # the others jump; load errors do not transfer
            by_area[a].update({t: v + 500.0 for t, v in by_area[a].items() if t > T0})
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, T0 - 20 * Q, T0 + 9 * Q, cross=False, floor=None)
        for t, est in res["estimated"].items():   # forecast 190 + own error 10, fading; the others' +500 is ignored
            self.assertAlmostEqual(est, 190.0 + 10.0 * math.exp(-((t - T0) / Q) / 32), places=6)

    def test_generation_is_never_negative(self):
        official, by_area, forecast, total_fc = scene()
        by_area["AMPRION"][T0] = 0.0                      # own error -190 at the anchor
        forecast["AMPRION"] = {t: (190.0 if t <= T0 else 20.0) for t in forecast["AMPRION"]}
        official[T0] = 800.0
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, T0 - 20 * Q, T0 + 9 * Q)
        self.assertTrue(all(v >= 0.0 for v in res["estimated"].values()))

    def test_solar_proxy_scales_with_the_ratio_before_the_gap(self):
        official, by_area, forecast, total_fc = scene()
        start = T0 - 20 * Q
        proxy = {"AMPRION": {**flat(start, 21, 125.0), **flat(T0 + Q, 6, 150.0)}}   # 200 / 125 = 1.6 before the gap
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, start, T0 + 9 * Q, proxy_by_area=proxy)
        self.assertEqual(res["methods"], {"AMPRION": "scaled"})
        self.assertEqual(sorted(res["values"]), [T0 + i * Q for i in range(1, 7)])   # ends where the proxy ends
        for est in res["estimated"].values():
            self.assertAlmostEqual(est, 240.0, places=6)

    def test_proxy_without_enough_common_daylight_falls_back(self):
        official, by_area, forecast, total_fc = scene()
        proxy = {"AMPRION": flat(T0 - 3 * Q, 12, 125.0)}   # only 4 common MTUs
        res = gapfill.fill(official, by_area, {"AMPRION": forecast["AMPRION"]}, total_fc, T0 - 20 * Q, T0 + 9 * Q, proxy_by_area=proxy)
        self.assertEqual(res["methods"], {"AMPRION": "anchored"})

    def test_lagging_areas(self):
        through = {"50HERTZ": T0 + 8 * Q, "AMPRION": T0, "TENNET_DE": T0 + 7 * Q, "TRANSNETBW": T0 + 8 * Q}
        self.assertEqual(gapfill.lagging_areas(through), {"AMPRION": T0})
        self.assertEqual(gapfill.lagging_areas({a: T0 for a in AREAS}), {})


class HochrechnungTests(unittest.TestCase):
    CSV = ('Datum;von;Zeitzone von;bis;Zeitzone bis;50Hertz (MW);Amprion (MW);TenneT TSO (MW);TransnetBW (MW)\n'
           '2026-11-10;08:30;UTC;08:45;UTC;359,920;1221,658;970,147;263,350\n'
           '2026-11-10;08:45;UTC;09:00;UTC;N.A.;N.A.;N.A.;N.A.\n')

    def test_second_csv_layout_is_parsed(self):
        with patch.object(ntp, '_get', return_value=self.CSV):
            d = ntp.fetch_hochrechnung('Solar', T0 - Q, T0 + 4 * Q)
        self.assertEqual(d['AMPRION'], {T0: 1221.658})   # N.A. dropped, thousands dot handled
        self.assertEqual(set(d), set(AREAS))

    def test_old_layout_still_parses(self):
        rows = ntp.parse_csv('Datum;Zeitzone;von;bis;Deutschland\n28.09.2026;UTC;17:00;17:15;1,5\n')
        self.assertEqual(rows[0]['ts'], datetime(2026, 9, 28, 17, 0, tzinfo=m.UTC))


def xml_points(start, values, psr=None, forecast=False, load=False):
    points = ''.join(f'<Point><position>{i + 1}</position><quantity>{v}</quantity></Point>' for i, v in enumerate(values))
    domain = ('<outBiddingZone_Domain.mRID>X</outBiddingZone_Domain.mRID>' if load and not forecast else
              '<inBiddingZone_Domain.mRID>X</inBiddingZone_Domain.mRID>')
    psr_xml = f'<MktPSRType><psrType>{psr}</psrType></MktPSRType>' if psr else ''
    end = start + len(values) * Q
    return (f'<TimeSeries>{domain}{psr_xml}<Period><timeInterval><start>{start:%Y-%m-%dT%H:%MZ}</start><end>{end:%Y-%m-%dT%H:%MZ}</end>'
            f'</timeInterval><resolution>PT15M</resolution>{points}</Period></TimeSeries>')


def doc(*series):
    return f'<GL_MarketDocument><createdDateTime>2026-11-10T13:00:00Z</createdDateTime>{"".join(series)}</GL_MarketDocument>'.encode()


class PanelTests(unittest.TestCase):
    """fetch_renewables / fetch_load end to end on a fake ENTSO-E."""
    DAY = datetime(2026, 11, 9, 23, 0, tzinfo=m.UTC)      # 10.11.2026 00:00 Berlin
    NOW = datetime(2026, 11, 10, 13, 40, tzinfo=m.UTC)    # 14:40 Berlin
    SHARE = dict(zip(AREAS, (0.2, 0.3, 0.4, 0.1)))

    def setUp(self):
        m.CACHE.clear()
        self.calls = []

    def n(self, area):
        """Quarter-hours delivered today: Amprion until 08:30 UTC (39), the others until 13:00 (57)."""
        return 39 if area in ('AMPRION', 'DE') else 57

    def fake(self, params, start, end):
        self.calls.append(dict(params))
        eic = params.get('in_Domain') or params.get('outBiddingZone_Domain')
        area = next(a for a, e in m.AREAS.items() if e == eic)
        share = 1.0 if area == 'DE' else self.SHARE[area]
        two_days = start < self.DAY
        first = self.DAY - timedelta(days=1) if two_days else self.DAY
        offset = 96 if two_days else 0
        doc_type, process = params['documentType'], params.get('processType')
        if doc_type == 'A75':
            n = offset + self.n(area) if end > self.DAY else 96
            parts = [xml_points(first, [1000 * share] * n, 'B16'), xml_points(first, [2000 * share] * n, 'B19')]
            if area in ('DE', '50HERTZ', 'TENNET_DE'):
                parts.append(xml_points(first, [500 * (1.0 if area == 'DE' else 0.5)] * (offset + 57 if end > self.DAY else 96), 'B18'))
            return doc(*parts)
        if doc_type == 'A69':
            if process != 'A01':
                raise LookupError('No matching data found')
            n = offset + 96
            return doc(xml_points(first, [900 * share] * n, 'B16'), xml_points(first, [2100 * share] * n, 'B19'),
                       xml_points(first, [450] * n, 'B18'))
        if doc_type == 'A65':
            if process == 'A01':
                return doc(xml_points(first, [50000 * share] * (offset + 96), load=True, forecast=True))
            n = offset + self.n(area) if end > self.DAY else 96
            return doc(xml_points(first, [52000 * share] * n, load=True))
        raise AssertionError(params)

    def run_panels(self, gapfill_on=True, configured=False):
        real_now = datetime

        class FakeDT(real_now):
            @classmethod
            def now(cls, tz=None):
                return PanelTests.NOW.astimezone(tz) if tz else PanelTests.NOW.replace(tzinfo=None)

        with patch.object(m, 'entsoe_request', side_effect=self.fake), patch.object(m, 'datetime', FakeDT), \
                patch.object(m, 'API_KEY', 'x'), patch.object(m, 'GAPFILL', gapfill_on), \
                patch.object(ntp, 'configured', return_value=configured):
            ren = m.panel_response('renewables', lambda: m.fetch_renewables(None, True))
            load = m.panel_response('load', lambda: m.fetch_load(None, True))
            stale = (m.staleness('renewables', ren), m.staleness('load', load))
        return ren, load, stale

    def test_late_area_is_bridged_and_official_series_stay_official(self):
        ren, load, _ = self.run_panels()
        s = ren['series']
        self.assertEqual(len(s['Solar Actual']), 39)                 # official: unchanged
        self.assertEqual(len(s['Solar Actual Fill']), 18)            # 08:45 .. 13:00 UTC
        self.assertEqual(len(s['RES Actual']), 39)
        self.assertEqual(len(s['RES Actual Fill']), 18)
        self.assertEqual(s['Wind Offshore Actual Fill'], [])         # offshore was never late
        g = ren['gapfill']
        self.assertEqual(g['state'], 'filled')
        self.assertEqual(g['estimated_areas'], ['AMPRION'])
        self.assertEqual(g['lagging'], {'AMPRION': '2026-11-10T08:30:00+00:00'})
        self.assertEqual(g['methods'], {'Solar': 'anchored', 'Wind Onshore': 'anchored'})
        # Constant world: every area keeps its error, so the bridge is exact.
        self.assertAlmostEqual(s['RES Actual Fill'][-1]['v'], 3500.0, delta=1.0)
        k = ren['kpi']
        self.assertTrue(k['estimated'])
        self.assertEqual(k['as_of'], '2026-11-10T13:00:00+00:00')
        self.assertEqual(k['official_as_of'], '2026-11-10T08:30:00+00:00')
        self.assertAlmostEqual(k['res_error_mw'], 3500.0 - 3450.0, delta=1.0)
        # Load: the same late area, the residual load is bridged from both.
        self.assertEqual(len(load['series']['Load Actual']), 39)
        self.assertEqual(len(load['series']['Load Actual Fill']), 18)
        self.assertEqual(len(load['series']['Residual Load Actual Fill']), 18)
        # Load has no cross term: Amprion's +600 MW error fades towards its forecast (18 MTUs after the gap).
        amprion = 15000.0 + 600.0 * math.exp(-18 / 32)
        self.assertAlmostEqual(load['series']['Load Actual Fill'][-1]['v'], 52000.0 * 0.7 + amprion, delta=0.01)
        self.assertAlmostEqual(load['series']['Residual Load Actual Fill'][-1]['v'], 52000.0 * 0.7 + amprion - 3500.0, delta=0.01)
        self.assertTrue(load['kpi']['estimated'])
        self.assertEqual(load['gapfill']['estimated_areas'], ['AMPRION'])

    def test_stale_official_data_is_reported_even_when_bridged(self):
        ren, load, (stale_ren, stale_load) = self.run_panels()
        self.assertEqual(ren['quality']['state'], 'complete')        # the series exist ...
        self.assertTrue(stale_ren['stale'])                          # ... and are 295 min old
        self.assertEqual(stale_ren['official_age_min'], 295)
        self.assertTrue(stale_load['stale'])
        self.assertEqual(ren['freshness']['actual_through'], '2026-11-10T08:30:00+00:00')
        self.assertEqual(ren['freshness']['filled_through'], '2026-11-10T13:00:00+00:00')

    def test_switch_off_restores_the_previous_behaviour(self):
        ren, load, (stale_ren, _) = self.run_panels(gapfill_on=False)
        self.assertEqual(ren['gapfill'], {'state': 'off'})
        self.assertEqual(ren['series']['RES Actual Fill'], [])
        self.assertFalse(ren['kpi']['estimated'])
        self.assertEqual(ren['kpi']['as_of'], '2026-11-10T08:30:00+00:00')
        self.assertTrue(stale_ren['stale'])                          # the warning does not depend on the bridge
        self.assertFalse(any(p.get('in_Domain') == m.AREAS['AMPRION'] for p in self.calls))

    def test_fresh_totals_cost_no_extra_requests(self):
        self.n = lambda area: 57                                     # nobody is late
        ren, load, (stale_ren, _) = self.run_panels()
        self.assertEqual(ren['gapfill'], {'state': 'not_needed'})
        self.assertFalse(stale_ren['stale'])
        self.assertFalse(ren['kpi']['estimated'])
        self.assertEqual({p.get('in_Domain') or p.get('outBiddingZone_Domain') for p in self.calls}, {m.AREAS['DE']})

    def test_a_failed_area_request_never_looks_like_a_late_area(self):
        fake = self.fake

        def flaky(params, start, end):
            if params.get('in_Domain') == m.AREAS['TENNET_DE']:
                raise RuntimeError('ENTSO-E Netzwerkfehler: ReadTimeout')
            return fake(params, start, end)
        self.fake = flaky
        ren, _, _ = self.run_panels()
        self.assertEqual(ren['gapfill'], {'state': 'error'})
        self.assertEqual(ren['series']['RES Actual Fill'], [])
        self.assertEqual(len(ren['series']['RES Actual']), 39)       # official view untouched

    def test_a_failed_bridge_reuses_the_last_good_one_and_official_values_win(self):
        ren, _, _ = self.run_panels()
        self.assertEqual(len(ren['series']['RES Actual Fill']), 18)
        # Next refresh: ENTSO-E fails for one area, and Amprion has delivered 4 more MTUs meanwhile.
        fake, n = self.fake, self.n

        def flaky(params, start, end):
            if params.get('in_Domain') == m.AREAS['TENNET_DE']:
                raise RuntimeError('ENTSO-E Netzwerkfehler: ReadTimeout')
            return fake(params, start, end)
        self.fake = flaky
        self.n = lambda area: 43 if area in ('AMPRION', 'DE') else n(area)
        ren2, _, _ = self.run_panels()   # forced rebuild; the remembered bridge is still in the cache
        self.assertEqual(ren2['gapfill']['state'], 'filled')
        self.assertEqual(len(ren2['series']['RES Actual']), 43)
        self.assertEqual(len(ren2['series']['RES Actual Fill']), 14)     # the 4 now-official MTUs are not shown twice
        self.assertFalse({p['t'] for p in ren2['series']['RES Actual']} & {p['t'] for p in ren2['series']['RES Actual Fill']})

    def test_past_days_are_never_bridged_or_stale(self):
        self.assertFalse(m.behind({}, self.DAY - timedelta(days=1), (self.NOW - timedelta(days=1)).date(), self.NOW.astimezone(m.BERLIN)))
        self.assertEqual(m.staleness('renewables', {'date': '2026-11-09', 'freshness': {'actual_through': None}}, self.NOW.astimezone(m.BERLIN)), {})

    def test_staleness_without_any_value_counts_from_midnight(self):
        st = m.staleness('renewables', {'date': '2026-11-10', 'freshness': {'actual_through': None}}, self.NOW.astimezone(m.BERLIN))
        self.assertEqual(st['official_age_min'], 880)
        self.assertTrue(st['stale'])
        early = datetime(2026, 11, 9, 23, 40, tzinfo=m.UTC).astimezone(m.BERLIN)   # 00:40: nothing can be there yet
        self.assertFalse(m.staleness('renewables', {'date': '2026-11-10', 'freshness': {'actual_through': None}}, early)['stale'])


class FrontEndTests(unittest.TestCase):
    def setUp(self):
        self.js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
        self.html = (ROOT / 'app/templates/index.html').read_text(encoding='utf-8')

    def test_status_bar_knows_stale(self):
        self.assertIn("freshness?.stale", self.js)
        self.assertIn("else if (stale.length || partial.length) setStatus('warn', stamp, warnText, warnInfo)", self.js)
        # Stale must come before the outage panel's permanent "incomplete".
        self.assertLess(self.js.index("stale.length ? staleText()"), self.js.index("partial.length ? tr('incompleteIn'"))

    def test_bridged_values_are_marked_everywhere(self):
        for needle in ("'Actual Fill'", "'RES Forecast Error Fill'", "'Residual Load Surprise Fill'", 'k.estimated', 'gapChip(g)'):
            self.assertIn(needle, self.js)
        for needle in ('id="srcRes"', 'id="srcLoad"'):
            self.assertIn(needle, self.html)

    def test_new_texts_exist_in_both_languages(self):
        for key in ('staleIn', 'lagSince', 'lagInfo', 'estOf', 'areaSum', 'actualPrelim', 'prelimTo', 'bordersLag'):
            self.assertRegex(self.js, rf"{key}: \['[^']+',\s*'[^']+'\]")


if __name__ == '__main__':
    unittest.main()
