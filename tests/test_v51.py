"""v5.1 regression tests: A86 semantics (business type, direction codes,
revision flips, units), publication status of reBAP/imbalance, 1-hour means,
past-day cache TTL and secret masking in logs."""
import logging
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import main as m

BERLIN = m.BERLIN
T0 = datetime(2026, 8, 28, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)


def vol(value, direction='A01', business='A19', area='50HERTZ', ts=T0, revision='1', created='2026-08-28T10:20Z',
        unit='MWH', status=None, resolution='PT15M'):
    return {'ts': ts, 'value': value, 'business': business, 'direction': direction, 'category': None,
            'source_area': area, 'revision': revision, 'created': created, 'unit': unit, 'doc_status': status,
            'resolution': resolution}


def a86_xml(status_code=None):
    status = f'<docStatus><value>{status_code}</value></docStatus>' if status_code else ''
    return (f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="urn:t"><revisionNumber>1</revisionNumber>{status}'
            '<TimeSeries><businessType>A19</businessType><flowDirection.direction>A02</flowDirection.direction>'
            '<quantity_Measure_Unit.name>MWH</quantity_Measure_Unit.name><curveType>A01</curveType><Period><timeInterval>'
            '<start>2026-08-28T10:00Z</start><end>2026-08-28T10:15Z</end></timeInterval><resolution>PT15M</resolution>'
            '<Point><position>1</position><quantity>42</quantity></Point></Period></TimeSeries></Balancing_MarketDocument>').encode()


