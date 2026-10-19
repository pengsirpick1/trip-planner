import asyncio, json, time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, UTC
from pathlib import Path
import httpx
from app.api.main import create_app
from app.config import Settings
MESSAGE='请为我规划一次 北京 的旅行。日期：2026-09-09 至 2026-09-10，共 2 天。交通方式：自驾。住宿偏好：舒适型酒店。偏好：艺术文化。补充要求：景点之间不要距离太远。'
async def main():
 folder=Path('evals/results')/('latency_measure_'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S')); folder.mkdir(parents=True,exist_ok=True)
 app=create_app(Settings(db_path=str((folder/'runs.sqlite').resolve())))
 report={'message':MESSAGE,'started_at':datetime.now(UTC).isoformat()}; started=time.perf_counter()
 try:
  async with asyncio.timeout(360), app.router.lifespan_context(app):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',timeout=360) as client:
    run=(await client.post('/api/travel/runs',json={})).json(); report['run_id']=run['run_id']
    response=await client.post(f"/api/travel/runs/{run['run_id']}/messages",json={'message':MESSAGE}); response.raise_for_status()
    report['record']=response.json(); report['trace']=(await client.get(f"/api/travel/runs/{run['run_id']}/trace")).json()
 except Exception as error:
  report['error_type']=type(error).__name__;report['error']=str(error)
 finally:
  report['elapsed_seconds']=round(time.perf_counter()-started,3);report['finished_at']=datetime.now(UTC).isoformat();(folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
asyncio.run(main())


