"""景点图片解析（策略链：Unsplash → 百度图片 → 本地 SVG 占位）。

返回 (image_url_or_data_url, source)，
source ∈ {'unsplash','baidu','placeholder'}。
占位永远是兜底（零网络、不会 404），并被合成器写入 `unknown_fields=["image"]` 告诉前端。
"""

from __future__ import annotations

import hashlib
import html
from typing import Iterable, Optional, Tuple

from .baidu_image_service import BaiduImageService
from .unsplash_service import get_unsplash_service


class AttractionImageResolver:
    def __init__(
        self,
        unsplash_access_key: str = "",
        baidu_service: BaiduImageService | None = None,
    ):
        self.unsplash_access_key = unsplash_access_key
        self.baidu_service = baidu_service

    def resolve(
        self,
        name: str,
        city: Optional[str] = None,
    ) -> Tuple[Optional[str], str]:
        query = f"{name} {city or ''}".strip()
        if self.unsplash_access_key:
            try:
                url = get_unsplash_service().get_photo_url(query)
                if url:
                    return url, "unsplash"
            except Exception:
                pass
        if self.baidu_service and self.baidu_service.enabled:
            try:
                url = self.baidu_service.get_photo_url(query)
                if url:
                    return url, "baidu"
            except Exception:
                pass
        return placeholder_data_url(name, city), "placeholder"


def placeholder_data_url(name: str, city: Optional[str] = None) -> str:
    """生成确定性 SVG 占位（data URL），避免图片 404 与对真实图源的过度承诺。"""
    seed = f"{city or ''}|{name}".encode("utf-8")
    h = hashlib.sha1(seed).digest()  # sha1 仅用于稳定取色，并非安全用途
    hue1 = h[0] % 360
    hue2 = (h[0] + 40) % 360
    sat = 55 + (h[1] % 20)  # 55-74%
    light1 = 55 + (h[2] % 15)  # 55-69%
    light2 = 65 + (h[3] % 10)
    title = html.escape(name)
    sub = html.escape(city or "无坐标/未取图")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 320'>"
        f"<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>"
        f"<stop offset='0%' stop-color='hsl({hue1},{sat}%,{light1}%)'/>"
        f"<stop offset='100%' stop-color='hsl({hue2},{sat}%,{light2}%)'/>"
        f"</linearGradient></defs>"
        f"<rect width='600' height='320' fill='url(#g)'/>"
        f"<text x='50%' y='46%' text-anchor='middle' dominant-baseline='middle'"
        f" font-family='Helvetica,Arial,sans-serif' font-size='34' font-weight='600'"
        f" fill='rgba(255,255,255,0.95)'>{title}</text>"
        f"<text x='50%' y='58%' text-anchor='middle' dominant-baseline='middle'"
        f" font-family='Helvetica,Arial,sans-serif' font-size='16'"
        f" fill='rgba(255,255,255,0.75)'>{sub} · 本地占位（无真实图片来源）</text>"
        f"</svg>"
    )
    from base64 import b64encode

    return "data:image/svg+xml;base64," + b64encode(svg.encode("utf-8")).decode("ascii")