class ImbalanceVolumeTests(unittest.TestCase):
    def test_direction_codes_follow_ddd_17_1_h(self):
        t1, t2, t3 = T0, T0 + timedelta(minutes=15), T0 + timedelta(minutes=30)
        series, ignored = m.imbalance_volume_series([vol(100, 'A01', ts=t1), vol(80, 'A02', ts=t2), vol(0, 'A03', ts=t3)])
        self.assertEqual(series, {t1: 100.0, t2: -80.0, t3: 0.0})
        self.assertEqual(ignored, {})

    def test_unknown_or_missing_direction_is_dropped_not_guessed_positive(self):
        series, ignored = m.imbalance_volume_series([vol(50, None), vol(60, 'Z99')])
        self.assertEqual(series, {})
        self.assertEqual(ignored['direction'], ['None', 'Z99'])

    def test_other_business_types_are_never_added_to_d(self):
        # e.g. the separately published MV-SV value of TR 17.1.H
        series, ignored = m.imbalance_volume_series([vol(100, 'A01'), vol(30, 'A01', business='B33')])
        self.assertEqual(series, {T0: 100.0})
        self.assertEqual(ignored['business'], ['B33'])

    def test_missing_business_type_is_accepted(self):
        series, _ = m.imbalance_volume_series([vol(10, 'A02', business=None)])
        self.assertEqual(series, {T0: -10.0})

    def test_mw_unit_is_converted_to_mwh_per_isp(self):
        series, _ = m.imbalance_volume_series([vol(400, 'A02', unit='MAW')])
        self.assertEqual(series, {T0: -100.0})

    def test_unknown_unit_is_dropped(self):
        series, ignored = m.imbalance_volume_series([vol(400, 'A02', unit='KWH')])
        self.assertEqual(series, {})
        self.assertEqual(ignored['unit'], ['KWH'])

    def test_revision_that_flips_direction_replaces_the_old_direction(self):
        old = vol(40, 'A01', revision='1', created='2026-08-28T10:20Z')
        new = vol(25, 'A02', revision='2', created='2026-08-28T11:00Z')
        rows = m.latest_revision_groups([old, new], ('source_area', 'business', 'ts'))
        self.assertEqual(rows, [new])
        self.assertEqual(m.imbalance_volume_series(rows)[0], {T0: -25.0})

    def test_both_directions_of_one_revision_are_kept_and_netted(self):
        up, down = vol(0, 'A01'), vol(25, 'A02')
        rows = m.latest_revision_groups([up, down, dict(down)], ('source_area', 'business', 'ts'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(m.imbalance_volume_series(rows)[0], {T0: -25.0})


class PublicationStatusTests(unittest.TestCase):
    def test_doc_status_and_unit_are_parsed(self):
        rows = m.parse_timeseries(a86_xml('A02'))
        self.assertEqual(rows[0]['doc_status'], 'A02')
        self.assertEqual(rows[0]['unit'], 'MWH')
        self.assertEqual(rows[0]['business'], 'A19')
        self.assertIsNone(m.parse_timeseries(a86_xml())[0]['doc_status'])

    def test_status_summary(self):
        self.assertEqual(m.publication_status([vol(1, status='A02'), vol(1, status='A02')], T0), 'final')
        self.assertEqual(m.publication_status([vol(1, status='A02'), vol(1, status='A01')], T0), 'preliminary')
        self.assertEqual(m.publication_status([vol(1, status='X01')], T0), 'preliminary')
        self.assertIsNone(m.publication_status([vol(1)], T0))
        self.assertIsNone(m.publication_status([vol(1, status='A02')], None))


class BalancingV51Tests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def run_balancing(self, volume, price):
        def fake_bids(process, start, end, area):
            return [], 'no_data'

        def fake_doc(doc, start, end):
            rows = price if doc == 'A85' else volume
            return rows, {a: 'ok' for a in m.BALANCING_AREAS}, 'German control areas'

        with patch('app.main._query_aggregated_bids', side_effect=fake_bids), \
                patch('app.main._query_balancing_doc_with_fallback', side_effect=fake_doc), \
                patch('app.main.ntp.configured', return_value=False):
            return m.fetch_balancing('2026-08-28', force=True)

    def test_germany_sum_ignores_mv_sv_and_reports_it(self):
        volume = []
        for i in range(4):
            t = T0 + timedelta(minutes=15 * i)
            for a in m.BALANCING_AREAS:
                volume.append(vol(10, 'A02', area=a, ts=t))
                volume.append(vol(999, 'A01', business='B33', area=a, ts=t))
        price = [{**vol(88.0, None, business=None, area=a, ts=T0 + timedelta(minutes=45)), 'category': c}
                 for a in m.BALANCING_AREAS for c in ('A04', 'A05')]
        d = self.run_balancing(volume, price)
        k = d['kpi']
        self.assertEqual(k['imbalance_volume_mwh'], -40.0)
        self.assertEqual(k['imbalance_state'], 'deficit')
        self.assertEqual(k['imbalance_avg_mw'], -160.0)
        self.assertEqual(k['imbalance_1h_avg_mw'], -160.0)
        self.assertIsNone(k['price_status'])
        self.assertEqual(k['imbalance_price_eur_mwh'], 88.0)
        self.assertEqual(d['sources']['A86']['ignored']['50HERTZ']['business'], ['B33'])

    def test_one_area_with_unknown_direction_removes_the_mtu_instead_of_guessing(self):
        volume = [vol(10, 'A02', area=a) for a in m.BALANCING_AREAS[:3]] + [vol(10, None, area=m.BALANCING_AREAS[3])]
        d = self.run_balancing(volume, [])
        self.assertEqual(d['series']['Net imbalance volume'], [])
        self.assertIsNone(d['kpi']['imbalance_state'])

    def test_final_price_status_is_reported(self):
        price = [{**vol(50.0, None, business=None, area=a, status='A02'), 'category': None} for a in m.BALANCING_AREAS]
        d = self.run_balancing([], price)
        self.assertEqual(d['kpi']['price_status'], 'final')


class HelperTests(unittest.TestCase):
    def test_trailing_mean_needs_four_contiguous_mtus(self):
        s = {T0 + timedelta(minutes=15 * i): float(i) for i in range(4)}
        last = T0 + timedelta(minutes=45)
        self.assertEqual(m.trailing_mean(s, last), 1.5)
        del s[T0 + timedelta(minutes=15)]
        self.assertIsNone(m.trailing_mean(s, last))
        self.assertIsNone(m.trailing_mean(s, None))

    def test_past_days_are_cached_longer(self):
        today = datetime.now(BERLIN).date()
        self.assertEqual(m.ttl_for(today, 240), 240)
        self.assertGreaterEqual(m.ttl_for(today - timedelta(days=1), 240), 3600)

    def test_log_upstream_masks_the_api_key(self):
        with patch.object(m, 'API_KEY', 'SECRET-TOKEN-123'):
            with self.assertLogs('de_power_desk', level='WARNING') as cm:
                m.log_upstream('A86 TENNET_DE', RuntimeError('bad url ?securityToken=SECRET-TOKEN-123'))
        self.assertNotIn('SECRET-TOKEN-123', '\n'.join(cm.output))
        self.assertIn('***', '\n'.join(cm.output))

    def test_border_methodology_does_not_call_the_total_a_loop_flow(self):
        with patch('app.main._one_flow', return_value=({}, 'no_data')):
            d = m.fetch_borders('2026-08-28', ['FR'], force=True)
        self.assertIn('loop flows cancel', d['methodology'])


class FrontendV51Tests(unittest.TestCase):
    def test_x_axis_is_utc_with_berlin_labels(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        self.assertNotIn('p.t.slice(0, 19)', js)  # old wall-clock x values collide on DST day
        for needle in ('utcX', 'timeTicks', 'withBerlinTime', "timeZone: 'Europe/Berlin'"):
            self.assertIn(needle, js)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
