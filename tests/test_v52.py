"""v5.2 regression tests: stale-while-revalidate cache, background refresher,
freshness endpoint, 'system now' from the freshest source."""
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as m

BERLIN = m.BERLIN


class StaleCacheTests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def test_expired_value_is_only_available_via_get_stale(self):
        m.CACHE.set('k', {'v': 1}, ttl=0)
        self.assertIsNone(m.CACHE.get('k'))
        self.assertEqual(m.CACHE.get_stale('k', max_age=60), {'v': 1})
        self.assertIsNone(m.CACHE.get_stale('k', max_age=-1))

    def test_cached_serves_stale_only_for_refresher_managed_keys(self):
        calls = []
        def work():
            calls.append(1)
            return {'n': len(calls)}
        m.CACHE.set('p', {'n': 0}, ttl=0)
        with patch.object(m.REFRESHER, 'manages', return_value=True):
            self.assertEqual(m.cached('p', 60, False, work), {'n': 0})
        self.assertEqual(calls, [])
        with patch.object(m.REFRESHER, 'manages', return_value=False):
            self.assertEqual(m.cached('p', 60, False, work), {'n': 1})
        self.assertEqual(calls, [1])

    def test_force_never_serves_stale(self):
        m.CACHE.set('p', {'n': 0}, ttl=0)
        with patch.object(m.REFRESHER, 'manages', return_value=True):
            self.assertEqual(m.cached('p', 60, True, lambda: {'n': 9}), {'n': 9})

    def test_cache_keys_match_between_fetchers_and_refresher(self):
        d = datetime.now(BERLIN).date()
        keys = m.BackgroundRefresher.keys_for(d)
        self.assertIn(f'borders:v5:{d.isoformat()}:{",".join(m.DEFAULT_NEIGHBORS)}', keys)
        self.assertIn(f'balancing:v5:{d.isoformat()}', keys)
        self.assertFalse(m.BackgroundRefresher().manages(f'balancing:v5:{d.isoformat()}'))  # not running


