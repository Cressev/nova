from __future__ import annotations

import os
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from .models import ChatMessage, ChatRole


class ProviderError(RuntimeError):
    """模型调用失败时抛出，API 层会把它转换成可读错误消息。"""


class BigModelProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str = "BIGMODEL_API_KEY",
        api_key_file: Path | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BIGMODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")).rstrip("/")
        self.model = model or os.getenv("BIGMODEL_MODEL", "glm-4.7")
        self.api_key_env = api_key_env
        self._runtime_api_key = self._load_runtime_api_key(api_key_file)

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
        payload = {self.api_key_env: api_key} if api_key else {}
        api_key_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def complete(self, messages: list[ChatMessage]) -> str:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        # 只把对话所需字段发给模型，内部错误消息、工具 trace 和调试信息不进入模型上下文。
        payload_messages = [
            {"role": message.role.value, "content": message.content}
            for message in messages
            if message.role in {ChatRole.SYSTEM, ChatRole.USER, ChatRole.ASSISTANT}
        ]
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": 0.3,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"GLM 调用失败：HTTP {response.status_code}，{response.text[:300]}"
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("GLM 返回结构异常，无法提取 assistant 消息。") from exc

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        api_key = self._api_key()
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请在设置页填写 API Key，或在启动服务前设置环境变量。"
            )

        # 采用 OpenAI-compatible SSE：后端只向前端转发文本增量，不暴露原始响应。
        payload_messages = [
            {"role": message.role.value, "content": message.content}
            for message in messages
            if message.role in {ChatRole.SYSTEM, ChatRole.USER, ChatRole.ASSISTANT}
        ]
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": 0.3,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ProviderError(
                        f"GLM 调用失败：HTTP {response.status_code}，{body[:300].decode(errors='ignore')}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = payload.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
