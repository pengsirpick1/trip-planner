"""Regressions for real application wiring; deterministic model responses."""
import asyncio
import pytest
from app.application.travel_service import TravelApplicationService, RunRecord
from app.application.intent_service import IntentService
from app.application.replanning_service import ReplanningService
from app.domain import IntentUpdate, Task, TaskGraph, TaskResult
from app.evaluation.deterministic import DeterministicTripEvaluator
from app.persistence import SQLiteRunRepository
from app.runtime.graph_runtime import GraphRuntime
from app.runtime.registry import ExecutorRegistry
from tests.test_travel_processor import _make_pipeline, _INTENT_PLAN, _SIGNALS_COMPLEX, _DRAFT_COMPLEX
from tests.test_evaluation import _intent, _plan, _day
from tests.fakes import CannedStructuredLLM
from tests.test_mcp_tools import _FakeMCPClient, _make_adapter, _text_result


def model():
    return CannedStructuredLLM(structured_map={
        'IntentUpdate': {'extracted_fields': dict(_INTENT_PLAN)},
        'SemanticComplexitySignals': dict(_SIGNALS_COMPLEX), 'PlanDraft': dict(_DRAFT_COMPLEX)})


@pytest.mark.parametrize('kind', ['city', 'dates', 'daily_date', 'negated_must_have'])
async def test_evaluator_rejects_wrong_intent(kind):
    plan, intent = _plan(), _intent()
    if kind == 'city': plan.city = '上海'
    if kind == 'dates':
        plan.start_date = plan.end_date = '2027-01-01'
        plan.days = [_day(date='2027-01-01')]
    if kind == 'daily_date': plan.days[0].date = '2030-12-31'
    if kind == 'negated_must_have':
        intent.must_have = ['长城']
        plan.assumptions = ['没有安排长城']
    assert (await DeterministicTripEvaluator().evaluate(intent, plan)).status.value == 'fail'


async def test_conversation_persists_across_service_restart(workdir):
    class Parser:
        async def parse(self, message, state=None):
            fields = {'task_type': 'travel', 'planning_intent': True}
            if message == '北京': fields['destination'] = '北京'
            else:
                assert state.intent.destination == '北京'
                fields['duration_days'] = 3
            return IntentUpdate(extracted_fields=fields)
    repo = SQLiteRunRepository(workdir / 'conversation.sqlite')
    first_pipeline = _make_pipeline(model())
    first_pipeline.intent_service = IntentService(Parser())
    app = TravelApplicationService(repo, processor=first_pipeline)
    run = await app.create_run()
    result = await app.handle_message(run.run_id, '北京')
    assert result.kind.value == 'clarification'
    # New application/parser instance must load prior state from SQLite.
    second_pipeline = _make_pipeline(model())
    second_pipeline.intent_service = IntentService(Parser())
    second = TravelApplicationService(SQLiteRunRepository(repo.path), processor=second_pipeline)
    result = await second.handle_message(run.run_id, '三天')
    assert result.kind.value != 'clarification'
    saved = await second.get_run(run.run_id)
    assert saved.conversation['intent']['destination'] == '北京'
    assert saved.conversation['intent']['duration_days'] == 3


