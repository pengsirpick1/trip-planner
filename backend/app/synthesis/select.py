"""行程候选“选择收敛”（Phase G+：让选点有依据、数量有上限）。

背景：Synthesizer 一次性可给出很多候选点，但没有规则约束——
本项目在此补一层确定性后处理，使“为什么选它”可解释：

1. 打分（仅作排序依据，非唯一来源）：
   - must_have 命中 名称/类别（+100）或 描述（+40）→ 必去项始终保留；
   - 偏好类别关键词映射（+15/命中）；
   - 有 evidence_ids（真证据）→ +10；
   - 有 rating（若将来接入）→ ≤ +15。
2. 每天数量上限：per_day_max（默认 4）。总量会顺带收敛。
3. 去重：先精确去重，再做“一个名称包含另一个”的别名合并（保留更长名）。
4. 被裁掉的候选写入 assumptions 说明（可追溯）。

注意：这是展示层收敛，不替代 ReAct 研究阶段的配额保护
（后者靠预算软预警 + “至多 5 个关键 POI 查 detail”提示词）。
"""

from __future__ import annotations

from typing import Optional

from ..models import schemas

# 偏好 → 关键词（命中 name/category/description）
PREFERENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "历史文化": ("历史", "文化", "博物馆", "古迹", "遗址", "古城", "古镇", "寺", "宫", "展览", "纪念", "祠"),
    "自然风光": ("自然", "风光", "风景", "山", "湖", "公园", "森林", "峡谷", "瀑布", "草原", "海滨", "岛", "湿地"),
    "美食": ("美食", "小吃", "菜", "食街", "夜市", "餐厅", "点心", "烧烤"),
    "购物": ("购物", "街", "商场", "市集", "市场", "步行街"),
    "艺术": ("艺术", "美术馆", "画廊", "剧院", "音乐", "演出"),
    "休闲": ("公园", "湖", "老街", "广场", "夜景", "步行街"),
}

DEFAULT_PER_DAY_MAX = 4


def score_attraction(
    attr: schemas.Attraction,
    must_have: list[str],
    preferences: list[str],
) -> float:
    text_name = (attr.name or "")
    text_cat = (attr.category or "")
    desc = (attr.description or "")
    score = 0.0
    for token in must_have:
        token = (token or "").strip().lower()
        if not token or len(token) < 2:
            continue
        if token in text_name.lower() or token in text_cat.lower():
            score += 100.0
        elif token in desc.lower()[:80]:
            score += 40.0
    for pref in preferences:
        for keyword in PREFERENCE_KEYWORDS.get(pref, ()):
            if keyword in text_name or keyword in text_cat or keyword in desc[:80]:
                score += 15.0
                break
    if attr.evidence_ids:
        score += 10.0
    if attr.rating:
        try:
            score += min(max(float(attr.rating), 0.0), 5.0) / 5.0 * 15.0
        except (TypeError, ValueError):
            pass
    return score


def _dedupe(attrs: list[schemas.Attraction]) -> list[schemas.Attraction]:
    """先精确去重；再做包含式别名合并（“故宫”与“故宫博物院”保留更长）。"""
    seen_exact: set[str] = set()
    unique: list[schemas.Attraction] = []
    for attr in attrs:
        name = (attr.name or "").strip()
        key = name.lower()
        if not key or key in seen_exact:
            continue
        seen_exact.add(key)
        unique.append(attr)

    merged: list[schemas.Attraction] = []
    for attr in unique:
        name = (attr.name or "").strip().lower()
        replaced = False
        for idx, kept in enumerate(merged):
            kept_name = (kept.name or "").strip().lower()
            if len(name) >= 2 and (name in kept_name or kept_name in name):
                merged[idx] = kept if len(kept_name) >= len(name) else attr
                replaced = True
                break
        if not replaced:
            merged.append(attr)
    return merged


def refine_attraction_day(
    attrs: list[schemas.Attraction],
    must_have: list[str] | None = None,
    preferences: list[str] | None = None,
    per_day_max: int = DEFAULT_PER_DAY_MAX,
) -> tuple[list[schemas.Attraction], list[str]]:
    """对一天的候选收敛：去重 → 打分 → 必去保留 → 截断。

    返回 (保留列表, 被裁掉的景点名列表)。
    """
    must_have = must_have or []
    preferences = preferences or []
    unique = _dedupe(attrs)
    if len(unique) <= per_day_max:
        return unique, []

    scored = sorted(
        ((score_attraction(a, must_have, preferences), a) for a in unique),
        key=lambda pair: pair[0],
        reverse=True,
    )
    must_ids: set[int] = set()
    for score, attr in scored:
        if score >= 100.0:
            must_ids.add(id(attr))
    kept: list[schemas.Attraction] = []
    dropped_names: list[str] = []
    for score, attr in scored:
        if id(attr) in must_ids or len(kept) < per_day_max:
            kept.append(attr)
        else:
            dropped_names.append(attr.name or attr.address or "未知名点")
    # 排序回稳定顺序（保留原顺序而非按分数），避免展示跳跃
    order = {id(a): i for i, a in enumerate(unique)}
    kept.sort(key=lambda a: order[id(a)])
    return kept, dropped_names
