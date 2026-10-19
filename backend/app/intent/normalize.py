"""意图字段归一化（迁自 travel_agent/agent/intent/normalize.py，Phase B 修复固定年份）。"""

import re
from datetime import date, datetime

from ..domain import IntentUpdate


class IntentNormalizer:
    """把 parser 产出的松散字段整理为 canonical 形态。

    year 修复（审计 G2）：不再硬编码 2026；缺年份时用调用方传入的 year，
    否则回退当前自然年（按真实时钟，便于测试注入）。
    """

    def normalize(
        self,
        update: IntentUpdate,
        year: int | None = None,
        now: datetime | None = None,
    ) -> IntentUpdate:
        fields = dict(update.extracted_fields)
        ambiguities = list(update.ambiguities)
        assumptions = update.assumptions
        if isinstance(assumptions, dict):
            assumptions = [f"{key}={value}" for key, value in sorted(assumptions.items())]
        elif isinstance(assumptions, str):
            assumptions = [assumptions]
        elif assumptions is None:
            assumptions = []

        for key in ("preferences", "must_have", "avoid", "destination_candidates"):
            if isinstance(fields.get(key), str):
                fields[key] = [fields[key]]
        # must_have 防"整句入列"：切分后只保留具体短词（时间/动词/数量/方位等干扰子串剔除）
        if "must_have" in fields and isinstance(fields.get("must_have"), list):
            stop_tokens = (
                "想去看看", "想要看看", "一定要去", "必须去", "一定去", "想去", "还要去",
                "上午", "下午", "晚上", "中午", "早上", "当天", "第二天",
                "安排", "参观", "游览", "游玩", "需要", "要看", "还有",
                "附近步行可达的两个景点", "步行可达的两个景点", "附近的两个景点",
                "的两个景点", "两个景点", "附近景点", "周边景点", "个景点", "景点",
                "附近", "周边", "步行可达", "可达", "两个", "三个",
            )
            cleaned: list[str] = []
            for raw in fields["must_have"]:
                parts = re.split(r"[，,、。；;：:和及与或/]|\s+", str(raw))
                for part in parts:
                    fragment = part.strip()
                    for stop in stop_tokens:
                        fragment = fragment.replace(stop, "")
                    fragment = fragment.strip(" ，,的")
                    if len(fragment) >= 2 and fragment not in cleaned:
                        cleaned.append(fragment)
            fields["must_have"] = cleaned if cleaned else []

        fallback_year = year if year is not None else (now or datetime.now()).year
        for key in ("start_date", "end_date"):
            value = fields.get(key)
            if isinstance(value, str):
                match = re.fullmatch(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})[日号]?", value.strip())
                if match:
                    try:
                        fields[key] = date(
                            int(match.group(1) or fallback_year),
                            int(match.group(2)),
                            int(match.group(3)),
                        )
                        continue
                    except ValueError:
                        ambiguities.append(f"invalid {key}: {value}")
                else:
                    ambiguities.append(f"unrecognized {key}: {value}")
                # 无法解析的日期不能留成字符串进入 merge（否则下游 pydantic date 解析失败）
                fields.pop(key, None)

        return update.model_copy(
            update={
                "extracted_fields": fields,
                "ambiguities": ambiguities,
                "assumptions": assumptions,
            }
        )
