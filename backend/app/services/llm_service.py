"""LLM服务模块（[DEPRECATED] legacy 专用）"""

import warnings

from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式) —— [DEPRECATED] 仅旧固定四 Agent 路径（ENABLE_LEGACY_PLANNER）使用。
    Phase B 起新链路统一使用 providers/ 下的 OpenAICompatible adapter，
    其配置来自 backend/.env 的 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL（见 config.py）。
    
    Returns:
        HelloAgentsLLM实例
    """
    global _llm_instance
    warnings.warn(
        "get_llm()/HelloAgentsLLM 已弃用，仅 legacy 路径使用；新链路请用 providers adapter",
        DeprecationWarning,
        stacklevel=2,
    )
    
    if _llm_instance is None:
        settings = get_settings()
        
        # HelloAgentsLLM会自动从环境变量读取配置
        # 包括OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL等
        _llm_instance = HelloAgentsLLM()
        
        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None

