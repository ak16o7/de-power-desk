"""v5.6: Systembilanz panel - one quantity in one unit, freshest source per
quarter-hour, self-labelled subplots, short description, methodology folded."""
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=m.BERLIN).astimezone(m.UTC)
Q = timedelta(minutes=15)
COLS = ('50Hertz', 'Amprion', 'TenneT TSO', 'TransnetBW')


class NrvFallbackTests(unittest.TestCase):
    def test_mtu_not_covered_by_all_rz_columns_falls_back_to_nrv(self):
        a86 = {a: {T0: 1.0} for a in m.BALANCING_AREAS}
        total = {T0: 4.0}
        rz = {c: {T0 + Q: 40.0} for c in COLS}
        rz['Amprion'] = {}                                   # Amprion RZ not yet published for T0+Q
        nrv = {T0: 999.0, T0 + Q: 160.0, T0 + 2 * Q: -80.0}
        out = m.imbalance_nowcast(a86, rz, total, nrv)
        # T0 is official (A86) and never overwritten; T0+Q/T0+2Q from -NRV/4
        self.assertEqual(out, {T0 + Q: -40.0, T0 + 2 * Q: 20.0})

    def test_per_area_values_win_over_nrv(self):
        a86 = {a: {} for a in m.BALANCING_AREAS}
        rz = {c: {T0: 40.0} for c in COLS}
        out = m.imbalance_nowcast(a86, rz, {}, {T0: 999.0})
        self.assertEqual(out, {T0: -40.0})

    def test_without_a86_the_whole_day_comes_from_nrv(self):
        out = m.imbalance_nowcast({}, {}, {}, {T0: 100.0, T0 + Q: -100.0})
        self.assertEqual(out, {T0: -25.0, T0 + Q: 25.0})
        self.assertEqual(list(out), sorted(out))


class SystembilanzPanelTests(unittest.TestCase):
    js = Path('app/static/app.js').read_text(encoding='utf-8')
    html = TestClient(m.app).get('/').text

    def render_sys(self):
        a = self.js.index('function renderSys()')
        return self.js[a:self.js.index('const RENDER =', a)]

    def test_one_unit_and_no_duplicate_nrv_line(self):
        f = self.render_sys()
        self.assertIn('q.v * 4', f)                           # A86 MWh per 15 min -> MW
        self.assertNotIn("s['NRV-Saldo']", f)                 # same quantity as the bars
        self.assertNotIn('(MWh)', f)
        self.assertIn('showlegend: false', f)
        for head in ('<b>Systembilanz</b> MW', '<b>reBAP</b> €/MWh', '<b>Regelenergie</b> MW, + = hoch'):
            self.assertIn(head, f)

    def test_description_is_short_and_methodology_folded(self):
        p = re.search(r'<h2>Systembilanz, reBAP &amp; Regelenergie</h2><p>(.*?)</p>', self.html).group(1)
        self.assertLess(len(p), 140)
        self.assertNotIn('RZ-Saldo', p)
        self.assertIn('<details class="method" id="methSys">', self.html)
        self.assertIn('id="srcSysAll"', self.html)
        # only problems as chips; the English 'nur Reserve' chip is gone
        self.assertNotIn('nur Reserve', self.js)
        self.assertIn("stateCls(v.state) !== 'ok'", self.render_sys())


if __name__ == '__main__':
    unittest.main()
