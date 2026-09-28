"""v5.7.1: reBAP from the TSOs' AEP-Schätzer (netztransparenz) when ENTSO-E
A85 lags - A85 republishes exactly these values, but at times an hour late."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import main as m
from app import ntp

T0 = datetime(2026, 9, 28, 16, 0, tzinfo=m.UTC)
Q = timedelta(minutes=15)
AEP_CSV = ('Datum;Zeitzone;von;bis;Datenkategorie;Datentyp;Einheit;AEP-Schätzer;Status\n'
           '28.09.2026;UTC;17:00;17:15;AEP-Schätzer;Betrieblich;EUR/MWh;317,04;0\n'
           '28.09.2026;UTC;17:15;17:30;AEP-Schätzer;Betrieblich;EUR/MWh;;\n')


class AepFetchTests(unittest.TestCase):
    def test_aep_is_parsed_and_kept_out_of_the_core_status(self):
        start = datetime(2026, 9, 28, tzinfo=m.UTC)
        def fake_get(session, path, s, e, diag=None):
            if 'AepSchaetzer' in path:
                return AEP_CSV
            raise LookupError('no data')
        with patch.object(ntp, '_get', side_effect=fake_get):
            block = ntp.fetch_block(start, start + timedelta(days=1))
        self.assertEqual(block['aep'], {datetime(2026, 9, 28, 17, 0, tzinfo=m.UTC): 317.04})  # empty future row dropped
        self.assertEqual(block['aep_status'], 'ok')
        self.assertNotIn('aep', block['status'])

    def test_aep_failure_never_breaks_the_block(self):
        start = datetime(2026, 9, 28, tzinfo=m.UTC)
        def fake_get(session, path, s, e, diag=None):
            if 'AepSchaetzer' in path:
                raise RuntimeError('boom')
            raise LookupError('no data')
        with patch.object(ntp, '_get', side_effect=fake_get):
            block = ntp.fetch_block(start, start + timedelta(days=1))
        self.assertEqual(block['aep_status'], 'error')


class RebapExtensionTests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def run_balancing(self, aep):
        price = [{'ts': T0 + i * Q, 'value': 100.0 + i, 'category': None, 'source_area': 'DE', 'revision': '1',
                  'created': '2026-09-28T17:00Z', 'doc_status': None, 'business': None, 'direction': None} for i in range(2)]
        block = {'status': {'nrv': 'ok', 'afrr': 'ok', 'mfrr': 'ok'}, 'diag': {}, 'nrv': {}, 'afrr_up': {}, 'afrr_down': {},
                 'mfrr_up': {}, 'mfrr_down': {}, 'rz': {}, 'rz_status': 'not_requested', 'aep': aep, 'aep_status': 'ok'}
        def fake_doc(doc, start, end):
            return (price if doc == 'A85' else []), {'DE': 'ok'}, 'DE'
        with patch('app.main._query_aggregated_bids', return_value=([], 'no_data')), \
                patch('app.main._query_balancing_doc_with_fallback', side_effect=fake_doc), \
                patch('app.main.ntp.configured', return_value=True), \
                patch('app.main.ntp.fetch_block', return_value=block):
            return m.fetch_balancing('2026-09-28', force=True)

    def test_mtus_after_the_last_a85_value_come_from_netztransparenz(self):
        aep = {T0: 999.0, T0 + Q: 999.0, T0 + 2 * Q: 317.04, T0 + 3 * Q: 250.0}
        d = self.run_balancing(aep)
        pts = [(p['t'], p['v']) for p in d['series']['Imbalance price']]
        self.assertEqual([v for _, v in pts], [100.0, 101.0, 317.04, 250.0])   # A85 wins where it exists
        k = d['kpi']
        self.assertEqual(k['imbalance_price_eur_mwh'], 250.0)
        self.assertEqual(k['price_ntp_points'], 2)
        self.assertEqual(k['price_source'], 'AEP-Schätzer (netztransparenz.de)')
        self.assertEqual(k['price_status'], 'preliminary')
        self.assertEqual(d['sources']['NTP']['aep_schaetzer'], 'ok')

    def test_without_newer_aep_values_a85_stays_the_source(self):
        d = self.run_balancing({T0: 100.0})
        self.assertEqual(d['kpi']['price_ntp_points'], 0)
        self.assertEqual(d['kpi']['price_source'], 'A85 (ENTSO-E)')

    def test_methodology_names_the_source(self):
        html = Path('app/templates/index.html').read_text(encoding='utf-8')
        self.assertIn('AEP-Schätzer', html)


if __name__ == '__main__':
    unittest.main()
