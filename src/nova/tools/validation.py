"""参数 JSON Schema 验证器（对照 dsh core/tools/json-schema.ts 的受支持子集）。

dsh 只支持一个刻意收窄的 JSON Schema 子集：
    type / oneOf / properties / required / additionalProperties / items /
    enum / const + 注解（description/title/default/examples）
不支持 min*/max*/pattern/format 等任何其它关键字——收窄是为了让 schema
本身可静态校验、可类型投影。Nova 照抄同一子集与同一 violation 文案，
保证两边对同一份 schema 产出同一份错误。

验证器刻意写成纯函数（schema 与 value 都不信任），消息格式与 dsh 对齐：
    missing required property "arguments.path"
    "arguments.items[0].status" must be one of: ...
    "arguments" is not a declared property (additionalProperties: false)
"""

from __future__ import annotations

import copy
import json
from typing import Any

_SCALAR_TYPES = ("string", "number", "integer", "boolean", "null")
_ALL_TYPES = ("object", "array", *_SCALAR_TYPES)
_CONSTRAINT_KEYWORDS = {
    "type",
    "oneOf",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
}
_ANNOTATION_KEYWORDS = {"description", "title", "default", "examples"}
# type 关键字与约束关键字的合法搭配（dsh allowedFor 的镜像）
_ALLOWED_FOR_TYPE = {
    "properties": ("object",),
    "required": ("object",),
    "additionalProperties": ("object",),
    "items": ("array",),
    "enum": _ALL_TYPES,
    "const": _ALL_TYPES,
}


class InvalidToolSchemaError(Exception):
    """工具作者声明的 schema 本身违反受支持子集。"""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


def assert_supported_schema(node: Any, path: str = "schema", _seen: tuple[int, ...] = ()) -> None:
    """静态校验 schema 节点：非法关键字/搭配在注册期就报错，不留到运行期。"""
    if not isinstance(node, dict):
        raise InvalidToolSchemaError([f"{path} must be a schema object"])
    if id(node) in _seen:
        raise InvalidToolSchemaError([f"{path} is circular"])
    seen = (*_seen, id(node))

    has_type = "type" in node
    has_one_of = "oneOf" in node
    if has_type and has_one_of:
        raise InvalidToolSchemaError([f"{path} cannot declare both type and oneOf"])
    for keyword in ("properties", "required", "additionalProperties", "items", "enum", "const"):
        if keyword in node and not (has_type or has_one_of):
            raise InvalidToolSchemaError([f"{path}.{keyword} requires type or oneOf"])

    for key, value in node.items():
        if key in _ANNOTATION_KEYWORDS:
            continue
        if key == "oneOf":
            if not isinstance(value, list) or len(value) < 2:
                raise InvalidToolSchemaError([f"{path}.oneOf must be an array of at least two schemas"])
            for index, branch in enumerate(value):
                assert_supported_schema(branch, f"{path}.oneOf[{index}]", seen)
            continue
        if key not in _CONSTRAINT_KEYWORDS:
            raise InvalidToolSchemaError(
                [f"{path}.{key} is not a supported keyword (subset: type/oneOf/properties/required/additionalProperties/items/enum/const + annotations)"]
            )
        if key == "description" or key == "title":
            if not isinstance(value, str):
                raise InvalidToolSchemaError([f"{path}.{key} must be a string"])

    if has_one_of:
        # oneOf 的兄弟约束关键字在 dsh 中被拒绝（不允许组合歧义）
        for key in _CONSTRAINT_KEYWORDS - {"oneOf"}:
            if key in node:
                raise InvalidToolSchemaError([f"{path}.{key} is not supported beside oneOf"])
        return

    node_type = node.get("type")
    if isinstance(node_type, list):
        raise InvalidToolSchemaError([f"{path}.type must be a single type"])
    if node_type not in _ALL_TYPES:
        raise InvalidToolSchemaError([f"{path}.type must be one of {', '.join(_ALL_TYPES)}"])
    for keyword, types in _ALLOWED_FOR_TYPE.items():
        if keyword in node and node_type not in types:
            raise InvalidToolSchemaError([f"{path}.{keyword} is not supported on type \"{node_type}\""])

    if node_type == "object":
        if "additionalProperties" in node and not isinstance(node["additionalProperties"], bool):
            raise InvalidToolSchemaError([f"{path}.additionalProperties must be a boolean"])
        if "required" in node:
            required = node["required"]
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise InvalidToolSchemaError([f"{path}.required must be an array of strings"])
        if "properties" in node:
            properties = node["properties"]
            if not isinstance(properties, dict):
                raise InvalidToolSchemaError([f"{path}.properties must be an object of schemas"])
            for key, child in properties.items():
                assert_supported_schema(child, f"{path}.properties.{key}", seen)
            for key in node.get("required") or []:
                if key not in properties:
                    raise InvalidToolSchemaError([f'{path}.required names "{key}" which is not in properties'])
    if node_type == "array" and "items" in node:
        assert_supported_schema(node["items"], f"{path}.items", seen)


