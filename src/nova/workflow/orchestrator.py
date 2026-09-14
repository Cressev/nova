"""workflow 脚本编排桥（dsh workflow 对齐）。

node 宿主（host.js）跑模型写的 JS 协调脚本，脚本本身跑在只读 seatbelt 内；
agent() 请求经 stdio JSON 协议回到 Python，由注入的 spawn_agent 执行真实
子代理。桥层负责：并发上限、总超时、phase/log 事件收集、schema 子集校验。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..tools.sandbox import SandboxPolicy, confine

HOST_JS = Path(__file__).with_name("host.js")

# dsh schema 子集：只允许这些关键字（no pattern/format/numeric bounds）
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "oneOf"}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def validate_schema_subset(value: Any, schema: Any, path: str = "$") -> list[str]:
    """校验 value 是否符合 schema 子集；返回违例列表（空=通过）。"""
    violations: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    unknown = [k for k in schema if k not in _SCHEMA_KEYS]
    if unknown:
        violations.append(f"{path}: unsupported schema keys {sorted(unknown)}")
        return violations

    if "const" in schema and value != schema["const"]:
        violations.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema:
        if not isinstance(schema["enum"], list):
            violations.append(f"{path}: enum must be an array")
        elif value not in schema["enum"]:
            violations.append(f"{path}: {value!r} not in enum")
    if "oneOf" in schema:
        if not isinstance(schema["oneOf"], list) or not schema["oneOf"]:
            violations.append(f"{path}: oneOf must be a non-empty array")
        else:
            matches = sum(1 for sub in schema["oneOf"] if not validate_schema_subset(value, sub, path))
            if matches != 1:
                violations.append(f"{path}: matches {matches} oneOf branches (need exactly 1)")

    expected_type = schema.get("type")
    if expected_type is not None:
        ok = False
        if expected_type == "object":
            ok = isinstance(value, dict)
        elif expected_type == "array":
            ok = isinstance(value, list)
        elif expected_type == "string":
            ok = isinstance(value, str)
        elif expected_type == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif expected_type == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected_type == "boolean":
            ok = isinstance(value, bool)
        elif expected_type == "null":
            ok = value is None
        if expected_type not in _TYPES:
            violations.append(f"{path}: unsupported type {expected_type!r}")
        elif not ok:
            violations.append(f"{path}: expected {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        props = schema["properties"]
        for key in schema.get("required") or []:
            if key not in value:
                violations.append(f"{path}: missing required property {key!r}")
        additional = schema.get("additionalProperties")
        for key, sub in props.items():
            if key in value:
                violations.extend(validate_schema_subset(value[key], sub, f"{path}.{key}"))
            elif sub.get("required") or "const" in sub or "enum" in sub:
                # 可选属性不在值里不校验；required 由上层处理
                pass
        if additional is False:
            extra = [k for k in value if k not in props]
            if extra:
                violations.append(f"{path}: additional properties not allowed {sorted(extra)}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            violations.extend(validate_schema_subset(item, schema["items"], f"{path}[{index}]"))
    return violations


def extract_json_object(text: str) -> Any:
    """从子代理输出提取首个 JSON 对象（容忍 ```json 围栏与前后杂文字）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in output")
    return json.loads(cleaned[start : end + 1])


def run_workflow_script(
    script: str,
    args: dict[str, Any],
    *,
    spawn_agent: Callable[[str, dict[str, Any]], dict[str, Any]],
    node_bin: str | None = None,
    total_timeout_s: float = 480.0,
    max_concurrency: int = 6,
) -> dict[str, Any]:
    """执行 JS 编排脚本，返回 {"value", "events", "agents"}。

    spawn_agent(prompt, opts) -> {"ok": bool, "result": Any, "error": str}.
    脚本失败（语法/误用钩子/宿主崩溃/总超时）抛 WorkflowScriptError。
    """
    node = node_bin or shutil.which("node")
    if not node:
        raise WorkflowScriptError("workflow 脚本编排需要 Node.js（未在 PATH 找到 node）")
    with tempfile.TemporaryDirectory(prefix="nova-wf-") as tmp:
        script_file = Path(tmp) / "script.js"
        script_file.write_text(script, encoding="utf-8")
        args_file = Path(tmp) / "args.json"
        args_file.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
        policy = SandboxPolicy(mode="read_only", workspace_root="")
        argv = confine(
            f"{shlex.quote(node)} {shlex.quote(str(HOST_JS))} {shlex.quote(str(script_file))} {shlex.quote(str(args_file))}",
            policy,
        )
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise WorkflowScriptError(f"workflow 宿主启动失败：{exc}") from exc

        events: list[dict[str, Any]] = []
        agents: list[dict[str, Any]] = []
        semaphore = threading.Semaphore(max_concurrency)
        stdin_lock = threading.Lock()
        state = {"done": False, "value": None, "error": None}

        def respond(message: dict[str, Any]) -> None:
            with stdin_lock:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                    proc.stdin.flush()

        def handle_agent(msg: dict[str, Any]) -> None:
            call_id = msg["id"]
            prompt = str(msg.get("prompt") or "")
            opts = msg.get("opts") if isinstance(msg.get("opts"), dict) else {}

            def work() -> None:
                try:
                    with semaphore:
                        outcome = spawn_agent(prompt, dict(opts))
                except Exception as exc:  # 裸抛=永不响应=脚本挂死；失败走 ok False
                    outcome = {"ok": False, "result": None, "error": f"{type(exc).__name__}: {exc}"}
                agents.append(
                    {
                        "label": opts.get("label") or f"agent-{len(agents) + 1}",
                        "ok": bool(outcome.get("ok")),
                        "result": outcome.get("result"),
                        "error": outcome.get("error"),
                        "provider": opts.get("provider"),
                        "model": opts.get("model"),
                        "schema": bool(opts.get("schema")),
                    }
                )
                if outcome.get("ok"):
                    respond({"id": call_id, "ok": True, "result": outcome.get("result")})
                else:
                    # dsh 语义：子代理失败 → agent() 解析为 null（不杀脚本）
                    respond({"id": call_id, "ok": False})

            threading.Thread(target=work, daemon=True).start()

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = msg.get("op")
                if op == "agent":
                    handle_agent(msg)
                elif op == "phase":
                    events.append({"kind": "phase", "title": msg.get("title") or ""})
                elif op == "log":
                    events.append({"kind": "log", "message": msg.get("message") or ""})
                elif op == "done":
                    state["done"] = True
                    state["value"] = msg.get("value")
                    return
                elif op == "error":
                    state["error"] = str(msg.get("message") or "script failed")
                    return
            if not state["done"] and state["error"] is None:
                state["error"] = "workflow 宿主提前退出（stdout 关闭）"

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        deadline = time.monotonic() + total_timeout_s
        reader_thread.join(timeout=total_timeout_s)
        timed_out = False
        if reader_thread.is_alive():
            timed_out = True
        elif state["error"] is None and not state["done"]:
            # reader 结束但既没 done 也没 error：等一小会儿收尾
            state["error"] = "workflow 宿主异常终止"
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        if timed_out:
            raise WorkflowScriptError(f"workflow 脚本超时（>{int(total_timeout_s)}s），已终止")
        if state["error"] is not None:
            raise WorkflowScriptError(f"workflow 脚本失败：{state['error']}")
        return {"value": state["value"], "events": events, "agents": agents}


class WorkflowScriptError(RuntimeError):
    """workflow 编排脚本失败（语法误用/宿主崩溃/超时）。"""
