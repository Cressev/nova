from __future__ import annotations

import os
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..models import ChatMessage, ChatRole
from ..tools.workspace import TOOL_SPECS, ToolSpec


class ProviderError(RuntimeError):
    """模型调用失败时抛出，API 层会把它转换成可读错误消息。"""


@dataclass(frozen=True)
class ProviderDecision:
    content: str
    tool_calls: list[dict[str, Any]]


class BigModelProvider:
    _BUILTIN_CHAT_TOOL_NAMES: set[str] = set()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "BIGMODEL_API_KEY",
        api_key_file: Path | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BIGMODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")).rstrip("/")
        self.model = model or os.getenv("BIGMODEL_MODEL", "glm-4.7")
        self.api_key_env = api_key_env
        self._runtime_api_key = self._load_runtime_api_key(api_key_file)
        self._client_factory = client_factory

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def api_key_source(self) -> str | None:
        if self._runtime_api_key:
            return "runtime"
        if os.getenv(self.api_key_env):
            return "environment"
        return None

    def set_runtime_api_key(self, api_key: str, *, api_key_file: Path | None = None) -> None:
        value = api_key.strip()
        self._runtime_api_key = value or None
        if api_key_file is not None:
            self._write_runtime_api_key(api_key_file, self._runtime_api_key)

    def clear_runtime_api_key(self, *, persist: bool = True, api_key_file: Path | None = None) -> None:
        self._runtime_api_key = None
        if persist and api_key_file is not None:
            self._write_runtime_api_key(api_key_file, None)

    def _api_key(self) -> str | None:
        return self._runtime_api_key or os.getenv(self.api_key_env) or None

    def api_key_for_tools(self) -> str | None:
        # 只在进程内传递给本地工具，API 响应和日志都不回显明文。
        return self._api_key()

    def _load_runtime_api_key(self, api_key_file: Path | None) -> str | None:
        if api_key_file is None or not api_key_file.exists():
            return None
        try:
            payload = json.loads(api_key_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = payload.get(self.api_key_env) if isinstance(payload, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _write_runtime_api_key(self, api_key_file: Path, api_key: str | None) -> None:
        api_key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(api_key_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if api_key:
            payload[self.api_key_env] = api_key
        else:
            payload.pop(self.api_key_env, None)
        api_key_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _assistant_message_text(self, message: dict) -> str:
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
        return content if isinstance(content, str) else ""

    def _stream_delta_text(self, delta: dict) -> str | None:
        content = self._read_attr(delta, "content")
        return content if isinstance(content, str) and content else None

    def _stream_reasoning_text(self, delta: dict) -> str | None:
        """GLM-4.7 等思考模型的推理增量（reasoning_content）。

        思考发生在正文/工具调用之前；单独成流下发，前端渲染为
        Think 披露行（dsh ReasoningRow），不与正文混排。
        """
        for key in ("reasoning_content", "reasoning"):
            value = self._read_attr(delta, key)
            if isinstance(value, str) and value:
                return value
        return None

    def _openai_client(self, api_key: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(api_key)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError("缺少 openai SDK，请安装依赖后重启 Nova。") from exc
        return AsyncOpenAI(base_url=self.base_url, api_key=api_key, timeout=60)

    def openai_tool_schemas(self, tool_specs: dict[str, ToolSpec] | None = None) -> list[dict[str, Any]]:
        specs = tool_specs or TOOL_SPECS
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": self._tool_parameters_schema(spec),
                },
            }
            for spec in specs.values()
            if spec.model_visible and spec.name not in self._BUILTIN_CHAT_TOOL_NAMES
        ]

    def chat_tool_schemas(
        self,
        tool_specs: dict[str, ToolSpec] | None = None,
        *,
        enable_web_search: bool = False,
        enable_web_fetch: bool = True,
        web_search_only: bool = False,
    ) -> list[dict[str, Any]]:
        specs = tool_specs or TOOL_SPECS
        if web_search_only:
            specs = {name: spec for name, spec in specs.items() if spec.name == "web_search"}
            return self.openai_tool_schemas(specs) if enable_web_search else []
        if not enable_web_fetch:
            specs = {name: spec for name, spec in specs.items() if spec.name != "web_fetch"}
        if not enable_web_search:
            specs = {name: spec for name, spec in specs.items() if spec.name != "web_search"}
        tools = self.openai_tool_schemas(specs)
        return tools

    def bigmodel_web_search_tool(self) -> dict[str, Any]:
        return {
            "type": "web_search",
            "web_search": {
                "enable": True,
                "search_engine": "search_pro",
                "search_result": True,
                "search_prompt": (
                    "你是 Nova 的联网检索助手。请基于网络搜索结果 {search_result} 回答用户，"
                    "优先使用最新、可核验的信息；涉及事实、新闻、价格、版本、政策时必须引用来源或日期。"
                ),
                "count": 5,
                "search_recency_filter": "noLimit",
                "content_size": "high",
            },
        }

    def _tool_parameters_schema(self, spec: ToolSpec) -> dict[str, Any]:
        """模型可见参数 schema（dsh schemas() 白名单语义：所见即所验）。

        有真实契约的工具直接发契约本体 + annotation 属性——模型看到的
        required/enum/additionalProperties 与执行器验证的完全一致，而不是
        从示例 dict 猜出来的宽松 schema。动态 MCP 工具无契约时退回示例
        推断。annotation 在执行器验证前就被弹出，不影响契约闭合性。
        """
        if spec.json_schema:
            return {
                "type": "object",
                "properties": {**(spec.json_schema.get("properties") or {})},
                "required": [*(spec.json_schema.get("required") or [])],
                "additionalProperties": False,
            }
        properties = {
            key: self._json_schema_from_example(value)
            for key, value in spec.schema.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": [],
            "additionalProperties": True,
        }

    def _json_schema_from_example(self, value: Any) -> dict[str, Any]:
        if isinstance(value, bool):
            return {"type": "boolean", "default": value}
        if isinstance(value, int):
            return {"type": "integer", "default": value}
        if isinstance(value, float):
            return {"type": "number", "default": value}
        if isinstance(value, str):
            return {"type": "string", "description": value}
        if isinstance(value, list):
            item_schema = self._json_schema_from_example(value[0]) if value else {}
            return {"type": "array", "items": item_schema}
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {
                    str(key): self._json_schema_from_example(item)
                    for key, item in value.items()
                },
                "additionalProperties": True,
            }
        return {"description": str(value)}

    async def complete_with_tools(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ProviderDecision:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        client = self._openai_client(api_key)
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=self._payload_messages(messages),
                temperature=0.3,
                tools=tools or self.openai_tool_schemas(),
                tool_choice="auto",
                stream=False,
            )
        except Exception as exc:
            raise ProviderError(f"模型工具决策调用失败：{exc}") from exc

        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError("模型返回结构异常，无法提取 assistant 消息。") from exc
        return ProviderDecision(
            content=self._message_text(message),
            tool_calls=self._message_tool_calls(message),
        )

    def _payload_messages(self, messages: list[ChatMessage]) -> list[dict[str, str]]:
        # 只把对话所需字段发给模型，内部错误消息、工具 trace 和调试信息不进入模型上下文。
        return [
            {"role": message.role.value, "content": message.content}
            for message in messages
            if message.role in {ChatRole.SYSTEM, ChatRole.USER, ChatRole.ASSISTANT}
        ]

    def _message_text(self, message: Any) -> str:
        if isinstance(message, dict):
            return self._assistant_message_text(message)
        content = getattr(message, "content", None)
        return content if isinstance(content, str) else ""

    def _message_tool_calls(self, message: Any) -> list[dict[str, Any]]:
        raw_tool_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        if not raw_tool_calls:
            return []
        normalized: list[dict[str, Any]] = []
        for raw_call in raw_tool_calls:
            call_id = self._read_attr(raw_call, "id") or ""
            function = self._read_attr(raw_call, "function") or {}
            name = str(self._read_attr(function, "name") or "").strip()
            arguments = self._read_attr(function, "arguments") or {}
            if isinstance(arguments, str):
                try:
                    parsed_arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_arguments = {}
            else:
                parsed_arguments = arguments
            normalized.append(
                {
                    "id": str(call_id),
                    "type": self._read_attr(raw_call, "type") or "function",
                    "tool": name,
                    "arguments": parsed_arguments if isinstance(parsed_arguments, dict) else {},
                }
            )
        return [item for item in normalized if item["tool"]]

    def _read_attr(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    async def stream_with_tools(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict]:
        """流式工具决策（dsh 逐字输出语义）。

        文本增量以 {"type": "delta", "text": ...} 即时下发；
        原生 tool_calls 分片跨块聚合（index 对位拼接 name/arguments），
        流结束后以 {"type": "decision", "content", "tool_calls"} 收口，
        tool_calls 形状与 complete_with_tools 保持一致。
        """
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        client = self._openai_client(api_key)
        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=self._payload_messages(messages),
                temperature=0.3,
                tools=tools or self.openai_tool_schemas(),
                tool_choice="auto",
                stream=True,
            )
        except Exception as exc:
            raise ProviderError(f"模型流式工具决策调用失败：{exc}") from exc

        parts: list[str] = []
        fragments: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            choices = self._read_attr(chunk, "choices") or []
            if not choices:
                continue
            delta = self._read_attr(choices[0], "delta") or {}
            reasoning = self._stream_reasoning_text(delta)
            if reasoning:
                yield {"type": "reasoning_delta", "text": reasoning}
            content = self._stream_delta_text(delta)
            if content:
                parts.append(content)
                yield {"type": "delta", "text": content}
            for piece in self._read_attr(delta, "tool_calls") or []:
                index = self._read_attr(piece, "index")
                index = 0 if index is None else int(index)
                entry = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                piece_id = self._read_attr(piece, "id")
                if piece_id:
                    entry["id"] = str(piece_id)
                function = self._read_attr(piece, "function") or {}
                name = self._read_attr(function, "name")
                if name:
                    entry["name"] += str(name)
                arguments = self._read_attr(function, "arguments")
                if arguments:
                    entry["arguments"] += str(arguments)

        normalized: list[dict[str, Any]] = []
        for index in sorted(fragments):
            entry = fragments[index]
            name = entry["name"].strip()
            if not name:
                continue
            try:
                parsed_arguments = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                parsed_arguments = {}
            normalized.append(
                {
                    "id": entry["id"],
                    "type": "function",
                    "tool": name,
                    "arguments": parsed_arguments if isinstance(parsed_arguments, dict) else {},
                }
            )
        yield {"type": "decision", "content": "".join(parts), "tool_calls": normalized}

    async def complete(self, messages: list[ChatMessage]) -> str:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        client = self._openai_client(api_key)
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=self._payload_messages(messages),
                temperature=0.3,
                stream=False,
            )
        except Exception as exc:
            raise ProviderError(f"模型调用失败：{exc}") from exc
        try:
            return self._message_text(response.choices[0].message)
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError("GLM 返回结构异常，无法提取 assistant 消息。") from exc

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        client = self._openai_client(api_key)
        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=self._payload_messages(messages),
                temperature=0.3,
                stream=True,
            )
        except Exception as exc:
            raise ProviderError(f"模型流式调用失败：{exc}") from exc
        async for chunk in stream:
            choices = self._read_attr(chunk, "choices") or []
            if not choices:
                continue
            delta = self._read_attr(choices[0], "delta") or {}
            reasoning = self._stream_reasoning_text(delta)
            if reasoning:
                yield {"type": "reasoning_delta", "text": reasoning}
            content = self._stream_delta_text(delta)
            if content:
                yield content
