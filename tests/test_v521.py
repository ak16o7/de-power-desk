"""v5.2.1: favicon and footer."""
import unittest

from fastapi.testclient import TestClient

from app import main as m


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(m.app)

    def test_page_links_icons_and_shows_copyright(self):
        html = self.c.get('/').text
        self.assertIn('rel="icon" href="/static/favicon.svg', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('Kaan Akdere · DE Power Desk', html)
        self.assertIn(f'/static/app.js?v={m.VERSION}', html)
        self.assertIn(f'/static/style.css?v={m.VERSION}', html)

    def test_icon_files_are_served(self):
        for path, ctype in (('/favicon.ico', 'image/x-icon'), ('/static/favicon.svg?v=1', 'image/svg+xml'),
                            ('/static/favicon-32.png?v=1', 'image/png'), ('/static/apple-touch-icon.png?v=1', 'image/png')):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.headers['content-type'].startswith(ctype), (path, r.headers['content-type']))


if __name__ == '__main__':
    unittest.main()
