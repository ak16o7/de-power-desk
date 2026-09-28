"""v5.8: German / English. Static text exists in both languages (CSS shows
one, chosen in <head> before first paint); generated text comes from TXT."""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m


class LanguageTests(unittest.TestCase):
    html = TestClient(m.app).get('/').text
    js = Path('app/static/app.js').read_text(encoding='utf-8')
    css = Path('app/static/style.css').read_text(encoding='utf-8')

    def test_language_is_set_before_first_paint_and_css_shows_one(self):
        head = self.html.split('</head>')[0]
        self.assertIn("localStorage.getItem('dpd-lang')", head)
        self.assertIn("d.lang=(l==='de'||l==='en')?l:(/^de\\b/i.test(navigator.language||'de')?'de':'en')", head)
        self.assertIn('html:not([lang="en"]) [data-lang="en"],html[lang="en"] [data-lang="de"]{display:none!important}', self.css)

    def test_every_german_text_has_an_english_twin(self):
        de = len(re.findall(r'data-lang="de"', self.html))
        en = len(re.findall(r'data-lang="en"', self.html))
        self.assertGreater(de, 30)
        self.assertEqual(de, en)
        for attr in ('title', 'aria-label', 'placeholder'):
            short = {'title': 'title', 'aria-label': 'aria', 'placeholder': 'ph'}[attr]
            for tag in re.findall(rf'<[^>]*\bdata-en-{short}="[^"]*"[^>]*>', self.html):
                self.assertIn(f'{attr}="', tag)                       # a German original exists
        self.assertEqual(len(re.findall(r'<option[^>]*data-en="', self.html)), 9)

    def test_toggle_in_header_desktop_and_phone(self):
        self.assertEqual(self.html.count('class="btn icon lang js-lang'), 2)
        self.assertIn('lang js-lang desktop-only" id="lang"', self.html)
        self.assertIn('lang js-lang mobile-only"', self.html)
        self.assertIn("localStorage.setItem(LANG_KEY, next)", self.js)

    def test_every_used_key_exists_in_both_languages(self):
        block = self.js[self.js.index('const TXT = {'):self.js.index('const tr = (key, vars)')]
        keys = set(re.findall(r"(\w+): \[", block))
        used = set(re.findall(r"\btr\('(\w+)'", self.js))
        self.assertTrue(used)
        self.assertEqual(sorted(used - keys), [])
        # number and date formats follow the language
        self.assertIn("separators: lang() === 'en' ? '.,' : ',.'", self.js)
        self.assertIn("const LOCALE = () => (lang() === 'en' ? 'en-GB' : 'de-DE');", self.js)


if __name__ == '__main__':
    unittest.main()
