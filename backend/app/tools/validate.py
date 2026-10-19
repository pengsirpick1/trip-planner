"""轻量 JSON Schema 校验（供纯 JSON Schema 工具参数验证，审计 §9 缺口修复）。

范围：AMap/一般 MCP 工具 schema 常用子集 —— type / required / properties(嵌套) /
items / enum。不做完整 JSON Schema 草案（预算外复杂约束返回“无法校验”由调用方决策）。
策略：只拒绝确定非法（类型/缺必填/越枚举），绝不放行明显错误参数进入 Provider。
"""

from __future__ import annotations

from typing import Any

_JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


def _type_errors(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    expected = schema.get("type")
    if expected not in _JSON_TYPES:
        return []  # 无 type/未知 type → 不校验
    errors: list[str] = []

    def mismatch() -> str:
        return f"{path or '参数'}: 类型应为 {expected}，实际 {type(value).__name__}"

    if expected == "string":
        if not isinstance(value, str):
            errors.append(mismatch())
        elif "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: 长度不足 minLength={schema['minLength']}")
    elif expected in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(mismatch())
        elif expected == "integer" and isinstance(value, float) and not value.is_integer():
            errors.append(mismatch())
    elif expected == "boolean":
        if not isinstance(value, bool):
            errors.append(mismatch())
    elif expected == "array":
        if not isinstance(value, list):
            errors.append(mismatch())
        elif isinstance(schema.get("items"), dict):
            items_schema = schema["items"]
            for index, item in enumerate(value):
                errors.extend(_type_errors(item, items_schema, f"{path}[{index}]"))
    elif expected == "object":
        if not isinstance(value, dict):
            errors.append(mismatch())
        else:
            enum = schema.get("enum")
            if enum is not None and value not in enum:
                errors.append(f"{path}: 取值不在允许枚举内: {value!r}")
    return errors


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """返回参数违规列表；空列表表示（就本校验器范围而言）合法。"""
    if not isinstance(schema, dict):
        return []
    errors: list[str] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for field in required:
            if field not in arguments:
                errors.append(f"缺少必填参数: {field}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return errors
    for field, value in arguments.items():
        field_schema = properties.get(field)
        if not isinstance(field_schema, dict):
            continue
        errors.extend(_type_errors(value, field_schema, path=field))
        enum = field_schema.get("enum")
        if enum is not None and isinstance(value, (str, int, float, bool)) and value not in enum:
            errors.append(f"{field}: 取值不在允许枚举内: {value!r}")
    return errors
