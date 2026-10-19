"""Optional Redis accelerators. MySQL remains the source of truth."""
from __future__ import annotations
import json, time
from datetime import date, datetime


def _json_default(value):
    """Serialize cache snapshots, never domain state mutations.

    Pydantic records carry timezone-aware datetimes.  Redis is used for
    disposable read snapshots, so encode those values as ISO-8601 and let the
    Pydantic model at the boundary restore their declared types on a cache hit.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        return dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(value).__name__}")

class InMemoryRedis:
    def __init__(self): self.values={}; self.windows={}; self.counters={}
    async def get_json(self,key):
        item=self.values.get(key); return item[0] if item and item[1]>time.monotonic() else None
    async def set_json(self,key,value,ttl): self.values[key]=(value,time.monotonic()+ttl)
    async def invalidate(self,key): self.values.pop(key,None)
    async def allow(self,key,limit,window):
        now=time.monotonic(); items=[x for x in self.windows.get(key,[]) if x>now-window]
        if len(items)>=limit: self.windows[key]=items; return False
        items.append(now); self.windows[key]=items; return True
    async def increment(self,key,ttl): self.counters[key]=self.counters.get(key,0)+1; return self.counters[key]
    async def close(self): pass
    async def get_text(self,key):
        value=await self.get_json(key); return value
    async def set_text(self,key,value,ttl): await self.set_json(key,value,ttl)
    async def claim(self,key,ttl):
        if await self.get_json(key) is not None: return False
        await self.set_json(key,"LOCK",ttl); return True

class RedisStore:
    def __init__(self, client): self.client=client
    @classmethod
    async def connect(cls,url):
        from redis.asyncio import from_url
        client=from_url(url,decode_responses=True); await client.ping(); return cls(client)
    async def get_json(self,key):
        value=await self.client.get(key); return json.loads(value) if value else None
    async def set_json(self,key,value,ttl):
        await self.client.set(key, json.dumps(value, default=_json_default), ex=ttl)
    async def invalidate(self,key): await self.client.delete(key)
    async def allow(self,key,limit,window):
        bucket=int(time.time()//window); key=f"{key}:{bucket}"; count=await self.client.incr(key)
        if count==1: await self.client.expire(key,window+1)
        return count<=limit
    async def increment(self,key,ttl):
        count=await self.client.incr(key)
        if count==1: await self.client.expire(key,ttl)
        return count
    async def close(self): await self.client.aclose()
    async def get_text(self,key): return await self.client.get(key)
    async def set_text(self,key,value,ttl): await self.client.set(key,value,ex=ttl)
    async def claim(self,key,ttl): return bool(await self.client.set(key,"LOCK",ex=ttl,nx=True))

class CachedQuotaRepository:
    def __init__(self, source, cache, ttl=15): self.source,self.cache,self.ttl=source,cache,ttl
    async def get_account_for_user(self,user_id):
        key=f"quota:account:{user_id}"; value=await self.cache.get_json(key)
        if value is not None: return value
        value=await self.source.get_account_for_user(user_id)
        if value is not None: await self.cache.set_json(key,value,self.ttl)
        return value
    async def get_ledger_for_user(self,user_id,limit=50): return await self.source.get_ledger_for_user(user_id,limit)
