"""POI 相关 API（Phase C：detail/search 改接 GeoFacade；photo 保留 Unsplash 可选服务）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...config import get_settings
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/poi", tags=["POI"])


def _geo(request: Request):
    geo = getattr(request.app.state, "geo", None)
    if geo is None:
        raise HTTPException(
            status_code=503,
            detail="AMap 地图工具未启用：请在 backend/.env 配置 AMAP_API_KEY",
        )
    return geo


@router.get("/detail/{poi_id}", summary="获取POI详情")
async def get_poi_detail(poi_id: str, request: Request):
    result = await _geo(request).get_poi_detail(poi_id)
    return _envelope(result)


@router.get("/search", summary="搜索POI")
async def search_poi(keywords: str, city: str = "北京", request: Request = None):
    result = await _geo(request).search_poi(keywords, city)
    return _envelope(result)


@router.get("/photo", summary="获取景点图片（Unsplash，可选）")
async def get_attraction_photo(name: str):
    """Unsplash 图片服务（可选）：未配置 key 或无结果时明确返回空，不走假图片。"""
    settings = get_settings()
    if not settings.unsplash_access_key:
        return {
            "success": False,
            "message": "UNSPLASH_ACCESS_KEY 未配置，图片服务不可用",
            "data": None,
            "kind": "failed",
            "error_code": "UNSPLASH_NOT_CONFIGURED",
        }
    try:
        unsplash = get_unsplash_service()
        photo_url = unsplash.get_photo_url(f"{name} China landmark")
        if not photo_url:
            photo_url = unsplash.get_photo_url(name)
        return {
            "success": True,
            "message": "获取图片成功" if photo_url else "未找到图片（真实空结果）",
            "data": {"name": name, "photo_url": photo_url},
            "kind": "tool_result",
            "provider": "unsplash",
        }
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"获取图片失败: {error}")


def _envelope(result: dict):
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "调用失败"))
    return {
        "success": True,
        "message": result.get("message"),
        "data": result.get("data"),
        "kind": "tool_result",
        "provider": result.get("provider"),
        "source": result.get("source"),
    }
