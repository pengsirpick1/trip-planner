"""Explicitly opt-in read-only MCP probes. Never prints credentials."""
import asyncio
import json
import os
import sys
from datetime import datetime, UTC
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.tools.mcp.amap import build_amap_adapter

async def main():
    if os.getenv('RUN_ONLINE_TESTS') != '1':
        raise SystemExit('Set RUN_ONLINE_TESTS=1')
    adapter = build_amap_adapter(Settings())
    results = []
    try:
        await adapter.start()
        for name, arguments in [
            ('maps_text_search', {'keywords': '故宫博物院', 'city': '北京'}),
            ('maps_geo', {'address': '故宫博物院', 'city': '北京'}),
            ('maps_direction_walking_by_address', {'origin_address': '故宫博物院', 'destination_address': '景山公园', 'origin_city': '北京', 'destination_city': '北京'}),
            ('maps_direction_transit_integrated_by_address', {'origin_address': '北京站', 'destination_address': '故宫博物院', 'origin_city': '北京', 'destination_city': '北京'}),
        ]:
            result = await adapter.execute(name, arguments, {'tool_call_id': name})
            results.append({'tool': name, 'arguments': arguments, 'result': result.model_dump(mode='json')})
            print(json.dumps({'tool': name, 'success': result.success, 'error': result.error, 'diagnostic': result.metadata.get('diagnostic')}, ensure_ascii=False), flush=True)
    finally:
        await adapter.close()
    folder = Path(__file__).parent / 'results' / ('diagnostics_' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S'))
    folder.mkdir(parents=True)
    (folder / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(folder / 'result.json'))

if __name__ == '__main__': asyncio.run(main())
