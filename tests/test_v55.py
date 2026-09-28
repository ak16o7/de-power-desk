"""v5.5: compact header, refresh icon, all outage zones in one chart."""
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m


class HeaderTests(unittest.TestCase):
    def test_header_is_compact(self):
        html = TestClient(m.app).get('/').text
        self.assertIn('id="refresh" type="button" aria-label="Jetzt neu laden"', html)
        self.assertNotIn('>Aktualisieren<', html)
        self.assertIn('/> Live</label>', html)
        self.assertIn('id="statusDetail"', html)
        css = Path('app/static/style.css').read_text(encoding='utf-8')
        self.assertIn('.controls{display:flex;align-items:center;gap:8px;flex-wrap:nowrap}', css)

    def test_status_uses_readable_panel_names_and_tooltip(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        self.assertIn("outages: 'Kraftwerke'", js)
        self.assertIn("$('status').title =", js)
        # countdown lives in the tooltip, not in the visible status text
        self.assertNotIn("$('statusText').textContent = text + next", js)


class OutageChartTests(unittest.TestCase):
    def test_all_zones_in_one_chart_without_zone_switch(self):
        html = TestClient(m.app).get('/').text
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        self.assertNotIn('id="outZone"', html)
        self.assertNotIn('state.outZone', js)
        self.assertIn("const OUT_ZONES = ['DE_LU', 'FR', 'NL', 'BE'];", js)
        self.assertIn('annotations: labels', js)


class PhoneChartTests(unittest.TestCase):
    def test_legend_never_covers_the_plot_on_narrow_charts(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        # legend anchored to the figure top pushes the plot down; card grows by its height
        self.assertIn("yref: 'container', y: 1, yanchor: 'top'", js)
        self.assertIn('.then(() => fitLegend(el, narrow))', js)
        self.assertIn("showlegend: !isNarrow($('chOut'))", js)
        # tick spacing from the plot area width, re-render when crossing the breakpoint
        self.assertIn('plotW < 240 ? 6 : plotW < 490 ? 4 : 2', js)
        self.assertIn("window.addEventListener('resize'", js)


if __name__ == '__main__':
    unittest.main()
