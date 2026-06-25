from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def normalize_mcp_name(name: str) -> str:
    """对齐 cc 的 MCP 工具命名思路：mcp__server__tool。"""

    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "server"


class McpManager:
    """项目级 MCP 配置与最小 demo 调用入口。

    当前先实现可验证的 discover/call 闭环：真实 stdio/http/sse transport
    后续接 MCP SDK，但工具命名、配置形状和 UI 数据结构先稳定下来。
    """

    def __init__(self, project_root: Path) -> None:
        project_root = project_root.expanduser()
        self.project_root = self._display_path(project_root if project_root.is_absolute() else project_root.resolve())
        self.config_path = self.project_root / ".nova" / "mcp.json"

    def _display_path(self, path: Path) -> Path:
        """macOS 临时目录常显示为 /var，但 resolve 后会变成 /private/var。"""

        text = str(path)
        private_var = "/private/var"
        if text == private_var or text.startswith(f"{private_var}/"):
            candidate = Path("/var") / Path(text).relative_to(private_var)
            if candidate.exists() or candidate.parent.exists():
                return candidate
        return path

    def status(self) -> dict[str, Any]:
        config, config_error = self._read_config()
        servers = self._server_statuses(config, config_error=config_error)
        tools: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        for server in servers:
            tools.extend(server.get("tools", []))
            resources.extend(server.get("resources", []))
        return {
            "enabled": self.config_path.exists(),
            "config_path": str(self.config_path),
            "servers": servers,
            "tools": tools,
            "resources": resources,
            "error": config_error,
        }

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [
            self._tool_to_spec(tool)
            for tool in self.status()["tools"]
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._find_tool(tool_name)
        if tool is None:
            raise ValueError(f"未知 MCP 工具：{tool_name}")
        server_name = str(tool["server"])
        original_name = str(tool["original_name"])
        started_at = time.perf_counter()
        if server_name == "demo" and original_name == "echo":
            content = str(arguments.get("text") or arguments.get("message") or "")
            result = {
                "content": content,
                "is_error": False,
            }
        else:
            raise ValueError(f"MCP 工具尚未连接真实 transport：{tool_name}")

        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        spec = self._tool_to_spec(tool)
        return {
            "ok": True,
            "tool": tool_name,
            "server": server_name,
            "result": result,
            "output": result["content"],
            "data": {
                "mcp": {
                    "server": server_name,
                    "transport": tool["transport"],
                    "original_tool": original_name,
                },
                "spec": spec,
                "duration_ms": duration_ms,
            },
        }

    def _read_config(self) -> tuple[dict[str, Any], str | None]:
        if not self.config_path.exists():
            return {"mcpServers": {}}, None
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"mcpServers": {}}, str(exc)
        if not isinstance(payload, dict):
            return {"mcpServers": {}}, "mcp.json 顶层必须是对象"
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict):
            return {"mcpServers": {}}, "mcp.json 需要 mcpServers 对象"
        return {"mcpServers": servers}, None

    def _server_statuses(self, config: dict[str, Any], *, config_error: str | None) -> list[dict[str, Any]]:
        if config_error:
            return []
        servers: list[dict[str, Any]] = []
        for name, raw_config in config.get("mcpServers", {}).items():
            if not isinstance(raw_config, dict):
                servers.append(
                    {
                        "name": str(name),
                        "normalized_name": normalize_mcp_name(str(name)),
                        "transport": "unknown",
                        "status": "failed",
                        "error": "server config 必须是对象",
                        "tools": [],
                        "resources": [],
                    }
                )
                continue
            servers.append(self._server_status(str(name), raw_config))
        return servers

    def _server_status(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        transport = str(config.get("type") or "stdio").strip() or "stdio"
        normalized = normalize_mcp_name(name)
        disabled = bool(config.get("disabled"))
        status = "disabled" if disabled else "configured"
        error = None
        if transport not in {"stdio", "http", "sse"}:
            status = "failed"
            error = f"暂不支持的 MCP transport：{transport}"
        elif transport == "stdio" and not str(config.get("command") or "").strip():
            status = "failed"
            error = "stdio server 需要 command"
        elif transport in {"http", "sse"} and not str(config.get("url") or "").strip():
            status = "failed"
            error = f"{transport} server 需要 url"

        tools: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        if name == "demo" and status == "configured" and transport == "stdio":
            status = "connected"
            tools = [self._demo_echo_tool(name, normalized, transport)]
            resources = [self._demo_readme_resource(name)]

        return {
            "name": name,
            "normalized_name": normalized,
            "transport": transport,
            "status": status,
            "command": config.get("command"),
            "args": config.get("args") if isinstance(config.get("args"), list) else [],
            "url": config.get("url"),
            "tools": tools,
            "resources": resources,
            "error": error,
        }

    def _demo_echo_tool(self, server_name: str, normalized_server: str, transport: str) -> dict[str, Any]:
        return {
            "name": f"mcp__{normalized_server}__echo",
            "original_name": "echo",
            "server": server_name,
            "transport": transport,
            "description": "Demo MCP echo 工具，用于验证 MCP 调用链路。",
            "input_schema": {"text": "要原样返回的文本"},
            "read_only": True,
        }

    def _demo_readme_resource(self, server_name: str) -> dict[str, Any]:
        return {
            "server": server_name,
            "uri": "demo://readme",
            "name": "Demo README",
            "description": "Nova 内置 MCP demo resource。",
            "mime_type": "text/plain",
        }

    def _find_tool(self, tool_name: str) -> dict[str, Any] | None:
        for tool in self.status()["tools"]:
            if tool.get("name") == tool_name:
                return tool
        return None

    def _tool_to_spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": tool["name"],
            "description": tool.get("description") or tool["name"],
            "read_only": bool(tool.get("read_only", True)),
            "supports_parallel": True,
            "permission": "mcp",
            "schema": tool.get("input_schema") or {},
            "category": "mcp",
            "risk": "low",
            "interrupt_behavior": "cancel",
            "hooks_enabled": True,
            "mcp": {
                "server": tool.get("server"),
                "transport": tool.get("transport"),
                "original_tool": tool.get("original_name"),
            },
        }