async def test_live_events_visible_before_react_completes(workdir):
    llm = model()
    original = llm.decide
    entered, release = asyncio.Event(), asyncio.Event()
    async def decide(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    llm.decide = decide
    app = TravelApplicationService(SQLiteRunRepository(workdir / 'events.sqlite'), processor=_make_pipeline(llm))
    run = await app.create_run()
    task = asyncio.create_task(app.handle_message(run.run_id, '北京三天'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            events = await app.list_events(run.run_id)
            if any(e.event == 'task_started' for e in events): break
            await asyncio.sleep(.01)
        assert any(e.event == 'task_started' for e in events)
        assert not task.done()
    finally:
        release.set()
        await task


async def test_overlapping_runs_keep_event_ownership():
    gates = {n: asyncio.Event() for n in ['A', 'B']}
    began = {n: asyncio.Event() for n in ['A', 'B']}
    class Executor:
        executor_id = 'test'
        async def execute(self, task, context):
            began[task.id].set()
            await gates[task.id].wait()
            return TaskResult(task_id=task.id, success=True)
    registry = ExecutorRegistry()
    registry.register(Executor(), {'r'})
    runtime = GraphRuntime(registry)
    events = {'A': [], 'B': []}
    async def run(n):
        return await runtime.run(TaskGraph(graph_id=n, tasks=[Task(id=n, goal=n, required_capabilities={'r'})]), n,
            event_callback=lambda e,p: events[n].append((e,p)))
    a = asyncio.create_task(run('A'))
    await began['A'].wait()
    b = asyncio.create_task(run('B'))
    await began['B'].wait()
    gates['A'].set()
    await a
    gates['B'].set()
    await b
    for n in events:
        assert [p['task_id'] for e,p in events[n] if e == 'task_succeeded'] == [n]


async def test_evaluation_fail_repairs_then_rechecks():
    pipeline = _make_pipeline(model())
    pipeline.evaluator = DeterministicTripEvaluator()
    class Synth:
        calls = 0
        async def synthesize(self, intent, state):
            self.calls += 1
            if self.calls == 1:
                plan = _plan()
                plan.days[0].attractions[0].evidence_ids = []
                return plan
            assert state.metadata['synthesis_repair_issues']
            plan = _plan(end_date='2026-10-03', days=[_day(day_index=i, date=f'2026-10-0{i+1}') for i in range(3)])
            for day in plan.days: day.attractions[0].evidence_ids = []
            return plan
    pipeline.synthesizer = Synth()
    pipeline.replanning_service = ReplanningService()
    result = await pipeline.process('北京三天', RunRecord(run_id='r'))
    assert result.kind.value == 'trip_plan'
    assert pipeline.synthesizer.calls == 2
    assert result.data['plan_revision'] == 2
    assert sum(e['event'] == 'evaluation_completed' for e in result.events) == 2


async def test_upstream_json_error_never_becomes_evidence():
    client = _FakeMCPClient({'maps_text_search': {}}, call_results={
        'maps_text_search': _text_result('{"status":"0","info":"INSUFFICIENT_ABROAD_BALANCE","infocode":"10044"}')})
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute('maps_text_search', {}, {'tool_call_id': '1'})
    assert not result.success
    assert result.metadata['diagnostic']['provider_code'] == '10044'
    assert result.metadata['diagnostic']['category'] == 'quota'
    assert result.metadata['diagnostic']['attribution'] == 'upstream_service'


async def test_mcp_error_flag_not_success():
    client = _FakeMCPClient({'maps_text_search': {}}, call_results={
        'maps_text_search': _text_result('{"message":"INVALID_PARAMS"}', is_error=True)})
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute('maps_text_search', {}, {'tool_call_id': '1'})
    assert not result.success
    assert result.metadata['diagnostic']['attribution'] == 'local_request'


def test_qps_limit_is_not_insufficient_balance():
    from app.tools.diagnostics import diagnose
    d = diagnose('Geocoding failed: CUQPS_HAS_EXCEEDED_THE_LIMIT')
    assert d['category'] == 'rate_limit'
    assert d['retryable']
    assert d['action_required'] is None


async def test_rate_limit_retries_once_and_records_reason():
    class Client(_FakeMCPClient):
        calls = 0
        async def call_tool(self, name, arguments):
            self.calls += 1
            return _text_result('{"error":"CUQPS_HAS_EXCEEDED_THE_LIMIT"}' if self.calls == 1 else '[]')
    client = Client({'maps_text_search': {}})
    adapter = _make_adapter(client)
    await adapter.start()
    result = await adapter.execute('maps_text_search', {}, {'tool_call_id': 'qps'})
    assert result.success
    assert client.calls == 2
    assert result.metadata['retry_errors'][0]['category'] == 'rate_limit'


async def test_mcp_reads_large_result_without_fake_timeout():
    import sys
    from app.tools.mcp.client import StdioMCPClient
    program = """
import json, sys
for line in sys.stdin:
    p=json.loads(line)
    if 'id' not in p: continue
    if p['method']=='initialize': result={'serverInfo':{'name':'test'}}
    else:
        print('debug:' + 'x'*100000, flush=True)
        result={'content':[{'type':'text','text':json.dumps({'data':'x'*100000})}]}
    print(json.dumps({'jsonrpc':'2.0','id':p['id'],'result':result}), flush=True)
"""
    client = StdioMCPClient([sys.executable, '-u', '-c', program], request_timeout_seconds=3)
    try:
        await client.start()
        result = await client.call_tool('large', {})
        assert len(result['content'][0]['text']) > 100000
    finally:
        await client.close()


async def test_production_combined_executor_and_deterministic_synthesis_handoff(workdir, monkeypatch):
    from app.bootstrap import build_travel_app
    from app.config import Settings
    from app.tools.mcp import amap
    client = _FakeMCPClient({'maps_text_search': {}, 'maps_geo': {}})
    adapter = _make_adapter(client, allowed={
        'maps_text_search': {'capability': 'place_research'},
        'maps_geo': {'capability': 'geospatial_reasoning'}})
    monkeypatch.setattr(amap, 'build_amap_adapter', lambda *a, **kw: adapter)
    app = build_travel_app(Settings(llm_api_key='test', llm_base_url='http://unused', llm_model='test',
        amap_api_key='test', db_path=str(workdir / 'bootstrap.sqlite'), auth_secret_key='closed-loop-test-secret-long-enough'), llm=model())
    await app.start()
    registry = app.processor.runtime.registry
    assert registry.resolve({'place_research', 'weather_research'}).executor_id == 'react-travel'
    assert registry.resolve({'itinerary_synthesis'}).executor_id == 'evidence-handoff'
    await app.close()
