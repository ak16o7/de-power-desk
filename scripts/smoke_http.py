"""Read-only HTTP deployment smoke test; credentials are never printed."""
import argparse
import base64
import json
import os
import urllib.parse
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--base-url', default='http://127.0.0.1:8000')
p.add_argument('--day', required=True)
p.add_argument('--user', default=os.getenv('DASHBOARD_USERNAME', ''))
p.add_argument('--password', default=os.getenv('DASHBOARD_PASSWORD', ''))
a = p.parse_args()
headers = {}
if a.user and a.password:
    headers['Authorization'] = 'Basic ' + base64.b64encode(f'{a.user}:{a.password}'.encode()).decode()

for route in ['health', 'api/renewables', 'api/load', 'api/borders', 'api/outages', 'api/balancing']:
    url = a.base_url.rstrip('/') + '/' + route + ('' if route == 'health' else '?' + urllib.parse.urlencode({'day': a.day}))
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=240) as response:
        data = json.load(response)
    if route == 'health':
        assert data['version'].startswith('5.') and data['configured'], data
        if not data.get('auth_enabled'):
            print('WARNING: dashboard is public - set DASHBOARD_USERNAME/DASHBOARD_PASSWORD in Render')
        print('health OK', data['version'], 'netztransparenz:', data.get('netztransparenz'))
    else:
        assert 'series' in data and 'kpi' in data, (route, data)
        print(route, 'OK', data.get('quality', {}).get('state'), json.dumps(data.get('sources', data.get('coverage', {})), ensure_ascii=True)[:300])