def _is_lossless_json(value: Any) -> bool:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected in ("number", "integer"):
        # bool 是 int 的子类，必须显式排除（dsh 的 isJsonNumber 同样不接受布尔）
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if expected == "integer":
            return float(value).is_integer()
        return True
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return False


def _scalar_type_of(value: Any) -> str:
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return "unknown"


def validate_value(schema: dict[str, Any], value: Any, path: str = "arguments") -> list[str]:
    """按受支持子集验证 value；返回 path 限定的 violation 列表（空 = 通过）。

    刻意与 dsh 的 violation 文案逐字对齐（见模块 docstring 示例），这样
    同一份 schema 在两边的模型可见错误完全一致。
    """
    violations: list[str] = []

    if "oneOf" in schema:
        branches = schema["oneOf"]
        matched = sum(1 for branch in branches if not validate_value(branch, value, path))
        if matched != 1:
            violations.append(f'"{path}" must match exactly one oneOf branch (matched {matched})')
        return violations

    if "const" in schema and value != schema["const"]:
        violations.append(f'"{path}" must be {json.dumps(schema["const"], ensure_ascii=False)}')
        return violations

    node_type = schema.get("type")
    if node_type is None:
        # 无 type 节点 = 注解性 schema（dsh 的 json 类型投影）
        return [] if _is_lossless_json(value) else [f'"{path}" must be lossless JSON data']

    if "enum" in schema:
        enum = schema["enum"]
        type_name = _scalar_type_of(value)
        if type_name not in ("string", "number", "boolean", "null") or value not in enum:
            violations.append(f'"{path}" must be one of: {", ".join(json.dumps(item, ensure_ascii=False) for item in enum)}')
        if violations:
            return violations

    if node_type in _SCALAR_TYPES:
        if not _type_matches(value, node_type):
            violations.append(f'"{path}" must be a {node_type}')
        return violations

    if node_type == "object":
        if not isinstance(value, dict):
            return [f'"{path}" must be an object']
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value or value[key] is None:
                violations.append(f'missing required property "{path}.{key}"')
        for key, child_schema in properties.items():
            if key not in value or value[key] is None:
                continue
            violations.extend(validate_value(child_schema, value[key], f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    violations.append(f'"{path}.{key}" is not a declared property (additionalProperties: false)')
        return violations

    if node_type == "array":
        if not isinstance(value, list):
            return [f'"{path}" must be an array']
        items_schema = schema.get("items")
        if items_schema is None:
            return [] if _is_lossless_json(value) else [f'"{path}" must be a dense lossless JSON array']
        for index, item in enumerate(value):
            violations.extend(validate_value(items_schema, item, f"{path}[{index}]"))
        return violations

    return violations


def validate_arguments(schema: dict[str, Any] | None, arguments: dict[str, Any]) -> list[str]:
    """验证顶层参数对象；schema 缺失（动态 MCP 工具）时返回空（不设防）。"""
    if not schema:
        return []
    return validate_value(schema, arguments)


def snapshot_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """无损 JSON 快照边界（dsh snapshotJsonValue 的参数侧镜像）。

    模型参数与 hook 改写都过这一关：往返序列化同时完成「深拷贝隔离」
    （后续 handler 改不动调用方的原 dict）与「非无损值提前爆炸」（NaN、
    非字符串键等——dsh 的 JsonValue 要求对象键必须是字符串，Python 的
    json.dumps 会把整数键静默转成字符串，那是有损变换，必须显式拒绝）。
    失败时抛 ValueError，由执行器转成 INVALID_ARGS。
    """
    if any(not isinstance(key, str) for key in arguments):
        raise ValueError("工具参数对象的键必须是字符串（无损 JSON 边界）")
    try:
        encoded = json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"工具参数不是无损 JSON：{exc}") from exc
    return copy.deepcopy(json.loads(encoded))
