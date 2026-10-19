"""Provider 协议（自 travel_agent/agent/llm.py 拆分，供 providers/openai_compatible.py 使用）。"""

import json
from typing import Protocol

from pydantic import BaseModel, ValidationError


from ..errors import ProviderError


class ToolCallingLLM(Protocol):
    async def decide(self, task, observations, *, messages, tools, dependency_results): ...


class StructuredLLM(Protocol):
    async def generate_structured(self, messages, response_model: type[BaseModel]): ...


def parse_structured_content(content: str, response_model: type[BaseModel]):
    """先解包再校验：避免全默认字段模型把 {ModelName:{...}} 包装误当空更新接受。"""
    parsed = json.loads(content)
    if isinstance(parsed, dict) and set(parsed) == {response_model.__name__}:
        parsed = parsed[response_model.__name__]
    return response_model.model_validate(parsed)


def is_validation_error(error: Exception) -> bool:
    return isinstance(error, (json.JSONDecodeError, ValidationError))
