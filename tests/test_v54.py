"""v5.4: preliminary imbalance from netztransparenz RZ-Saldo, behind a switch."""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import main as m
from app import ntp

BERLIN = m.BERLIN
T0 = datetime(2026, 8, 28, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)
Q = timedelta(minutes=15)
COLS = ('50Hertz', 'Amprion', 'TenneT TSO', 'TransnetBW')


class NowcastFunctionTests(unittest.TestCase):
    def test_fills_only_missing_areas_after_the_last_complete_a86_sum(self):
        a86 = {'50HERTZ': {T0: -10.0, T0 + Q: -11.0}, 'AMPRION': {T0: 5.0, T0 + Q: 6.0},
               'TENNET_DE': {T0: -20.0}, 'TRANSNETBW': {T0: 1.0, T0 + Q: 2.0}}
        total = {T0: -24.0}
        rz = {'50Hertz': {T0 + Q: 999.0}, 'Amprion': {T0 + Q: 999.0}, 'TenneT TSO': {T0: 1.0, T0 + Q: 80.0},
              'TransnetBW': {T0 + Q: 999.0}}
        out = m.imbalance_nowcast(a86, rz, total)
        # T0 already covered by A86; T0+Q: published A86 values win, only TenneT from -RZ/4
        self.assertEqual(out, {T0 + Q: -11.0 + 6.0 - 20.0 + 2.0})

    def test_mtu_is_skipped_unless_every_area_is_covered(self):
        a86 = {a: {} for a in m.BALANCING_AREAS}
        rz = {'50Hertz': {T0: 4.0}, 'Amprion': {T0: 4.0}, 'TenneT TSO': {T0: 4.0}}  # TransnetBW missing
        self.assertEqual(m.imbalance_nowcast(a86, rz, {}), {})

    def test_sign_matches_a86(self):
        # RZ-Saldo > 0 = short -> A86 negative
        a86 = {a: {} for a in m.BALANCING_AREAS}
        rz = {c: {T0: 40.0} for c in COLS}
        self.assertEqual(m.imbalance_nowcast(a86, rz, {}), {T0: -40.0})


class NowcastPanelTests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def run_balancing(self, switch_on=True):
        volume = [{'ts': T0 + i * Q, 'value': 10.0, 'business': 'A19', 'direction': 'A02', 'category': None,
                   'source_area': a, 'revision': '1', 'created': '2026-08-28T12:00Z'}
                  for i in range(4) for a in m.BALANCING_AREAS if not (a == 'TENNET_DE' and i == 3)]
        rz = {c: {T0 + i * Q: 40.0 for i in range(5)} for c in COLS}
        block = {'status': {'nrv': 'ok', 'afrr': 'ok', 'mfrr': 'ok'}, 'diag': {}, 'nrv': {T0 + i * Q: 160.0 for i in range(5)},
                 'afrr_up': {}, 'afrr_down': {}, 'mfrr_up': {}, 'mfrr_down': {}, 'rz': rz, 'rz_status': 'ok'}

        def fake_doc(doc, start, end):
            return (volume if doc == 'A86' else []), {a: 'ok' for a in m.BALANCING_AREAS}, 'German control areas'
        calls = []
        def fake_block(start, end, include_rz=False):
            calls.append(include_rz)
            return block if include_rz else {**block, 'rz': {}, 'rz_status': 'not_requested'}
        with patch('app.main._query_aggregated_bids', return_value=([], 'no_data')), \
                patch('app.main._query_balancing_doc_with_fallback', side_effect=fake_doc), \
                patch('app.main.ntp.configured', return_value=True), \
                patch('app.main.ntp.fetch_block', side_effect=fake_block), \
                patch.object(m, 'NTP_IMBALANCE_NOWCAST', switch_on):
            return m.fetch_balancing('2026-08-28', force=True), calls

    def test_nowcast_extends_the_series_and_hour_mean(self):
        d, calls = self.run_balancing(True)
        self.assertEqual(calls, [True])
        s, k = d['series'], d['kpi']
        self.assertEqual(len(s['Net imbalance volume']), 3)          # A86 sum complete through T0+2Q
        pts = s['Net imbalance volume nowcast']
        self.assertEqual([p['v'] for p in pts], [-40.0, -40.0])      # T0+3Q (TenneT filled) and T0+4Q (all RZ)
        self.assertEqual(k['imbalance_nowcast_points'], 2)
        self.assertEqual(k['imbalance_1h_avg_mw'], -160.0)
        self.assertEqual(k['imbalance_volume_mwh'], -40.0)           # A86 KPI itself unchanged
        self.assertEqual(d['sources']['NTP']['state'], 'ok')
        self.assertEqual(d['sources']['NTP']['rz_saldo'], 'ok')

    def test_switch_off_is_exactly_the_previous_behaviour(self):
        d, calls = self.run_balancing(False)
        self.assertEqual(calls, [False])
        self.assertEqual(d['series']['Net imbalance volume nowcast'], [])
        self.assertEqual(d['kpi']['imbalance_nowcast_points'], 0)
        self.assertEqual(d['kpi']['imbalance_1h_avg_mw'], None)      # only 3 A86 MTUs -> no full hour


class NtpRzParsingTests(unittest.TestCase):
    CSV = ('Datum;Zeitzone;von;bis;Datenkategorie;Datentyp;Einheit;50Hertz;Amprion;TenneT TSO;TransnetBW\n'
           '24.09.2026;UTC;09:45;10:00;RZ-Saldo;Betrieblich;MW;-1015,931;-169,536;-167,477;803,775\n')

    def test_rz_columns_are_parsed_and_status_kept_separate(self):
        start = datetime(2026, 9, 24, 0, 0, tzinfo=m.UTC)
        def fake_get(session, path, s, e, diag=None):
            if 'RZSaldo' in path:
                return self.CSV
            raise LookupError('no data')
        with patch.object(ntp, '_get', side_effect=fake_get):
            block = ntp.fetch_block(start, start + timedelta(days=1), include_rz=True)
        t = datetime(2026, 9, 24, 9, 45, tzinfo=m.UTC)
        self.assertEqual(block['rz']['TenneT TSO'][t], -167.477)
        self.assertEqual(block['rz']['TransnetBW'][t], 803.775)
        self.assertEqual(block['rz_status'], 'ok')
        self.assertNotIn('rz', block['status'])

    def test_rz_not_requested_by_default(self):
        with patch.object(ntp, '_get', side_effect=LookupError('none')) as g:
            block = ntp.fetch_block(datetime(2026, 9, 24, tzinfo=m.UTC), datetime(2026, 9, 25, tzinfo=m.UTC))
        self.assertFalse(any('RZSaldo' in c.args[1] for c in g.call_args_list))
        self.assertEqual(block['rz_status'], 'not_requested')


class FrontendV54Tests(unittest.TestCase):
    def test_preliminary_bars_are_marked(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        self.assertIn("s['Net imbalance volume nowcast']", js)
        self.assertIn("vorläufig (netztransparenz)", js)
        self.assertIn("pattern: { shape: '/'", js)


if __name__ == '__main__':
    unittest.main()
