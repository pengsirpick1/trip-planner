"""候选收敛（Phase G+）：去重/必去保留/每天上限/打分排序。"""

from app.models import schemas
from app.synthesis.select import refine_attraction_day, score_attraction


def _attr(name: str, category: str = "景点", evidence: bool = False, desc: str = "") -> schemas.Attraction:
    return schemas.Attraction(
        name=name,
        address=f"{name}地址",
        description=desc or f"{name}介绍",
        category=category,
        evidence_ids=["e1"] if evidence else [],
    )


def test_must_have_kept_and_others_truncated_to_limit():
    must = ["长城"]
    cands = [_attr("长城", "古迹", evidence=True)] + [_attr(f"备选{i}", f"类别{i}") for i in range(1, 8)]
    kept, dropped = refine_attraction_day(cands, must_have=must, preferences=[], per_day_max=4)
    assert len(kept) == 4
    assert any(a.name == "长城" for a in kept)
    assert len(dropped) == 4


def test_alias_containment_merge():
    cands = [_attr("故宫博物院", "博物馆"), _attr("故宫", "博物馆")]
    kept, dropped = refine_attraction_day(cands, must_have=[], preferences=[], per_day_max=4)
    names = [a.name for a in kept]
    assert "故宫博物院" in names and "故宫" not in names
    assert len(kept) == 1


def test_preference_keyword_boosts_but_still_respects_limit():
    cands = [
        _attr("自然1", "森林公园"),
        _attr("自然2", "湿地公园"),
        _attr("自然3", "山岳景区"),
        _attr("人文1", "博物馆"),
        _attr("人文2", "古城"),
        _attr("人文3", "历史街区"),
        _attr("无关1", "商业街"),
        _attr("无关2", "夜市"),
    ]
    kept, dropped = refine_attraction_day(cands, must_have=[], preferences=["自然风光"], per_day_max=4)
    assert len(kept) == 4
    kept_names = {a.name for a in kept}
    assert "自然1" in kept_names  # 命中的优先于无关项


def test_score_is_stable_and_non_negative():
    a1 = _attr("故宫", "博物馆", desc="明清皇家宫殿与历史展览", evidence=True)
    a2 = _attr("普通商业广场", "购物", desc="购物中心")
    s1 = score_attraction(a1, must_have=["故宫"], preferences=["历史文化", "艺术"])
    s2 = score_attraction(a2, must_have=[], preferences=[])
    assert s1 >= 100
    assert s2 >= 0
    assert s1 > s2
