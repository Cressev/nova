"""多 Provider 注册表（dsh llm 多提供方对齐）。

全部走 OpenAI 兼容端点（BigModelProvider 本就是 OpenAI client），切换 = 换
base_url/model/api_key_env 三元组，运行时热切换不重启。预设只存"端点形状"，
不存任何密钥；key 仍只从各 provider 的环境变量或运行时密钥文件读取。
"""

from __future__ import annotations

from typing import Any

# protocol: "openai" = OpenAI chat/completions 兼容；"anthropic" = Anthropic Messages API。
# 两种协议都在同一个 provider 槽位上热切换（routes 切 preset 时按 protocol 换实例）。
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "bigmodel": {
        "label": "BigModel（GLM）",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "BIGMODEL_API_KEY",
        "default_model": "glm-4.7",
    },
    "zai-anthropic": {
        "label": "BigModel（Anthropic 兼容）",
        "protocol": "anthropic",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "glm-4.7",
    },
    "anthropic": {
        "label": "Anthropic（Claude）",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-5",
    },
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
    "moonshot": {
        "label": "Moonshot（Kimi）",
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "default_model": "moonshot-v1-32k",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "protocol": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "protocol": "openai",
        "base_url": "",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "",
    },
}


def resolve_preset(name: str) -> dict[str, Any]:
    key = (name or "bigmodel").strip().lower()
    return dict(PROVIDER_PRESETS.get(key) or PROVIDER_PRESETS["bigmodel"])


def preset_catalog() -> list[dict[str, Any]]:
    """设置页下拉用的目录（不含任何敏感值）。"""
    return [
        {
            "id": key,
            "label": value["label"],
            "default_model": value["default_model"],
            "protocol": value.get("protocol") or "openai",
            "base_url": value["base_url"],
        }
        for key, value in PROVIDER_PRESETS.items()
    ]
