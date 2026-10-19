from __future__ import annotations
import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from ..domain import GraphExecutionState
from ..persistence.sqlalchemy_models import CheckpointRow

from ..errors import ConcurrentStateUpdateError
class ConcurrentCheckpointUpdateError(ConcurrentStateUpdateError):
    """Keeps legacy diagnostic construction while exposing a stable public code."""
    def __init__(self, detail: str = ""):
        super().__init__()
        self.internal_context = {"detail": detail} if detail else {}

class MySQLStateStore:
    """Latest snapshot per (user_id, run_id), guarded by optimistic version CAS."""
    def __init__(self, sessions: async_sessionmaker): self.sessions=sessions
    async def load(self, run_id: str, *, user_id: str):
        async with self.sessions() as s:
            row=await s.scalar(select(CheckpointRow).where(CheckpointRow.user_id==user_id,CheckpointRow.run_id==run_id))
            if row is None:return None
            state=GraphExecutionState.model_validate(row.state_json); state.metadata['_checkpoint_version']=row.version; return state
    async def save(self, state: GraphExecutionState, *, user_id: str, run_id: str | None=None):
        run_id=run_id or state.session_id; expected=state.metadata.get('_checkpoint_version')
        async with self.sessions() as s:
            async with s.begin():
                if expected is None:
                    existing=await s.scalar(select(CheckpointRow).where(CheckpointRow.user_id==user_id,CheckpointRow.run_id==run_id))
                    if existing is None:
                        row=CheckpointRow(id=uuid.uuid4().hex,user_id=user_id,run_id=run_id,graph_id=state.graph.graph_id,graph_version=state.graph.version,state_json=state.model_dump(mode='json'),version=0);s.add(row);state.metadata['_checkpoint_version']=0;return
                    expected=existing.version
                result=await s.execute(update(CheckpointRow).where(CheckpointRow.user_id==user_id,CheckpointRow.run_id==run_id,CheckpointRow.version==expected).values(graph_id=state.graph.graph_id,graph_version=state.graph.version,state_json=state.model_dump(mode='json'),version=expected+1))
                if result.rowcount!=1: raise ConcurrentCheckpointUpdateError(f'stale checkpoint {run_id}')
                state.metadata['_checkpoint_version']=expected+1
