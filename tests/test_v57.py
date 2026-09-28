"""v5.7: one presentation style for all panels - HTML chart heads with keys,
short descriptions, methodology folded, German labels, linked zoom."""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m

CHARTS = ('chRes', 'chResDiff', 'chLoad', 'chLoadDiff', 'chFlow', 'chOut', 'chSys', 'chRebap', 'chReg')


class PresentationTests(unittest.TestCase):
    html = TestClient(m.app).get('/').text
    js = Path('app/static/app.js').read_text(encoding='utf-8')
    css = Path('app/static/style.css').read_text(encoding='utf-8')

    def test_every_chart_has_a_head_and_every_panel_a_methodology(self):
        for cid in CHARTS:
            self.assertIn(f'<div class="chead"></div><div class="chart" id="{cid}"></div>', self.html)
            self.assertIn(f"head('{cid}',", self.js)
        panels = re.findall(r'<article class="panel[^"]*" id="(p\w+)">(.*?)</article>', self.html, re.S)
        self.assertEqual([p for p, _ in panels], ['pRes', 'pLoad', 'pFlow', 'pOut', 'pSys'])
        for pid, body in panels:
            self.assertIn('<summary><span data-lang="de">Quellen &amp; Methodik</span><span data-lang="en">Sources &amp; methodology</span></summary>', body, pid)

    def test_descriptions_are_one_plain_sentence_without_codes(self):
        found = re.findall(r'<p><span data-lang="de">(.*?)</span><span data-lang="en">(.*?)</span></p>', self.html)
        self.assertEqual(len(found), 5)
        for de, en in found:                                        # v5.8: both languages
            for p in (de, en):
                self.assertLess(len(p), 160, p)
                self.assertIsNone(re.search(r'\bA\d\d\b', p), p)  # ENTSO-E codes live in the methodology
        self.assertIn('<span data-lang="de">Kraftwerksverfügbarkeit</span><span data-lang="en">Power plant availability</span>', self.html)

    def test_keys_toggle_series_and_zoom_is_linked(self):
        self.assertIn("button.key[data-chart]", self.js)
        self.assertIn("state.hidden[b.dataset.chart]", self.js)
        self.assertIn("el.on('plotly_relayout'", self.js)
        self.assertIn("el.on('plotly_doubleclick'", self.js)
        self.assertIn("const zoom = state.zoom[groupOf(id)[0]];", self.js)
        self.assertIn('fixedrange: true', self.js)                # drag zooms time only
        self.assertIn('.chead{', self.css)
        self.assertIn('button.key[aria-pressed="false"]', self.css)

    def test_german_labels(self):
        self.assertIn("Nuclear: 'Kernenergie'", self.js)
        self.assertIn('${fuel(n.fuel)}', self.js)
        self.assertNotIn('Intraday-XB', self.js)
        self.assertNotIn('(A54)', self.html)
        self.assertNotIn('plant-only', self.js)


if __name__ == '__main__':
    unittest.main()
