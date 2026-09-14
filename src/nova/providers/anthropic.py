"""Anthropic Messages API 提供方（协议对齐：与 BigModelProvider 同形接口）。

调用面（complete_with_tools / stream_with_tools / complete / stream、
api-key 文件持久化、usage 记录）与 BigModelProvider 保持鸭子类型一致，
runtime/routes 无需感知协议差异；差异全部封装在请求/响应转换层：

- 请求：system 消息提升为顶层 system 参数；tools 用 {name, description,
  input_schema}；header x-api-key + anthropic-version。
- 响应：content blocks（text/tool_use/thinking）→ Nova 归一化
  {content, tool_calls:[{id,type,tool,arguments}]}；usage.input/output_tokens
  → prompt/completion_tokens（token-meter 同口径）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx

from .bigmodel import ProviderError
from ..tools.workspace import TOOL_SPECS, ToolSpec

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    _BUILTIN_CHAT_TOOL_NAMES: set[str] = set()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_key_file: Path | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.api_key_env = api_key_env
        self._runtime_api_key = self._load_runtime_api_key(api_key_file)
        self._http_client_factory = http_client_factory
        # token-meter（与 BigModelProvider 同口径）
        self.last_usage: dict[str, int] | None = None
        self.session_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # ---- 凭据（与 BigModelProvider 同语义：运行时 key > 环境变量；文件持久化） ----

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
        return self._api_key()

    def _load_runtime_api_key(self, api_key_file: Path | None) -> str | None:
        if api_key_file is None or not api_key_file.exists():
            return None
        try:
            payload = json.loads(api_key_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        value = str(payload.get("api_key") or "").strip()
        return value or None

    def _write_runtime_api_key(self, api_key_file: Path, value: str | None) -> None:
        try:
            api_key_file.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if api_key_file.exists():
                try:
                    loaded = json.loads(api_key_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        existing = loaded
                except (OSError, json.JSONDecodeError):
                    existing = {}
            existing["api_key"] = value or ""
            api_key_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---- 工具 schema（Anthropic 形状：name/description/input_schema） ----

    def openai_tool_schemas(self, tool_specs: dict[str, ToolSpec] | None = None) -> list[dict[str, Any]]:
        """Anthropic 格式的工具清单（方法名保留 openai_* 以匹配调用方）。"""
        from .bigmodel import BigModelProvider

        specs = tool_specs or TOOL_SPECS
        helper = BigModelProvider.__new__(BigModelProvider)  # 只借 schema 转换，不触发凭据加载
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": helper._tool_parameters_schema(spec),
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
            specs = {name: spec for name, spec in specs.items() if name == "web_search"}
            return self.openai_tool_schemas(specs) if enable_web_search else []
        if not enable_web_fetch:
            specs = {name: spec for name, spec in specs.items() if name != "web_fetch"}
        if not enable_web_search:
            specs = {name: spec for name, spec in specs.items() if name != "web_search"}
        return self.openai_tool_schemas(specs)

    # ---- HTTP ----

    def _client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

    def _endpoint(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _require_api_key(self) -> str:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )
        return api_key

    def _request_body(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None,
        stream: bool,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        system_parts = [m.content for m in messages if getattr(m.role, "value", m.role) == "system"]
        convo = [
            {"role": getattr(m.role, "value", m.role), "content": str(m.content or "")}
            for m in messages
            if getattr(m.role, "value", m.role) in {"user", "assistant"}
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": convo,
            "stream": stream,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = tools
        return body

    # ---- usage 记录（token-meter 同口径） ----

    def _record_usage(self, input_tokens: Any, output_tokens: Any) -> dict[str, int] | None:
        clean = {
            "prompt_tokens": int(input_tokens or 0),
            "completion_tokens": int(output_tokens or 0),
            "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
        }
        if clean["total_tokens"] <= 0:
            return None
        self.last_usage = dict(clean)
        for key in self.session_usage:
            self.session_usage[key] += clean.get(key, 0)
        return dict(clean)

    def reset_session_usage(self) -> dict[str, int]:
        snapshot = dict(self.session_usage)
        self.session_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return snapshot

    # ---- 非流式 ----

    async def complete_with_tools(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        api_key = self._require_api_key()
        body = self._request_body(messages, tools=tools or self.openai_tool_schemas(), stream=False)
        client = self._client()
        try:
            response = await client.post(
                self._endpoint(),
                headers=self._headers(api_key),
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic 模型工具决策调用失败：{exc}") from exc
        finally:
            await client.aclose()
        if response.status_code >= 400:
            raise ProviderError(f"Anthropic 接口错误 {response.status_code}：{response.text[:300]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Anthropic 返回非 JSON 响应。") from exc
        return self._normalize_message(payload)

    def _normalize_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(str(block.get("text") or ""))
            elif btype == "tool_use":
                arguments = block.get("input")
                tool_calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "tool": str(block.get("name") or ""),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    }
                )
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        self._record_usage(usage.get("input_tokens"), usage.get("output_tokens"))
        return {
            "content": "".join(text_parts),
            "tool_calls": [item for item in tool_calls if item["tool"]],
        }

    async def complete(self, messages: list[Any]) -> str:
        api_key = self._require_api_key()
        body = self._request_body(messages, tools=None, stream=False)
        client = self._client()
        try:
            response = await client.post(self._endpoint(), headers=self._headers(api_key), json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic 模型调用失败：{exc}") from exc
        finally:
            await client.aclose()
        if response.status_code >= 400:
            raise ProviderError(f"Anthropic 接口错误 {response.status_code}：{response.text[:300]}")
        decision = self._normalize_message(response.json())
        return str(decision["content"])

    # ---- 流式 ----

    async def stream(self, messages: list[Any]) -> AsyncIterator[str]:
        decision: dict[str, Any] = {"content": "", "tool_calls": []}
        async for event in self.stream_with_tools(messages, tools=None):
            if event.get("type") == "delta":
                yield str(event.get("text") or "")
            elif event.get("type") == "decision":
                decision = event
        yield decision.get("content") or ""

    async def stream_with_tools(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict]:
        api_key = self._require_api_key()
        body = self._request_body(messages, tools=tools or self.openai_tool_schemas(), stream=True)
        client = self._client()
        text_parts: list[str] = []
        tool_fragments: dict[int, dict[str, str]] = {}
        input_tokens = 0
        output_tokens = 0
        try:
            async with client.stream(
                "POST", self._endpoint(), headers=self._headers(api_key), json=body
            ) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode("utf-8", errors="replace")
                    raise ProviderError(f"Anthropic 接口错误 {response.status_code}：{raw[:300]}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "message_start":
                        msg = event.get("message") if isinstance(event.get("message"), dict) else {}
                        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                        input_tokens = usage.get("input_tokens") or input_tokens
                    elif etype == "content_block_start":
                        block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
                        index = int(event.get("index") or 0)
                        if block.get("type") == "tool_use":
                            tool_fragments[index] = {
                                "id": str(block.get("id") or ""),
                                "name": str(block.get("name") or ""),
                                "arguments": "",
                            }
                    elif etype == "content_block_delta":
                        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                        index = int(event.get("index") or 0)
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            chunk = str(delta.get("text") or "")
                            text_parts.append(chunk)
                            yield {"type": "delta", "text": chunk}
                        elif dtype == "thinking_delta":
                            yield {"type": "reasoning_delta", "text": str(delta.get("thinking") or "")}
                        elif dtype == "input_json_delta":
                            entry = tool_fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            entry["arguments"] += str(delta.get("partial_json") or "")
                    elif etype == "message_delta":
                        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                        output_tokens = usage.get("output_tokens") or output_tokens
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic 流式调用失败：{exc}") from exc
        finally:
            await client.aclose()

        usage_captured = self._record_usage(input_tokens, output_tokens)
        normalized: list[dict[str, Any]] = []
        for index in sorted(tool_fragments):
            entry = tool_fragments[index]
            name = entry["name"].strip()
            if not name:
                continue
            try:
                parsed = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                parsed = {}
            normalized.append(
                {
                    "id": entry["id"],
                    "type": "function",
                    "tool": name,
                    "arguments": parsed if isinstance(parsed, dict) else {},
                }
            )
        decision: dict[str, Any] = {
            "type": "decision",
            "content": "".join(text_parts),
            "tool_calls": normalized,
        }
        if usage_captured:
            decision["usage"] = usage_captured
        yield decision
