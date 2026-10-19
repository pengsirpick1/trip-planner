"""MySQL implementations. Every resource query is tenant-scoped."""
from __future__ import annotations
import uuid
from datetime import UTC, datetime
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from .sqlalchemy_models import QuotaLedgerRow, RunEventRow, RunRow, UserMemoryRow, UserQuotaAccountRow, UserRow
from .user_repository import MemoryRecord, UserRecord
from ..application.travel_service import RunEvent, RunRecord, RunStatus, ResultKind

def _user(row): return UserRecord(id=row.id, username=row.username, password_hash=row.password_hash, created_at=row.created_at, last_login_at=row.last_login_at)
def _memory(row): return MemoryRecord(id=row.id, user_id=row.user_id, memory_key=row.memory_key, category=row.category, value=row.value_json, summary=row.summary, confidence=row.confidence, importance=row.importance, source_run_id=row.source_run_id, created_at=row.created_at, updated_at=row.updated_at)

class MySQLUserRepository:
    def __init__(self, sessions: async_sessionmaker, *, default_new_user_points: int = 0):
        self.sessions = sessions; self.default_new_user_points = default_new_user_points
    async def create_user(self, username, password_hash):
        now=datetime.now(UTC); row=UserRow(id=uuid.uuid4().hex, username=username, password_hash=password_hash, status='ACTIVE', created_at=now, updated_at=now)
        try:
            async with self.sessions() as s:
                async with s.begin():
                    s.add(row)
                    await s.flush()
                    points = self.default_new_user_points
                    s.add(UserQuotaAccountRow(user_id=row.id,total_points=points,reserved_points=0,consumed_points=0,version=0,created_at=now,updated_at=now))
                    s.add(QuotaLedgerRow(id=uuid.uuid4().hex,user_id=row.id,reservation_id=None,llm_call_id=None,event_type='CREDIT',total_delta=points,reserved_delta=0,consumed_delta=0,idempotency_key=f'quota:{row.id}:initial-credit',total_after=points,reserved_after=0,consumed_after=0,reason_code='INITIAL_CREDIT',metadata_json=None,created_at=now))
        except IntegrityError as error:
            raise ValueError('用户名已存在') from error
        return _user(row)
    async def get_by_username(self, username):
        async with self.sessions() as s: return (_user(x) if (x:=await s.scalar(select(UserRow).where(UserRow.username==username))) else None)
    async def get_by_id(self, user_id):
        async with self.sessions() as s: return (_user(x) if (x:=await s.scalar(select(UserRow).where(UserRow.id==user_id, UserRow.status=='ACTIVE'))) else None)
    async def touch_login(self, user_id):
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(UserRow).where(UserRow.id==user_id)); row.last_login_at=row.updated_at=datetime.now(UTC)
    async def upsert_memory(self,user_id,memory_key,category,value,summary,confidence,importance,source_run_id):
        now=datetime.now(UTC)
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(UserMemoryRow).where(UserMemoryRow.user_id==user_id,UserMemoryRow.memory_key==memory_key))
                if row is None: row=UserMemoryRow(id=uuid.uuid4().hex,user_id=user_id,memory_key=memory_key,category=category,value_json=value,summary=summary,confidence=confidence,importance=importance,source_run_id=source_run_id,created_at=now,updated_at=now); s.add(row)
                else: row.category,row.value_json,row.summary,row.confidence,row.importance,row.source_run_id,row.updated_at=category,value,summary,confidence,importance,source_run_id,now
            return _memory(row)
    async def list_memories(self,user_id,limit=30):
        async with self.sessions() as s: return [_memory(x) for x in (await s.scalars(select(UserMemoryRow).where(UserMemoryRow.user_id==user_id).order_by(UserMemoryRow.importance.desc(),UserMemoryRow.updated_at.desc()).limit(limit))).all()]
    async def delete_memory(self,user_id,memory_id):
        async with self.sessions() as s:
            async with s.begin(): return (await s.execute(delete(UserMemoryRow).where(UserMemoryRow.id==memory_id,UserMemoryRow.user_id==user_id))).rowcount>0

class MySQLRunRepository:
    def __init__(self, sessions: async_sessionmaker): self.sessions=sessions
    @staticmethod
    def _record(row, events=[]): return RunRecord(run_id=row.id,user_id=row.user_id,session_id=row.session_id,status=RunStatus(row.status),kind=ResultKind(row.kind) if row.kind else None,message=row.message,error_code=row.error_code,data=row.data,conversation=row.conversation,created_at=row.created_at,updated_at=row.updated_at,events=events)
    async def create(self,record):
        async with self.sessions() as s:
            async with s.begin():
                s.add(RunRow(id=record.run_id,user_id=record.user_id,session_id=record.session_id,status=record.status.value,kind=record.kind.value if record.kind else None,message=record.message,error_code=record.error_code,data=record.data,conversation=record.conversation,created_at=record.created_at,updated_at=record.updated_at))
                await s.flush()
                s.add_all([RunEventRow(run_id=record.run_id,seq=e.seq,event=e.event,payload=e.payload,created_at=e.timestamp) for e in record.events])
    async def update(self,record):
        async with self.sessions() as s:
            async with s.begin():
                row=await s.scalar(select(RunRow).where(RunRow.id==record.run_id,RunRow.user_id==record.user_id));
                if row is None: return
                for k,v in {'session_id':record.session_id,'status':record.status.value,'kind':record.kind.value if record.kind else None,'message':record.message,'error_code':record.error_code,'data':record.data,'conversation':record.conversation,'updated_at':record.updated_at}.items(): setattr(row,k,v)
                await s.execute(delete(RunEventRow).where(RunEventRow.run_id==record.run_id)); s.add_all([RunEventRow(run_id=record.run_id,seq=e.seq,event=e.event,payload=e.payload,created_at=e.timestamp) for e in record.events])
    async def get_for_user(self,user_id,run_id):
        async with self.sessions() as s:
            row=await s.scalar(select(RunRow).where(RunRow.id==run_id,RunRow.user_id==user_id));
            if not row:return None
            es=(await s.scalars(select(RunEventRow).where(RunEventRow.run_id==run_id).order_by(RunEventRow.seq))).all()
            return self._record(row,[RunEvent(seq=e.seq,event=e.event,payload=e.payload,timestamp=e.created_at) for e in es])
    async def get(self,run_id): raise RuntimeError('tenant-scoped get_for_user is required in MySQL production')
    async def list_events_for_user(self,user_id,run_id):
        record=await self.get_for_user(user_id,run_id); return record.events if record else []
    async def list_events(self,run_id): raise RuntimeError('tenant-scoped list_events_for_user is required in MySQL production')
    def close(self): pass
