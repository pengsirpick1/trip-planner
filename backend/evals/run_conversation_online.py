"""Real HTTP API conversation, durable state and typed outcome assertions."""
import asyncio
import json
import os
import sys
from datetime import datetime, UTC
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.api.main import create_app
from app.config import Settings

async def main():
    if os.getenv('RUN_ONLINE_TESTS') != '1': raise SystemExit('Set RUN_ONLINE_TESTS=1')
    folder = Path(__file__).parent / 'results' / ('conversation_' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S'))
    folder.mkdir(parents=True)
    report = {'turns': [], 'passed': False, 'started_at': datetime.now(UTC).isoformat()}
    cfg = Settings(db_path=str((folder / 'runs.sqlite').resolve()))
    web = create_app(cfg)
    try:
        async with asyncio.timeout(300), web.router.lifespan_context(web):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://test', timeout=300) as client:
                created = (await client.post('/api/travel/runs', json={})).json()
                run_id = created['run_id']
                messages = ['我想去北京旅游，必须去故宫博物院，请帮我安排旅行计划。',
                    '2026年10月1日当天，一共一天，预算500元，公共交通出行。上午故宫博物院，下午安排故宫附近步行可达的两个景点。不需要住宿。请用真实地图信息；查不到的门票价格或交通信息请注明待确认。']
                for message in messages:
                    response = await client.post(f'/api/travel/runs/{run_id}/messages', json={'message': message})
                    response.raise_for_status()
                    record = response.json()
                    report['turns'].append({'message': message, 'record': record})
                    (folder / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({'kind': record['kind'], 'error_code': record.get('error_code'), 'message': record['message'],
                                      'evaluation': (record.get('data') or {}).get('evaluation'),
                                      'diagnostics': (record.get('data') or {}).get('diagnostics')}, ensure_ascii=False), flush=True)
                final = report['turns'][-1]['record']
                report['passed'] = (report['turns'][0]['record']['kind'] == 'clarification' and
                    final['kind'] == 'trip_plan' and final['data']['city'].removesuffix('市') == '北京' and
                    final['data']['start_date'] == '2026-10-01' and len(final['data']['days']) == 1 and
                    bool(final['data'].get('sources')) and final['data']['evaluation']['status'] != 'fail')
    except Exception as error:
        report['error_type'] = type(error).__name__
        report['error'] = str(error)
    finally:
        report['finished_at'] = datetime.now(UTC).isoformat()
        (folder / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'result_file': str(folder / 'result.json')}, ensure_ascii=False))
    return report['passed']

if __name__ == '__main__': raise SystemExit(0 if asyncio.run(main()) else 1)
