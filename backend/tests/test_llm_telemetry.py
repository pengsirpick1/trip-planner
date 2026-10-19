from __future__ import annotations
import asyncio
from app.telemetry import LLMCallContext, TelemetryLLMGateway
class _Repo:
    def __init__(self): self.rows = []
    async def record(self, *args): self.rows.append(args)
class _Provider:
    async def decide_with_telemetry(self, task, observations, **kwargs):
        await asyncio.sleep(0)
        return {"final": task, "usage": {"total_tokens": 7}}, [{"kind":"provider", "status":"SUCCEEDED", "usage":{"total_tokens":7}, "provider_request_id":task, "latency_ms":1}]
def test_gateway_keeps_concurrent_calls_and_contexts_isolated():
    async def run():
        repo = _Repo(); gateway = TelemetryLLMGateway(_Provider(), repo)
        async def invoke(user, task):
            with gateway.scope(LLMCallContext(stage="REACT", user_id=user, run_id="r-" + user, task_id=task)):
                return await gateway.decide(task, [])
        first, second = await asyncio.gather(invoke("u1", "t1"), invoke("u2", "t2"))
        assert first["usage"]["total_tokens"] == second["usage"]["total_tokens"] == 7
        assert len(repo.rows) == 2
        assert {row[1].user_id: row[1].task_id for row in repo.rows} == {"u1":"t1", "u2":"t2"}
    asyncio.run(run())
