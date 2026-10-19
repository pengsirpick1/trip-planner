"""行程合成（Phase D）：成功 TaskResults → 可展示、可追溯 TripPlan。

职责边界（审计 §10）：
- Synthesizer 不获得 ToolRegistry，不调用任何工具；只读输入 intent + TaskResults + 证据索引。
- 输出引用已有证据（evidence_ids 校验），缺证据的坐标/价格保持 None + assumptions，绝不补造。
- 表达/编排可单独修复（plan_revision），无需重跑研究。
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

from ..domain import GraphExecutionState, UserIntent
from ..domain.provenance import (
    build_evidence_index,
    build_source_summary,
    collect_coordinate_pool,
)
from ..domain.travel import SynthesizedPlan
from ..models import schemas
from ..providers import ProviderError
from ..services.attraction_image import AttractionImageResolver
from .select import DEFAULT_PER_DAY_MAX, refine_attraction_day


def _xhs_search_url(name: str, city: Optional[str] = None) -> str:
    """生成小红书站内搜索直达链接（不抓取内容，仅跳转）。

    URL 规则：www.xiaohongshu.com/search_result?keyword=<URL编码关键词>
    """
    keyword = f"{name} {city} 攻略" if city else f"{name} 攻略"
    return "https://www.xiaohongshu.com/search_result?keyword=" + quote(keyword.strip())


def _hotel_verification_links(name: str, city: str) -> dict[str, str]:
    """User-initiated search links only; no scraping, login, or booking action."""
    keyword = quote(f"{city} {name}".strip())
    return {
        "携程核验": f"https://m.ctrip.com/webapp/hotel/?keyword={keyword}",
        "美团核验": f"https://i.meituan.com/s/{keyword}",
        "飞猪核验": f"https://www.fliggy.com/search?q={keyword}",
    }


class TripPlanSynthesizer:
    def __init__(self, llm, image_resolver: AttractionImageResolver | None = None):
        self.llm = llm
        self.image_resolver = image_resolver

    async def synthesize(
        self,
        intent: UserIntent,
        state: GraphExecutionState,
    ) -> schemas.TripPlan:
        index = build_evidence_index(state)
        pool = collect_coordinate_pool(index)
        digest = self._build_digest(intent, state, index, pool)
        plan = await self._generate(digest)
        dto = self._to_dto(plan, index, pool, image_resolver=self.image_resolver, intent=intent)
        # 装配层回填：图版本 + 溯源摘要（不是模型输出，不由 LLM 控制）
        dto.graph_version = state.graph.version
        dto.sources = build_source_summary(index)
        return dto

    # ---- 输入装配 ----

    def _build_digest(
        self,
        intent: UserIntent,
        state: GraphExecutionState,
        index,
        pool,
    ) -> dict[str, Any]:
        tasks = []
        for task_id, result in state.task_results.items():
            if not result.success:
                continue
            evidence_ids = [
                eid for eid, entry in index.items() if entry.task_id == task_id
            ]
            tasks.append(
                {
                    "task_id": task_id,
                    "output": str(result.output or "")[:800],
                    "evidence_ids": evidence_ids,
                    "sources": sorted(
                        e.source
                        for eid, e in index.items()
                        if eid in evidence_ids and e.source
                    ),
                }
            )
        return {
            "intent": intent.model_dump(mode="json", exclude={"session_id", "raw_query", "metadata"}),
            "graph": {
                "graph_id": state.graph.graph_id,
                "version": state.graph.version,
            },
            "task_results": tasks,
            # 模型仅接收短证据摘要和引用 ID，不把完整工具响应再次塞进最终上下文。
            'evidence': [
                {
                    "evidence_id": entry.evidence_id,
                    "task_id": entry.task_id,
                    "source": entry.source,
                    "observed_at": entry.observed_at,
                    "tool_name": entry.tool_name,
                    "snippet": (entry.snippet or "")[:600],
                }
                for entry in index.values()
            ],
            'repair_issues': state.metadata.get('synthesis_repair_issues', []),
            "allowed_coordinate_pool": [
                {"location": loc, "evidence_id": meta["evidence_id"]}
                for loc, meta in pool.items()
            ],
            "synthesis_rules": (
                "1) 只生成可被任务证据支撑的行程；缺失信息用假设说明。"
                "2) 坐标只能取自 allowed_coordinate_pool（原样复制 location 字符串），"
                "池中没有的坐标一律置 null。"
                "3) 门票/费用若证据里没有，一律 null，不得编 0 或任意数。"
                "4) 天气只放证据里出现的日期与数值；没有就 weather_info=[]。"
                "5) 每个景点尽量填证据里的 poi_id/address；name/description 允许常识性补充但放入 assumptions。"
                "6) start_date/end_date 与 days[].date 必须一致。"
                "7) intent.must_have 和 constraints.revision_required_places 中的地点是本轮强制安排项；"
                "有对应证据时必须出现在 days[].attractions 中，不能仅写在建议或 assumptions。"
                "8) 用户未指定日期的新增地点：使用 allowed_coordinate_pool 中的坐标，与各日已有景点"
                "坐标簇比较，安排到最近的一天；为避免超量，替换该日最不匹配的非必去项目，"
                "并在 description 或 assumptions 说明“按邻近性自动安排/替换”。"
                "9) intent.avoid 中的地点不得出现在最终 attractions；若新增地点没有可靠证据，"
                "明确标记待确认，不得假装已安排。"
                "10) 修订时必须重新检查所有 days，而不是只在原日期插入一个地点：将旧证据与"
                "本轮补充证据作为全局候选池，按主题、地理邻近性、开放时间和每日时长重新平衡。"
                "11) 以每日日间约 8 小时为容量：大型博物馆/故宫类通常占 3-4 小时，"
                "中型景点约 1.5-2.5 小时，小型公园/街区/展馆约 0.5-1.5 小时。"
                "在证据足够时，每日优先安排 3-4 个短中型点，或 1 个大型核心点加 1-2 个邻近小点；"
                "不要为了凑数量编造地点或时长。"
            ),
        }

    async def _generate(self, digest: dict[str, Any]) -> SynthesizedPlan:
        schema_note = (
            "Return JSON only for SynthesizedPlan: {city, start_date, end_date, days:[{date,"
            "day_index, description, transportation, accommodation, hotel?, attractions:[{name,"
            "address, poi_id?, location_str, visit_duration?, description, category?,"
            "ticket_price?, source?, evidence_ids}], meals?:[{type,name,description,"
            "estimated_cost?}]}], weather_info?:[{date, day_weather?, night_weather?, day_temp?,"
            "night_temp?, kind?, source?, evidence_ids}], overall_suggestions, budget?:{...}, "
            "hotel_candidates?:[{name,address,rating?,distance_summary?,commute_summary,selection_reason,rejection_reason?,evidence_ids}], assumptions:[...]}. "
            "Hotel candidates must only use POI/route evidence and must never contain price, inventory, room type or booking claims. "
            "Omit optional keys when unknown rather than inventing values."
        )
        try:
            return await self.llm.generate_structured(
                [
                    {
                        "role": "system",
                        "content": "你是行程合成器：把已完成的真实研究证据整理成最终行程。"
                        + schema_note
                        + " 严格服从 digest.synthesis_rules；不要调用工具，不要重复研究。",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(digest, ensure_ascii=False),
                    },
                ],
                SynthesizedPlan,
            )
        except Exception as error:
            raise ProviderError(f"synthesizer structured output failure: {error}", getattr(error, 'diagnostic', None)) from error

    # ---- DTO 转换 + 校验 ----

    def _to_dto(
        self,
        plan: SynthesizedPlan,
        index,
        pool,
        image_resolver: AttractionImageResolver | None = None,
        intent: UserIntent | None = None,
    ) -> schemas.TripPlan:
        assumptions = list(plan.assumptions)

        def parse_location(location_str: Optional[str]) -> Optional[schemas.Location]:
            if not location_str:
                return None
            if location_str not in pool:
                assumptions.append(f"坐标 {location_str} 不在证据池内，已置空（防止虚构坐标）")
                return None
            try:
                lng_text, lat_text = location_str.split(",")
                return schemas.Location(longitude=float(lng_text), latitude=float(lat_text))
            except (ValueError, TypeError):
                assumptions.append(f"坐标格式无法解析：{location_str}")
                return None

        def valid_evidence_ids(evidence_ids: list[str]) -> list[str]:
            kept = [eid for eid in evidence_ids if eid in index]
            if len(kept) != len(evidence_ids):
                assumptions.append("部分 evidence 引用在证据索引中不存在，已剔除")
            return kept

        days = []
        for position, day in enumerate(plan.days):
            hotels = None
            if day.hotel is not None:
                hotels = schemas.Hotel(
                    name=day.hotel.name or "",
                    address=day.hotel.address or "",
                    location=parse_location(day.hotel.location_str),
                    price_range=day.hotel.price_range or "",
                    rating=day.hotel.rating or "",
                    estimated_cost=day.hotel.estimated_cost,
                )
            unknown_attraction_fields: list[str] = []
            attractions = []
            for attraction in day.attractions:
                fields: list[str] = []
                location = parse_location(attraction.location_str)
                if location is None and attraction.location_str:
                    fields.append("location")
                if attraction.ticket_price is None:
                    fields.append("ticket_price")
                evidence_ids = valid_evidence_ids(attraction.evidence_ids)
                if fields:
                    unknown_attraction_fields.append(attraction.name)

                image_url: Optional[str] = None
                photos: Optional[list] = None
                if image_resolver is not None:
                    url, source = image_resolver.resolve(attraction.name, plan.city)
                    image_url = url
                    photos = [url] if url else []
                    if source == "placeholder":
                        fields = [*fields, "image"]

                attractions.append(
                    schemas.Attraction(
                        name=attraction.name,
                        address=attraction.address or "",
                        location=location,
                        description=attraction.description or "",
                        category=attraction.category,
                        visit_duration=attraction.visit_duration,
                        poi_id=attraction.poi_id or "",
                        ticket_price=attraction.ticket_price,
                        image_url=image_url,
                        photos=photos,
                        source=attraction.source or (
                            index[evidence_ids[0]].provider if evidence_ids else None
                        ),
                        evidence_ids=evidence_ids,
                        unknown_fields=fields,
                        xhs_url=_xhs_search_url(attraction.name, plan.city),
                    )
                )
            # 候选收敛：每天最多保留 per_day_max 个；必去项恒保留；剔除项记入 assumptions
            kept, dropped = refine_attraction_day(
                attractions,
                must_have=(intent.must_have if intent else None),
                preferences=(intent.preferences if intent else None),
                per_day_max=DEFAULT_PER_DAY_MAX,
            )
            if dropped:
                assumptions.append(
                    f"第{position + 1}天从 {len(kept) + len(dropped)} 个候选中保留 {len(kept)} 个"
                    f"（未入选：{'、'.join(dropped[:6])}{' 等' if len(dropped) > 6 else ''}）"
                )
            attractions = kept
            days.append(
                schemas.DayPlan(
                    date=day.date,
                    day_index=position,  # 契约保证：与数组位置一致，忽略模型自报值
                    description=day.description or f"第{position + 1}天",
                    transportation=day.transportation or "待确认",
                    accommodation=day.accommodation or "待确认",
                    hotel=hotels,
                    attractions=attractions,
                    meals=[
                        schemas.Meal(
                            type=meal.type,
                            name=meal.name or "",
                            description=meal.description or None,
                            estimated_cost=meal.estimated_cost,
                        )
                        for meal in day.meals
                    ],
                )
            )

        weather_info = []
        for weather in plan.weather_info:
            weather_info.append(
                schemas.WeatherInfo(
                    date=weather.date,
                    day_weather=weather.day_weather or "",
                    night_weather=weather.night_weather or "",
                    day_temp=weather.day_temp,
                    night_temp=weather.night_temp,
                    kind=weather.kind,
                    source=weather.source,
                )
            )

        budget_dto = None
        if plan.budget is not None:
            total = plan.budget.total
            if total is None and all(
                value is not None
                for value in (
                    plan.budget.total_attractions,
                    plan.budget.total_hotels,
                    plan.budget.total_meals,
                    plan.budget.total_transportation,
                )
            ):
                total = (
                    (plan.budget.total_attractions or 0)
                    + (plan.budget.total_hotels or 0)
                    + (plan.budget.total_meals or 0)
                    + (plan.budget.total_transportation or 0)
                )
            if total is not None:
                budget_dto = schemas.Budget(
                    total_attractions=plan.budget.total_attractions,
                    total_hotels=plan.budget.total_hotels,
                    total_meals=plan.budget.total_meals,
                    total_transportation=plan.budget.total_transportation,
                    total=total,
                )
            else:
                assumptions.append("预算信息证据不足，未生成预算汇总（不编造 0）")
        else:
            assumptions.append("本次研究未提供可验证预算，预算部分为空")

        if not weather_info and plan.weather_info:
            assumptions.append("部分天气条目因证据不足被省略")

        hotel_candidates = []
        for candidate in plan.hotel_candidates:
            evidence_ids = valid_evidence_ids(candidate.evidence_ids)
            if not evidence_ids:
                assumptions.append(f"住宿候选 {candidate.name or '（未命名）'} 缺少可追溯证据，未展示")
                continue
            evidence = [index[eid] for eid in evidence_ids]
            observed_at = max((entry.observed_at for entry in evidence if entry.observed_at), default=None)
            if hasattr(observed_at, "isoformat"):
                observed_at = observed_at.isoformat()
            hotel_candidates.append(schemas.HotelCandidate(
                name=candidate.name, address=candidate.address, rating=candidate.rating,
                distance_summary=candidate.distance_summary,
                commute_summary=candidate.commute_summary, selection_reason=candidate.selection_reason,
                rejection_reason=candidate.rejection_reason, evidence_ids=evidence_ids,
                source_references=sorted({entry.source for entry in evidence if entry.source}),
                retrieved_at=observed_at,
                verification_links=_hotel_verification_links(candidate.name, plan.city),
            ))

        return schemas.TripPlan(
            city=plan.city,
            start_date=plan.start_date,
            end_date=plan.end_date,
            days=days,
            weather_info=weather_info,
            overall_suggestions=plan.overall_suggestions or "（研究结果见各日安排）",
            budget=budget_dto,
            assumptions=assumptions,
            plan_revision=1,
            graph_version=None,  # 装配层可回填 state.graph.version
            sources=None,
            hotel_candidates=hotel_candidates,
        )
