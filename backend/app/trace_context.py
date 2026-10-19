from __future__ import annotations
import uuid
from contextvars import ContextVar
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
def get_trace_id() -> str | None: return _trace_id.get()
def set_trace_id(value: str): return _trace_id.set(value)
def reset_trace_id(token): _trace_id.reset(token)
def new_trace_id() -> str: return uuid.uuid4().hex