class RefresherTests(unittest.TestCase):
    def make(self, fail=None):
        r = m.BackgroundRefresher()
        order = []
        def job(name):
            def run():
                order.append(name)
                if name == fail:
                    raise RuntimeError(f'{name} upstream down')
            return run
        r.jobs = lambda: {name: job(name) for name, _ in r.SCHEDULE}
        return r, order

    def test_first_tick_runs_everything_in_order_renewables_before_load(self):
        r, order = self.make()
        ran = r.run_once(now=1000.0)
        self.assertEqual(ran, ['balancing', 'renewables', 'load', 'borders', 'outages'])
        self.assertLess(order.index('renewables'), order.index('load'))

    def test_intervals_are_respected(self):
        r, order = self.make()
        r.run_once(now=1000.0)
        order.clear()
        self.assertEqual(r.run_once(now=1000.0 + 179), [])
        self.assertEqual(r.run_once(now=1000.0 + 180), ['balancing'])
        self.assertEqual(r.run_once(now=1000.0 + 300), ['renewables', 'load', 'borders'])
        self.assertEqual(r.run_once(now=1000.0 + 900), ['balancing', 'renewables', 'load', 'borders', 'outages'])

    def test_one_failing_job_does_not_stop_the_others_and_is_reported(self):
        r, order = self.make(fail='borders')
        with self.assertLogs('de_power_desk', level='WARNING'):
            r.run_once(now=1.0)
        self.assertEqual(order, ['balancing', 'renewables', 'load', 'borders', 'outages'])
        self.assertIn('borders', r.last_error)
        self.assertIn('outages', r.last_ok)

    def test_transient_failure_keeps_previous_complete_panel(self):
        m.CACHE.clear()
        d = datetime.now(BERLIN).date()
        key = m.BackgroundRefresher.key_of('load', d)
        good = {'updated': 'old', 'series': {k: [1] for k in ('Load Actual', 'Load Forecast', 'Residual Load Actual',
                                                             'Residual Load Forecast', 'Residual Load Surprise')}}
        m.CACHE.set(key, good, 60)
        stored = m.CACHE.stored_at(key)
        r = m.BackgroundRefresher()
        r.jobs = lambda: {name: (lambda: m.CACHE.set(key, {'updated': 'new', 'series': {}}, 60)) if name == 'load' else (lambda: None)
                          for name, _ in r.SCHEDULE}
        with self.assertLogs('de_power_desk', level='WARNING'):
            r.run_once(now=5.0)
        self.assertEqual(m.CACHE.get_stale(key, 3600)['updated'], 'old')
        self.assertEqual(m.CACHE.stored_at(key), stored)       # true age preserved
        self.assertIn('kept previous complete', r.last_error['load'])

    def test_equal_or_better_new_result_replaces_previous(self):
        m.CACHE.clear()
        d = datetime.now(BERLIN).date()
        key = m.BackgroundRefresher.key_of('load', d)
        m.CACHE.set(key, {'updated': 'old', 'series': {}}, 60)
        r = m.BackgroundRefresher()
        r.jobs = lambda: {name: (lambda: m.CACHE.set(key, {'updated': 'new', 'series': {'Load Actual': [1]}}, 60)) if name == 'load' else (lambda: None)
                          for name, _ in r.SCHEDULE}
        r.run_once(now=5.0)
        self.assertEqual(m.CACHE.get_stale(key, 3600)['updated'], 'new')
        self.assertNotIn('load', r.last_error)

    def test_thread_starts_and_stops(self):
        r, _ = self.make()
        r.TICK_SECONDS = 0.05
        r.start()
        try:
            deadline = time.time() + 2
            while not r.last_ok and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(r.running)
            self.assertIn('balancing', r.last_ok)
        finally:
            r.stop()
        self.assertFalse(r.running)

    def test_lifespan_starts_refresher_only_with_api_key(self):
        with patch.object(m.REFRESHER, 'start') as start, patch.object(m.REFRESHER, 'stop') as stop:
            with patch.object(m, 'API_KEY', 'k'), patch.object(m, 'BACKGROUND_REFRESH', True):
                with TestClient(m.app):
                    pass
            self.assertEqual(start.call_count, 1)
            self.assertEqual(stop.call_count, 1)
        with patch.object(m.REFRESHER, 'start') as start, patch.object(m.REFRESHER, 'stop'):
            with patch.object(m, 'API_KEY', ''), patch.object(m, 'BACKGROUND_REFRESH', True):
                with TestClient(m.app):
                    pass
            start.assert_not_called()

    def test_load_does_not_force_renewables(self):
        with patch.object(m, 'fetch_renewables', return_value={'series': {}}) as ren, \
                patch.object(m, 'entsoe_request', side_effect=LookupError('none')):
            try:
                m.fetch_load('2026-09-10', True)
            except LookupError:
                pass
        for call in ren.call_args_list:
            self.assertFalse(call.kwargs.get('force', False))


class FreshnessEndpointTests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def test_reports_panel_build_times_from_cache_without_upstream(self):
        d = datetime.now(BERLIN).date()
        m.CACHE.set(m.cache_key('balancing', d), {'updated': '2026-09-25T18:00:00+02:00'}, ttl=0)
        with patch.object(m, 'entsoe_request', side_effect=AssertionError('no upstream')):
            f = TestClient(m.app).get('/api/freshness').json()
        self.assertEqual(f['date'], d.isoformat())
        self.assertEqual(f['panels']['balancing'], '2026-09-25T18:00:00+02:00')
        self.assertIsNone(f['panels']['borders'])
        self.assertIn('background', f)

    def test_health_reports_refresher(self):
        h = TestClient(m.app).get('/health').json()
        self.assertIn('background_refresh', h)
        self.assertEqual(h['version'], m.VERSION)


