"""v5.3: light/dark toggle."""
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m


class ThemeToggleTests(unittest.TestCase):
    def test_header_has_toggle_and_pre_paint_theme_script(self):
        html = TestClient(m.app).get('/').text
        self.assertIn('id="theme"', html)
        head = html.split('</head>')[0]
        # stored theme is applied before the stylesheet loads -> no flash
        self.assertLess(head.index("localStorage.getItem('dpd-theme')"), head.index('style.css'))

    def test_explicit_light_theme_overrides_dark_os(self):
        css = Path('app/static/style.css').read_text(encoding='utf-8')
        self.assertIn(':root[data-theme="light"]{', css)
        self.assertIn(':root:not([data-theme="dark"])', css)

    def test_choice_is_stored_defensively_and_charts_rerender(self):
        js = Path('app/static/app.js').read_text(encoding='utf-8')
        self.assertIn("THEME_KEY = 'dpd-theme'", js)
        self.assertIn('try { localStorage.setItem(THEME_KEY, next); } catch', js)
        self.assertIn('Object.values(RENDER).forEach((f) => f());  // Plotly', js)


if __name__ == '__main__':
    unittest.main()
