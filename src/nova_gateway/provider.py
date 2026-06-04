from __future__ import annotations

import os

import httpx

from .models import ChatMessage, ChatRole


class ProviderError(RuntimeError):
    """模型调用失败时抛出，API 层会把它转换成可读错误消息。"""


class BigModelProvider:
    def __init__(
        self,
        *,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        model: str = "glm-4.7",
        api_key_env: str = "BIGMODEL_API_KEY",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env

    def is_configured(self) -> bool:
        return bool(os.getenv(self.api_key_env))

    async def complete(self, messages: list[ChatMessage]) -> str:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ProviderError(
                f"未配置 {self.api_key_env}，请先在启动服务前设置环境变量。"
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