class SystemNowTests(unittest.TestCase):
    def setUp(self):
        m.CACHE.clear()

    def run_balancing(self, a86_times, nrv_times):
        t0 = datetime(2026, 8, 28, 12, 0, tzinfo=BERLIN).astimezone(m.UTC)
        volume = [{'ts': t0 + timedelta(minutes=15 * i), 'value': 10.0, 'business': 'A19', 'direction': 'A02',
                   'category': None, 'source_area': a, 'revision': '1', 'created': '2026-08-28T12:00Z'}
                  for i in a86_times for a in m.BALANCING_AREAS]
        nrv = {t0 + timedelta(minutes=15 * i): 150.0 + i for i in (nrv_times or [])}
        block = {'status': {'nrv': 'ok', 'afrr': 'ok', 'mfrr': 'ok'}, 'diag': {}, 'nrv': nrv,
                 'afrr_up': {}, 'afrr_down': {}, 'mfrr_up': {}, 'mfrr_down': {}}

        def fake_doc(doc, start, end):
            return (volume if doc == 'A86' else []), {a: 'ok' for a in m.BALANCING_AREAS}, 'German control areas'
        with patch('app.main._query_aggregated_bids', return_value=([], 'no_data')), \
                patch('app.main._query_balancing_doc_with_fallback', side_effect=fake_doc), \
                patch('app.main.ntp.configured', return_value=nrv_times is not None), \
                patch('app.main.ntp.fetch_block', return_value=block):
            return m.fetch_balancing('2026-08-28', force=True)['kpi']

    def test_fresher_nrv_drives_the_headline(self):
        k = self.run_balancing(a86_times=[0, 1], nrv_times=[0, 1, 2, 3])
        self.assertEqual(k['system_now_source'], 'NRV-Saldo (netztransparenz.de)')
        self.assertEqual(k['system_now_mw'], -153.0)       # balance sign = -NRV
        self.assertEqual(k['system_now_state'], 'deficit')
        self.assertEqual(k['imbalance_volume_mwh'], -40.0)  # A86 still reported
        self.assertEqual(k['system_now_changes']['d15_mw'], -1.0)

    def test_tie_prefers_a86(self):
        k = self.run_balancing(a86_times=[0, 1], nrv_times=[0, 1])
        self.assertEqual(k['system_now_source'], 'A86 (ENTSO-E)')
        self.assertEqual(k['system_now_mw'], -160.0)

    def test_without_netztransparenz_a86_is_used(self):
        k = self.run_balancing(a86_times=[0], nrv_times=None)
        self.assertEqual(k['system_now_source'], 'A86 (ENTSO-E)')


class LogMaskingTests(unittest.TestCase):
    def test_library_log_records_with_the_token_in_a_url_are_masked(self):
        import io, logging
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(m.SecretMaskFilter())
        lib = logging.getLogger('urllib3.connectionpool.test')
        lib.addHandler(handler)
        lib.propagate = False
        lib.setLevel(logging.WARNING)
        try:
            with patch.dict('os.environ', {'ENTSOE_API_KEY': 'abcd-1234-secret-token'}):
                lib.warning("Retrying after %s: /api?documentType=A24&securityToken=%s&periodStart=1",
                            'RemoteDisconnected', 'abcd-1234-secret-token')
                lib.warning("token appears raw: abcd-1234-secret-token")
        finally:
            lib.removeHandler(handler)
        out = stream.getvalue()
        self.assertNotIn('abcd-1234-secret-token', out)
        self.assertIn('securityToken=***', out)

    def test_root_handlers_carry_the_filter(self):
        import logging
        self.assertTrue(any(isinstance(f, m.SecretMaskFilter) for h in logging.getLogger().handlers for f in h.filters))


class TransportTests(unittest.TestCase):
    def test_static_assets_are_gzipped_and_cacheable_when_versioned(self):
        c = TestClient(m.app)
        r = c.get('/static/plotly.min.js?v=3.3.1', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get('content-encoding'), 'gzip')
        self.assertIn('immutable', r.headers.get('cache-control', ''))
        html = c.get('/').text
        self.assertIn('/static/plotly.min.js?v=', html)
        self.assertIn(f'/static/app.js?v={m.VERSION}', html)

    def test_unversioned_static_is_not_marked_immutable(self):
        r = TestClient(m.app).get('/static/app.js')
        self.assertNotIn('immutable', r.headers.get('cache-control', ''))


class FrontendV52Tests(unittest.TestCase):
    def test_frontend_polls_freshness_and_shows_age(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        for needle in ('/api/freshness', 'system_now_mw', 'Server-Stand', 'min alt', 'loadPanels(changed)'):
            self.assertIn(needle, js)


if __name__ == '__main__':
    unittest.main()
