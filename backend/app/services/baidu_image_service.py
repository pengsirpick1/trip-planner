"""百度图片搜索策略（opt-in，需要 `BAIDU_IMAGE_ENABLED=true`）。

- 直接用 image.baidu.com 的 acjson 端点，按 `word={query}` 返回 JSONP-风格文本
  （包裹形如 `jQuery…(…)` 或 `data:[…]`，前端官方反爬协议经常变化，因此做宽松解析）。
- 仅取首条结果的 `thumbURL` 字段（图片缩略图直链，AMap/HTML 都能直链）。
- 如未启用 / 失败 / 解析失败，返回 None，由 AttractionImageResolver 走下一个策略。
- 不需要任何 API key；但高频调用会被百度限速，建议作为兜底策略（Unsplash 优先）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = (
    "https://image.baidu.com/search/acjson"
    "?tn=resultjson&ie=utf-8&fp=detail&rn=1&fmt=json"
    "&word={word}"
)


class BaiduImageService:
    def __init__(
        self,
        enabled: bool = False,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout_seconds: float = 6.0,
    ):
        self.enabled = enabled
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def get_photo_url(self, query: str) -> Optional[str]:
        if not self.enabled:
            return None
        url = self.endpoint.replace("{word}", quote(query))
        try:
            resp = httpx.get(
                url,
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; trip-planner/1.0)",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            if resp.status_code != 200:
                log.warning("baidu image search HTTP %s", resp.status_code)
                return None
            text = resp.text.strip()
            data = _extract_json_array(text)
            if not data:
                return None
            for item in data:
                if not isinstance(item, dict):
                    continue
                # 常见字段：thumbURL / middleURL / objURL（百度不同时间格式名）
                for key in ("thumbURL", "middleURL", "objURL", "hoverUrl"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                        return candidate
        except Exception as error:  # 网络/解析失败都不致命，让策略链继续
            log.info("baidu image search failed: %s",error)
        return None


def _extract_json_array(text: str) -> Optional[list]:
    """百度返回通常形如 `jQuery123(…)`；剥回调拿到 JSON 数组。"""
    if not text:
        return None
    # 情况 1：纯 JSON
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # 情况 2：JSONP/JS 包装 `func(...)` 或 `var x = [...]`
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None