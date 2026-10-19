"""景点图片解析策略链测试（Unsplash → 百度 → 占位）。"""
from unittest.mock import MagicMock

import pytest

from app.services.attraction_image import (
    AttractionImageResolver,
    placeholder_data_url,
)
from app.services.baidu_image_service import BaiduImageService


def test_resolver_falls_back_to_placeholder_when_no_strategies_match():
    resolver = AttractionImageResolver(unsplash_access_key="")
    url, source = resolver.resolve("故宫", "北京")
    assert source == "placeholder"
    assert url.startswith("data:image/svg+xml;base64,")


def test_resolver_uses_unsplash_when_key_set(monkeypatch):
    fake = MagicMock()
    fake.get_photo_url.return_value = "https://images.unsplash.com/photo-1"
    monkeypatch.setattr(
        "app.services.attraction_image.get_unsplash_service",
        lambda: fake,
    )
    resolver = AttractionImageResolver(unsplash_access_key="any")
    url, source = resolver.resolve("故宫", "北京")
    assert source == "unsplash"
    assert url == "https://images.unsplash.com/photo-1"


def test_resolver_uses_baidu_when_unsplash_disabled(monkeypatch):
    baidu = BaiduImageService(enabled=True)
    baidu.get_photo_url = MagicMock(return_value="https://image.baidu.com/x.jpg")
    resolver = AttractionImageResolver(unsplash_access_key="", baidu_service=baidu)
    url, source = resolver.resolve("故宫", "北京")
    assert source == "baidu"
    assert "baidu" in url


def test_baidu_service_disabled_short_circuits():
    service = BaiduImageService(enabled=False)
    assert service.get_photo_url("anything") is None


def test_baidu_service_extracts_thumburl_from_jsonp(monkeypatch):
    baidu = BaiduImageService(enabled=True)

    class _Resp:
        status_code = 200
        text = 'jQuery1234567([{"thumbURL":"https://image.baidu.com/p.jpg","fromURL":"x"}])'

    monkeypatch.setattr("httpx.get", lambda *a, **kw: _Resp())
    assert baidu.get_photo_url("故宫") == "https://image.baidu.com/p.jpg"
