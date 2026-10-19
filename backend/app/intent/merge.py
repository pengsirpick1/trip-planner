"""意图增量合并（迁自 travel_agent/agent/intent/merge.py）。"""

from copy import deepcopy

from ..domain import IntentUpdate, UserIntent

_LIST_FIELDS = {"preferences", "must_have", "avoid", "destination_candidates"}
_ALLOWED_FIELDS = set(UserIntent.model_fields) - {"session_id", "raw_query"}


def merge_intent(intent: UserIntent, update: IntentUpdate) -> UserIntent:
    """保留已知字段，除非 parser 显式提出替换/纠正。"""
    values = intent.model_dump()
    for field, proposed in update.extracted_fields.items():
        if field not in _ALLOWED_FIELDS:
            continue
        current = values.get(field)
        if field in _LIST_FIELDS and field not in update.correction_fields:
            values[field] = list(dict.fromkeys([*(current or []), *(proposed or [])]))
        elif current is None or field in update.correction_fields:
            values[field] = deepcopy(proposed)
        elif current == proposed:
            continue
        # 已显式信息在非 correction 冲突时保持（parser 不应覆盖）
    values["metadata"] = {
        **values.get("metadata", {}),
        "intent_assumptions": [
            *values.get("metadata", {}).get("intent_assumptions", []),
            *update.assumptions,
        ],
    }
    return UserIntent.model_validate(values)
