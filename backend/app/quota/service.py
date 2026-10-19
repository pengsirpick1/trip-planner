from __future__ import annotations
from dataclasses import dataclass
from ..errors import QuotaInsufficientError, QuotaReconciliationRequiredError
class QuotaInsufficient(QuotaInsufficientError):
    def __init__(self, *_): super().__init__()
class QuotaReconciliationRequired(QuotaReconciliationRequiredError):
    def __init__(self, *_): super().__init__()
@dataclass(frozen=True)
class Reservation: id:str; user_id:str; llm_call_id:str; estimated_points:int; reserved_points:int; status:str; pricing_snapshot:dict
class QuotaService:
    def __init__(self, repository, pricing): self.repository,self.pricing=repository,pricing
    async def reserve(self, user_id, llm_call_id, provider, model, estimate):
        return await self.repository.reserve(user_id,llm_call_id,provider,model,estimate,self.pricing.get(provider,model))
    async def settle(self, llm_call_id, usage): return await self.repository.settle(llm_call_id, usage)
    async def release(self, llm_call_id, reason='NO_COST'): return await self.repository.release(llm_call_id,reason)
    async def mark_pending_reconciliation(self,llm_call_id,reason): return await self.repository.mark_pending(llm_call_id,reason)
