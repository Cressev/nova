from __future__ import annotations

import json
import os
from typing import Any, Callable


class ZaiWebSearchError(RuntimeError):
    """Z.ai 联网搜索失败时抛出，外层会包装为工具失败事件。"""


def run_zai_web_search(
    arguments: dict[str, Any],
    *,
    api_key: str | None = None,
    client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    query = str(arguments.get("query") or arguments.get("search_query") or "").strip()
    if not query:
        raise ZaiWebSearchError("web_search 需要 query")
    resolved_key = (api_key or os.getenv("ZAI_API_KEY") or os.getenv("BIGMODEL_API_KEY") or "").strip()
    if not resolved_key:
        raise ZaiWebSearchError("未配置 Z.ai/BigModel API Key，无法执行联网搜索")

    client = client_factory(resolved_key) if client_factory is not None else _create_zai_client(resolved_key)
    params = {
        "search_engine": str(arguments.get("search_engine") or "search_pro"),
        "search_query": query,
        "count": _bounded_int(arguments.get("count"), default=10, minimum=1, maximum=50),
        "search_recency_filter": str(arguments.get("search_recency_filter") or "noLimit"),
        "content_size": str(arguments.get("content_size") or "high"),
    }
    domain = str(arguments.get("search_domain_filter") or "").strip()
    if domain:
        params["search_domain_filter"] = domain

    try:
        response = client.web_search.web_search(**params)
    except Exception as exc:  # pragma: no cover - 真实 SDK 异常类型可能随版本变化。
        raise ZaiWebSearchError(f"Z.ai 联网搜索失败：{exc}") from exc

    payload = _response_to_dict(response)
    results = _normalize_results(payload.get("search_result") or payload.get("results") or [])
    return {
        "provider": "zai",
        "query": query,
        "request": params,
        "raw": payload,
        "results": results,
        "output": _format_results(query, results, payload),
    }


def _create_zai_client(api_key: str) -> Any:
    try:
        from zai import ZhipuAiClient  # type: ignore
    except ImportError:
        try:
            from zai import ZaiClient as ZhipuAiClient  # type: ignore
        except ImportError as exc:
            raise ZaiWebSearchError("缺少 zai-sdk，请安装依赖后重启 Nova。") from exc
    return ZhipuAiClient(api_key=api_key)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        except Exception:
            pass
    if hasattr(response, "to_dict"):
        try:
            dumped = response.to_dict()
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        except Exception:
            pass
    if hasattr(response, "dict"):
        try:
            dumped = response.dict()
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        except Exception:
            pass
    if hasattr(response, "__dict__"):
        payload = {
            key: _plain_value(value)
            for key, value in vars(response).items()
            if not key.startswith("_")
        }
        if payload:
            return payload
    attribute_payload = {
        key: _plain_value(getattr(response, key))
        for key in ["created", "request_id", "id", "search_intent", "search_result", "results"]
        if hasattr(response, key)
    }
    if attribute_payload:
        return attribute_payload
    try:
        return json.loads(str(response))
    except json.JSONDecodeError:
        return {"text": str(response)}


def _normalize_results(raw_results: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, Any]] = []
    for item in raw_results[:50]:
        if not isinstance(item, dict):
            item = _response_to_dict(item)
        title = str(item.get("title") or item.get("name") or item.get("site_name") or "未命名结果")
        url = str(item.get("link") or item.get("url") or "")
        content = str(item.get("content") or item.get("summary") or item.get("snippet") or "")
        results.append(
            {
                "title": title,
                "url": url,
                "content": content,
                "site_name": item.get("site_name") or item.get("site") or "",
                "publish_date": item.get("publish_date") or item.get("date") or "",
                "favicon": item.get("favicon") or "",
            }
        )
    return results


def _plain_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {
            key: _plain_value(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _format_results(query: str, results: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    if not results:
        fallback = payload.get("text") or payload.get("content") or ""
        return str(fallback or f"未搜索到与“{query}”相关的结果。")
    lines = [f"Z.ai 搜索结果：{query}"]
    for index, item in enumerate(results, start=1):
        title = item.get("title") or "未命名结果"
        url = item.get("url") or ""
        content = str(item.get("content") or "").replace("\n", " ").strip()
        if len(content) > 700:
            content = content[:700] + "...[摘要截断]"
        source = f"（{item.get('site_name')}）" if item.get("site_name") else ""
        lines.append(f"{index}. {title}{source}\n   URL: {url}\n   摘要: {content}")
    return "\n".join(lines)
