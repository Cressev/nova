"""多 Provider 注册表（dsh llm 多提供方对齐）。

全部走 OpenAI 兼容端点（BigModelProvider 本就是 OpenAI client），切换 = 换
base_url/model/api_key_env 三元组，运行时热切换不重启。预设只存"端点形状"，
不存任何密钥；key 仍只从各 provider 的环境变量或运行时密钥文件读取。
"""

from __future__ import annotations

from typing import Any

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "bigmodel": {
        "label": "BigModel（GLM）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "BIGMODEL_API_KEY",
        "default_model": "glm-4.7",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
    "moonshot": {
        "label": "Moonshot（Kimi）",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "default_model": "moonshot-v1-32k",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
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
        {"id": key, "label": value["label"], "default_model": value["default_model"]}
        for key, value in PROVIDER_PRESETS.items()
    ]
