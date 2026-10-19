"""Read-only upstream cross-check when MCP loses codes or times out."""
import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, UTC
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings

def main():
    if os.getenv('RUN_ONLINE_TESTS') != '1': raise SystemExit('Set RUN_ONLINE_TESTS=1')
    cfg = Settings()
    observations = []
    def fetch(endpoint, params):
        url = 'https://restapi.amap.com' + endpoint + '?' + urllib.parse.urlencode({**params, 'key': cfg.amap_api_key})
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = json.loads(response.read())
                observations.append({'endpoint': endpoint, 'params': params, 'http_status': response.status,
                    'status': body.get('status'), 'infocode': body.get('infocode'), 'info': body.get('info'),
                    'route_available': bool(body.get('route'))})
                return body
        except Exception as error:
            observations.append({'endpoint': endpoint, 'params': params, 'error_type': type(error).__name__,
                                 'http_status': getattr(error, 'code', None)})
            return {}
    origin = fetch('/v3/geocode/geo', {'address': '北京站', 'city': '北京'})
    destination = fetch('/v3/geocode/geo', {'address': '故宫博物院', 'city': '北京'})
    if origin.get('geocodes') and destination.get('geocodes'):
        fetch('/v3/direction/transit/integrated', {'origin': origin['geocodes'][0]['location'],
            'destination': destination['geocodes'][0]['location'], 'city': '北京', 'cityd': '北京'})
    path = Path(__file__).parent / 'results' / ('upstream_' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S') + '.json')
    path.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(observations, ensure_ascii=False, indent=2))
    print(str(path))

if __name__ == '__main__': main()
