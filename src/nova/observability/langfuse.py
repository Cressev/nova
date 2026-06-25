from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LangfuseConfig:
    public_key: str | None = None
    secret_key: str | None = None
    host: str = "https://cloud.langfuse.com"
    enabled: bool = True

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.public_key and self.secret_key)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "public_key_set": bool(self.public_key),
            "secret_key_set": bool(self.secret_key),
            "host": self.host,
            "enabled": self.enabled,
        }


class LangfuseTraceRecorder:
    """Nova 的 Langfuse 边界层。

    这里只记录模型可见输入输出、工具调用、错误和耗时；不会伪造或泄露模型隐藏思维链。
    """

    def __init__(self, config: LangfuseConfig, *, client: Any | None = None) -> None:
        self.config = config
        self.client = client if client is not None else self._create_client(config)
        self._sessions: dict[str, Any] = {}
        self._turns: dict[str, Any] = {}

    def start_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_input: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if self.client is None:
            return turn_id
        try:
            session = self._sessions.get(session_id)
            if session is None:
                session = self.client.start_observation(
                    name="nova.agent.session",
                    as_type="span",
                    input={"session_id": session_id},
                    metadata={"session_id": session_id, **(metadata or {})},
                )
                self._sessions[session_id] = session
            turn = session.start_observation(
                name="nova.agent.turn",
                as_type="span",
                input={"user_input": user_input},
                metadata={
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **(metadata or {}),
                },
            )
            self._turns[turn_id] = turn
        except Exception:
            return turn_id
        return turn_id

    def record_generation(
        self,
        *,
        turn_id: str,
        name: str,
        model: str,
        input_messages: list[dict[str, Any]],
        output: str,
        tool_calls: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        parent = self._turns.get(turn_id)
        if parent is None:
            return
        try:
            generation = parent.start_observation(
                name=name,
                as_type="generation",
                model=model,
                input=input_messages,
                metadata={"tool_calls": tool_calls, **(metadata or {})},
            )
            generation.update(output={"content": output, "tool_calls": tool_calls}).end()
        except Exception:
            return

    def record_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        output: str,
        ok: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        parent = self._turns.get(turn_id)
        if parent is None:
            return
        try:
            observation = parent.start_observation(
                name=f"tool.{tool}",
                as_type="tool",
                input={"arguments": _redact(arguments)},
                metadata={"call_id": call_id, "ok": ok, **(metadata or {})},
            )
            observation.update(output={"ok": ok, "output": output}).end()
        except Exception:
            return

    def end_turn(self, *, turn_id: str, output: str, status: str = "ok") -> None:
        root = self._turns.pop(turn_id, None)
        if root is None:
            return
        try:
            root.update(output={"content": output, "status": status}, metadata={"status": status}).end()
            flush = getattr(self.client, "flush", None)
            if callable(flush):
                flush()
        except Exception:
            return

    def _create_client(self, config: LangfuseConfig) -> Any | None:
        if not config.configured:
            return None
        os.environ["LANGFUSE_PUBLIC_KEY"] = config.public_key or ""
        os.environ["LANGFUSE_SECRET_KEY"] = config.secret_key or ""
        os.environ["LANGFUSE_HOST"] = config.host
        os.environ["LANGFUSE_BASE_URL"] = config.host
        try:
            from langfuse import get_client
        except ImportError:
            return None
        try:
            return get_client()
        except Exception:
            return None


def load_langfuse_config(path: Path | None) -> LangfuseConfig:
    payload = _read_secret_payload(path)
    public_key = str(payload.get("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = str(payload.get("LANGFUSE_SECRET_KEY") or os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    host = str(
        payload.get("LANGFUSE_HOST")
        or payload.get("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    ).strip()
    enabled_raw = payload.get("LANGFUSE_ENABLED", os.getenv("LANGFUSE_ENABLED", "true"))
    enabled = str(enabled_raw).lower() not in {"0", "false", "no", "off"}
    return LangfuseConfig(
        public_key=public_key or None,
        secret_key=secret_key or None,
        host=host or "https://cloud.langfuse.com",
        enabled=enabled,
    )


def update_langfuse_secrets(
    path: Path,
    *,
    public_key: str | None = None,
    secret_key: str | None = None,
    host: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_secret_payload(path)
    _set_or_delete(payload, "LANGFUSE_PUBLIC_KEY", public_key)
    _set_or_delete(payload, "LANGFUSE_SECRET_KEY", secret_key)
    if host is not None:
        value = host.strip()
        if value:
            payload["LANGFUSE_HOST"] = value.rstrip("/")
            payload["LANGFUSE_BASE_URL"] = value.rstrip("/")
    if enabled is not None:
        payload["LANGFUSE_ENABLED"] = bool(enabled)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_langfuse_config(path).status()


def _read_secret_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _set_or_delete(payload: dict[str, Any], key: str, value: str | None) -> None:
    if value is None:
        return
    stripped = value.strip()
    if stripped:
        payload[key] = stripped
    else:
        payload.pop(key, None)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ["key", "secret", "token", "password"]):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
