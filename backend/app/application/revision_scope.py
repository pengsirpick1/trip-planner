"""Deterministic impact routing for user feedback on an active itinerary."""

from __future__ import annotations

import re

from ..domain import TaskGraph


def select_revision_targets(graph: TaskGraph, message: str) -> list[str]:
    """Map a user edit to research domains; synthesis is added by downstream scope."""
    text = message.lower()
    hotel = ("酒店", "住宿", "房间", "民宿", "hotel")
    route = ("路线", "距离", "交通", "通勤", "停车", "自驾")
    # “还想去北航/想加一个地方” often contains no category word.  During a
    # revision (as opposed to a question) those verbs are a reliable signal that
    # new POI evidence is required.
    place = ("景点", "博物馆", "公园", "第", "亲子", "餐", "不要去", "替换", "换成", "想去", "还想去", "去")
    selected: list[str] = []
    for task in graph.tasks:
        caps = task.required_capabilities
        if any(word in text for word in hotel) and "accommodation_research" in caps:
            selected.append(task.id)
        if any(word in text for word in route) and "geospatial_reasoning" in caps:
            selected.append(task.id)
        if any(word in text for word in place) and "place_research" in caps:
            selected.append(task.id)
    # Budget and general preference changes usually need no new evidence; rerun
    # only the deterministic handoff/final synthesis downstream node.
    if not selected:
        selected = [task.id for task in graph.tasks if "itinerary_synthesis" in task.required_capabilities]
    return selected or [task.id for task in graph.tasks]


def extract_requested_places(message: str) -> list[str]:
    """Conservative fallback for explicit Chinese “go to X” revision language.

    The LLM parser remains authoritative for rich intent extraction.  This keeps
    a plainly named POI such as “还想去北航玩一下” from being lost when its
    wording is too conversational for a structured field extraction.
    """
    matches = re.findall(r"(?:还想|想|希望|要)?去([\u4e00-\u9fffA-Za-z0-9·]{2,16}?)(?:玩一下|游玩|参观|逛逛|看看|玩|逛|一下|$)", message)
    ignored = {"北京", "这里", "那里", "一个地方"}
    return list(dict.fromkeys(item for item in matches if item not in ignored))
