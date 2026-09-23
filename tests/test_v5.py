"""v5 regression tests: fixed DA basis, A77 plant-only supplement, 24h outage
change, total schedules / intraday cross-border, zero-flow flags, single reBAP,
partial aFRR labelling and the optional netztransparenz.de source."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as m
from app import ntp

FIX = Path(__file__).parent / 'fixtures'
BERLIN = m.BERLIN


def flow_xml(value: float, day='2026-09-10', points=96) -> bytes:
    start = datetime.fromisoformat(day).replace(tzinfo=BERLIN).astimezone(m.UTC)
    end = start + timedelta(minutes=15 * points)
    pts = ''.join(f'<Point><position>{i+1}</position><quantity>{value}</quantity></Point>' for i in range(points))
    return (f'<?xml version="1.0"?><Publication_MarketDocument xmlns="urn:t"><TimeSeries><curveType>A01</curveType><Period><timeInterval>'
            f'<start>{start:%Y-%m-%dT%H:%MZ}</start><end>{end:%Y-%m-%dT%H:%MZ}</end></timeInterval><resolution>PT15M</resolution>'
            f'{pts}</Period></TimeSeries></Publication_MarketDocument>').encode()


def outage_row(**kw):
    start = datetime(2026, 9, 9, 0, 0, tzinfo=BERLIN).astimezone(m.UTC)
    base = dict(zone='DE_LU', document_type='A80', doc_mrid='d1', ts_mrid='1', resource_id='u1', production_id='p1',
                start=start, end=start + timedelta(days=3), event_start=start, event_end=start + timedelta(days=3),
                plant='Unit', psr='B04', business='A53', revision='1', created='2026-09-01T10:00Z', docstatus='A05',
                unavailable=100.0, available=0.0, nominal=100.0, reason_code=None, reason_text=None, location=None)
    base.update(kw)
    return base


class OutageV5Tests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def test_live_a77_plant_only_is_added_next_to_a80(self):
        a80 = m._parse_outage_docs((FIX / 'live_A80.xml').read_bytes(), 'DE_LU', 'A80')
        a77 = m._parse_outage_docs((FIX / 'live_A77.xml').read_bytes(), 'DE_LU', 'A77')
        rows, label, complete, added = m.select_outage_rows({'A80': a80, 'A77': a77}, {'A80': 'ok', 'A77': 'ok'})
        self.assertEqual(label, 'A80 + A77 plant-only')
        self.assertEqual(added, 1)
        self.assertTrue(complete)
        self.assertIn('Global Tech I', {r['plant'] for r in rows})

    def test_a77_for_plant_with_unit_notice_is_not_double_counted(self):
        a80 = [outage_row()]
        a77 = [outage_row(document_type='A77', doc_mrid='x', resource_id='p1', production_id='p1', unavailable=300.0)]
        rows, label, _, added = m.select_outage_rows({'A80': a80, 'A77': a77}, {'A80': 'ok', 'A77': 'ok'})
        self.assertEqual((label, added, len(rows)), ('A80', 0, 1))

    def test_a77_failure_marks_zone_incomplete_but_keeps_numbers(self):
        rows, label, complete, _ = m.select_outage_rows({'A80': [outage_row()], 'A77': []}, {'A80': 'ok', 'A77': 'error'})
        self.assertFalse(complete)
        self.assertEqual(len(rows), 1)

    def test_zone_kpis_forced_and_24h_change(self):
        day0 = datetime(2026, 9, 10, 0, 0, tzinfo=BERLIN).astimezone(m.UTC)
        rows = [
            outage_row(),  # planned 100 MW, whole window
            outage_row(doc_mrid='d2', resource_id='u2', production_id='p2', business='A54', unavailable=400.0,
                       start=day0 + timedelta(hours=6), event_start=day0 + timedelta(hours=6),
                       created='2026-09-10T05:00Z', plant='Forced Unit'),
        ]
        with patch.object(m, '_fetch_outage_pages', side_effect=lambda z, d, *a: (rows, 'ok', 1) if (z, d) == ('DE_LU', 'A80') else ([], 'no_data', 0)):
            data = m.fetch_outages('2026-09-10', True)
        de = data['zones']['DE_LU']
        self.assertEqual(de['unavailable_mw'], 500.0)
        self.assertEqual(de['forced_mw'], 400.0)
        self.assertEqual(de['delta_24h_mw'], 400.0)
        self.assertEqual(de['forced_delta_24h_mw'], 400.0)
        self.assertEqual(data['kpi']['de_unavailable_mw'], 500.0)
        self.assertEqual([n['plant'] for n in data['recent']], ['Forced Unit'])
        self.assertEqual(data['kpi']['recent_forced_notices'], 1)
        self.assertEqual(data['notices'][0]['fuel'], 'Gas')

    def test_query_window_includes_previous_day(self):
        seen = []
        def pages(z, d, start, end, *a):
            seen.append(end - start)
            return [], 'no_data', 0
        with patch.object(m, '_fetch_outage_pages', side_effect=pages):
            m.fetch_outages('2026-09-10', True)
        self.assertTrue(all(delta == timedelta(days=2) for delta in seen))


class BordersV5Tests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def test_total_schedule_intraday_and_unscheduled(self):
        values = {('A11', 'import'): 1000, ('A11', 'export'): 200, ('A01', 'import'): 700, ('A01', 'export'): 100,
                  ('A05', 'import'): 900, ('A05', 'export'): 100}

        def fake(params, start, end):
            key = params.get('contract_MarketAgreement.Type', 'A11')
            direction = 'import' if params['in_Domain'] == m.AREAS['DE_LU'] else 'export'
            return flow_xml(values[(key, direction)])

        with patch.object(m, 'entsoe_request', side_effect=fake):
            d = m.fetch_borders('2026-09-10', ['FR'], True)
        k = d['kpi']
        self.assertEqual(k['net_import_mw'], 800.0)
        self.assertEqual(k['da_schedule_mw'], 600.0)
        self.assertEqual(k['total_schedule_mw'], 800.0)
        self.assertEqual(k['intraday_xb_mw'], 200.0)
        self.assertEqual(k['unscheduled_mw'], 0.0)
        self.assertEqual(d['table'][0]['intraday'], 200.0)
        self.assertTrue(d['coverage']['total_schedule_complete'])

    def test_zero_flow_link_is_flagged(self):
        with patch.object(m, 'entsoe_request', side_effect=lambda *a: flow_xml(0)):
            d = m.fetch_borders('2026-09-10', ['NO_2'], True)
        self.assertEqual(d['borders']['NO_2']['flag'], 'zero_flow_all_day')
        self.assertEqual(d['flags'], [{'border': 'NO_2', 'flag': 'zero_flow_all_day'}])


class BalancingV5Tests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def _run(self, bids, ntp_block=None):
        t0 = datetime(2026, 9, 10, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)
        price = [{'ts': t0, 'value': 90.0, 'category': c, 'source_area': 'DE', 'revision': '1', 'created': None,
                  'business': None, 'direction': None} for c in ('A04', 'A05')]
        volume = [{'ts': t0, 'value': 50.0, 'business': None, 'direction': 'A02', 'category': None, 'source_area': 'DE',
                   'revision': '1', 'created': None}]
        def fake_doc(doc, start, end):
            return (price, {'DE': 'ok'}, 'DE') if doc == 'A85' else (volume, {'DE': 'ok'}, 'DE')
        patches = [patch.object(m, '_query_aggregated_bids', side_effect=bids),
                   patch.object(m, '_query_balancing_doc_with_fallback', side_effect=fake_doc),
                   patch.object(ntp, 'configured', return_value=ntp_block is not None)]
        if ntp_block is not None:
            patches.append(patch.object(ntp, 'fetch_block', return_value=ntp_block))
        for p in patches:
            p.start()
        try:
            return m.fetch_balancing('2026-09-10', True), t0
        finally:
            for p in patches:
                p.stop()

    @staticmethod
    def three_areas(process, start, end, area):
        t0 = datetime(2026, 9, 10, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)
        if area == 'AMPRION' or process not in ('A67', 'A60'):
            return [], 'no_data'
        up = dict(ts=t0, process=process, activated=100.0 if process == 'A67' else 0.0, offered=1, source_area=area,
                  product='A01', direction='A01', mrid='1', revision='1', created='2026-09-10T12:00Z', doc_mrid=area)
        return [up, {**up, 'direction': 'A02', 'activated': 10.0 if process == 'A67' else 0.0}], 'ok'

    def test_identical_long_short_become_single_rebap_and_sign(self):
        d, _ = self._run(self.three_areas)
        self.assertEqual(d['kpi']['price_mode'], 'single')
        self.assertEqual(d['kpi']['imbalance_price_eur_mwh'], 90.0)
        self.assertEqual(d['kpi']['imbalance_state'], 'deficit')
        self.assertEqual(d['kpi']['imbalance_avg_mw'], -200.0)

    def test_missing_amprion_gives_explicit_partial_not_germany_total(self):
        d, _ = self._run(self.three_areas)
        self.assertIsNone(d['kpi']['afrr_net_mw'])
        self.assertEqual(d['kpi']['afrr_partial_mw'], 270.0)
        self.assertEqual(d['kpi']['afrr_missing_areas'], ['AMPRION'])
        self.assertEqual(d['kpi']['activation_source'], 'ENTSO-E A24 partial')
        self.assertEqual(d['sources']['NTP']['state'], 'not_configured')

    def test_netztransparenz_block_data_wins_when_configured(self):
        t0 = datetime(2026, 9, 10, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)
        block = {'status': {'nrv': 'ok', 'afrr': 'ok', 'mfrr': 'ok'}, 'nrv': {t0: 420.0},
                 'afrr_up': {t0: 500.0}, 'afrr_down': {t0: 80.0}, 'mfrr_up': {t0: 0.0}, 'mfrr_down': {t0: 0.0}}
        d, _ = self._run(self.three_areas, block)
        self.assertEqual(d['kpi']['nrv_saldo_mw'], 420.0)
        self.assertEqual(d['kpi']['nrv_state'], 'deficit')
        self.assertEqual(d['kpi']['afrr_de_mw'], 420.0)
        self.assertIn('netztransparenz', d['kpi']['activation_source'])
        self.assertEqual(d['sources']['NTP']['state'], 'ok')


class NtpParserTests(unittest.TestCase):
    def test_csv_comma_decimals_timezone_and_columns(self):
        csv = ('Datum;Zeitzone;von;bis;Datenkategorie;Datentyp;Einheit;Deutschland;AEP Knappheitskomponente\n'
               '10.06.2023;UTC;13:00;13:15;NRV-Saldo;Betrieblich;MW;561,721;0\n'
               '10.06.2023;MESZ;15:15;15:30;NRV-Saldo;Betrieblich;MW;-1.234,5;0\n'
               '10.06.2023;UTC;13:30;13:45;NRV-Saldo;Betrieblich;MW;N.A.;0\n')
        rows = ntp.parse_csv(csv)
        self.assertEqual(rows[0]['ts'], datetime(2023, 6, 10, 13, 0, tzinfo=m.UTC))
        self.assertAlmostEqual(rows[0]['deutschland'], 561.721)
        self.assertEqual(rows[1]['ts'], datetime(2023, 6, 10, 13, 15, tzinfo=m.UTC))
        self.assertAlmostEqual(rows[1]['deutschland'], -1234.5)
        self.assertIsNone(rows[2]['deutschland'])

    def test_csv_unknown_timezone_rejected(self):
        with self.assertRaises(ValueError):
            ntp.parse_csv('Datum;Zeitzone;von;bis;Deutschland\n10.06.2023;XYZ;13:00;13:15;1\n')

    def test_activation_columns(self):
        csv = ('Datum;Zeitzone;von;bis;Deutschland (Positiv);Deutschland (Negativ)\n'
               '10.06.2023;UTC;13:00;13:15;300,5;-120\n')
        rows = ntp.parse_csv(csv)
        up = ntp._column(rows, 'Deutschland (Positiv)')
        self.assertAlmostEqual(list(up.values())[0], 300.5)


class RenewablesV5Tests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def test_tech_split_of_headline_error(self):
        from tests.test_core import generation_xml
        docs = {'A75': generation_xml(100, 20, 80), 'A01': generation_xml(90, 18, 75),
                'A40': generation_xml(95, 19, 78), 'A18': generation_xml(98, 21, 79)}
        with patch.object(m, 'entsoe_request', side_effect=lambda p, s, e: docs['A75'] if p['documentType'] == 'A75' else docs[p['processType']]):
            d = m.fetch_renewables('2026-08-28', True)
        self.assertEqual(d['kpi']['tech_error_mw'], {'Solar': 10.0, 'Wind Offshore': 2.0, 'Wind Onshore': 5.0})
        self.assertEqual(d['kpi']['day_avg_error_mw'], 17.0)


class GuardTests(unittest.TestCase):
    def test_out_of_range_day_rejected_before_upstream(self):
        client = TestClient(m.app)
        with patch.object(m, 'entsoe_request', side_effect=AssertionError('no upstream call expected')):
            self.assertEqual(client.get('/api/load?day=1999-01-01').status_code, 422)
            self.assertEqual(client.get('/api/outages?day=2099-01-01').status_code, 422)

    def test_health_reports_auth_and_ntp(self):
        h = TestClient(m.app).get('/health').json()
        self.assertIn('auth_enabled', h)
        self.assertIn('netztransparenz', h)


if __name__ == '__main__':
    unittest.main()


class FakeResp:
    def __init__(self, status, text=''):
        self.status_code, self.content = status, text.encode()
        self.ok = 200 <= status < 300


class NtpClientTests(unittest.TestCase):
    CSV = ('Datum;Zeitzone;von;bis;Datenkategorie;Datentyp;Einheit;Deutschland\n'
           '10.09.2026;UTC;10:00;10:15;NRV-Saldo;Betrieblich;MW;250,5\n')

    def setUp(self):
        ntp._variant['value'] = None
        ntp._token.update(value='tok', expires=1e12)

    def tearDown(self):
        ntp._variant['value'] = None
        ntp._token.update(value=None, expires=0)

    def test_falls_back_to_query_variant_and_remembers_it(self):
        calls = []
        def fake(session, url, token):
            calls.append(url)
            return FakeResp(404) if '?dateFrom=' not in url else FakeResp(200, self.CSV)
        start = datetime(2026, 9, 10, 0, 0, tzinfo=m.UTC)
        with patch.object(ntp, '_request', side_effect=fake):
            diag = {}
            text = ntp._get(None, 'NrvSaldo/NRVSaldo/Betrieblich', start, start + timedelta(days=1), diag)
            ntp._get(None, 'NrvSaldo/NRVSaldo/Betrieblich', start, start + timedelta(days=1))
        self.assertIn('250,5', text)
        self.assertEqual(diag['variant'], 'query')
        self.assertTrue(calls[0].endswith('/2026-09-10T00:00:00/2026-09-11T00:00:00'))
        self.assertEqual(len(calls), 3)  # path 404, query 200, then query directly

    def test_fetch_block_uses_quality_assured_when_operational_empty(self):
        start = datetime(2026, 9, 10, 0, 0, tzinfo=m.UTC)
        def fake(session, url, token):
            if 'Betrieblich' in url:
                return FakeResp(204)
            if 'NRVSaldo' in url:
                return FakeResp(200, self.CSV)
            return FakeResp(200, 'Datum;Zeitzone;von;bis;Deutschland (Positiv);Deutschland (Negativ)\n10.09.2026;UTC;10:00;10:15;300;-50\n')
        with patch.object(ntp, '_request', side_effect=fake):
            block = ntp.fetch_block(start, start + timedelta(days=1))
        self.assertEqual(block['status'], {'nrv': 'ok', 'afrr': 'ok', 'mfrr': 'ok'})
        self.assertEqual(block['diag']['nrv']['flavour'], 'Qualitaetsgesichert')
        self.assertAlmostEqual(list(block['nrv'].values())[0], 250.5)
        self.assertEqual(list(block['afrr_down'].values())[0], 50.0)

    def test_unauthorized_is_error_not_crash(self):
        start = datetime(2026, 9, 10, 0, 0, tzinfo=m.UTC)
        with patch.object(ntp, '_request', return_value=FakeResp(401)), patch.object(ntp, '_get_token', return_value='tok'):
            block = ntp.fetch_block(start, start + timedelta(days=1))
        self.assertEqual(set(block['status'].values()), {'error'})
        self.assertIn('401', block['diag']['nrv']['error'])

    def test_html_error_page_is_parse_error(self):
        start = datetime(2026, 9, 10, 0, 0, tzinfo=m.UTC)
        with patch.object(ntp, '_request', return_value=FakeResp(200, '<html>maintenance</html>')), \
             patch.object(ntp, '_get_token', return_value='tok'):
            block = ntp.fetch_block(start, start + timedelta(days=1))
        self.assertEqual(block['status']['nrv'], 'parse_error')
