"""工具管线对齐 dsh 的边际测试。

覆盖：参数契约验证（INVALID_ARGS + path 限定违规）、别名归一、无损快照
边界、超时预算（TIMEOUT）、未知工具（UNKNOWN_TOOL）、派发前取消
（ABORTED_BEFORE_DISPATCH）、权限拒绝码、输出契约（INVALID_TOOL_OUTPUT）、
schema 注册期静态校验、取消后的串行批次短路。
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import time
import unittest
from pathlib import Path

from nova.tools.errors import (
    INVALID_ARGS,
    INVALID_TOOL_OUTPUT,
    TOOL_ABORTED_BEFORE_DISPATCH,
    TOOL_PERMISSION_DENIED,
    TOOL_TIMEOUT,
    UNKNOWN_TOOL,
)
from nova.tools.executor import ToolExecutor
from nova.tools.validation import (
    assert_supported_schema,
    snapshot_arguments,
    validate_arguments,
    validate_value,
)
from nova.tools.workspace import TOOL_SPECS, WorkspaceTools


def _make_executor(**kwargs) -> ToolExecutor:
    root = Path(tempfile.mkdtemp())
    tools = WorkspaceTools(root, permission_mode="bypass_permissions")
    return ToolExecutor(tools, **kwargs)


class ValidationSemanticsTest(unittest.TestCase):
    """验证器与 dsh json-schema.ts 的子集/文案对齐。"""

    def test_all_tool_contracts_pass_static_check(self) -> None:
        for name, spec in TOOL_SPECS.items():
            if spec.json_schema:
                assert_supported_schema(spec.json_schema, f"specs.{name}")

    def test_unsupported_keyword_rejected_at_schema_level(self) -> None:
        with self.assertRaises(Exception) as ctx:
            assert_supported_schema(
                {"type": "object", "properties": {"a": {"type": "string", "minLength": 1}}}
            )
        self.assertIn("not a supported keyword", str(ctx.exception))

    def test_missing_required_reports_path(self) -> None:
        schema = TOOL_SPECS["read_file"].json_schema
        violations = validate_arguments(schema, {})
        self.assertEqual(len(violations), 1)
        self.assertIn('missing required property "arguments.path"', violations)

    def test_wrong_type_violation(self) -> None:
        schema = TOOL_SPECS["read_file"].json_schema
        violations = validate_arguments(schema, {"path": 123})
        self.assertEqual(violations, ['"arguments.path" must be a string'])

    def test_enum_violation_in_nested_array_item(self) -> None:
        schema = TOOL_SPECS["todo_write"].json_schema
        violations = validate_arguments(
            schema,
            {"items": [{"content": "任务", "status": "done"}]},
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("arguments.items[0].status", violations[0])
        self.assertIn("must be one of", violations[0])

    def test_additional_properties_false_rejects_extras(self) -> None:
        schema = TOOL_SPECS["read_file"].json_schema
        violations = validate_arguments(schema, {"path": "a.txt", "extra": 1})
        self.assertEqual(
            violations,
            ['"arguments.extra" is not a declared property (additionalProperties: false)'],
        )

    def test_bool_is_not_integer(self) -> None:
        violations = validate_value({"type": "integer"}, True, "arguments.n")
        self.assertEqual(violations, ['"arguments.n" must be a integer'])

    def test_one_of_branch_matching(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ]
        }
        self.assertEqual(validate_value(schema, "x", "arguments.v"), [])
        self.assertEqual(validate_value(schema, 3, "arguments.v"), [])
        violations = validate_value(schema, True, "arguments.v")
        self.assertEqual(len(violations), 1)
        self.assertIn("exactly one oneOf branch", violations[0])

    def test_const_violation_format(self) -> None:
        violations = validate_value({"type": "string", "const": "exact"}, "other", "arguments.v")
        self.assertEqual(violations, ['"arguments.v" must be "exact"'])


class SnapshotBoundaryTest(unittest.TestCase):
    """无损 JSON 快照：隔离 + 非序列化提前爆炸。"""

    def test_snapshot_returns_isolated_copy(self) -> None:
        original = {"path": "a.txt", "nested": {"list": [1, 2]}}
        snapshot = snapshot_arguments(original)
        snapshot["nested"]["list"].append(3)
        self.assertEqual(original["nested"]["list"], [1, 2])

    def test_snapshot_rejects_non_lossless(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_arguments({"bad": float("nan")})
        with self.assertRaises(ValueError):
            snapshot_arguments({1: "int-key"})

    def test_hook_injected_garbage_becomes_invalid_args(self) -> None:
        executor = _make_executor()
        # 模拟 hook 改写后注入非序列化值（绕过模型 JSON 天然安全的假设）
        arguments = {"path": "a.txt"}
        arguments["bad"] = object()  # type: ignore[assignment]
        events, result_json = executor.run_one("call_x", "read_file", arguments)
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], INVALID_ARGS)


class PipelineCodesTest(unittest.TestCase):
    """执行器管线的结构化错误码与 body 隔离。"""

    def setUp(self) -> None:
        self.executor = _make_executor()

    def test_invalid_args_never_invokes_body(self) -> None:
        invoked: list[str] = []
        original_run = self.executor.tools.run

        def probe_run(name, arguments):
            invoked.append(name)
            return original_run(name, arguments)

        self.executor.tools.run = probe_run  # type: ignore[method-assign]
        events, result_json = self.executor.run_one("call_v", "read_file", {})
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], INVALID_ARGS)
        self.assertIn("missing required property", result["error"])
        self.assertIn('"arguments.path"', result["error"])
        self.assertEqual(invoked, [])

    def test_shell_alias_cmd_normalized_before_validation(self) -> None:
        events, result_json = self.executor.run_one(
            "call_alias", "shell_command", {"cmd": "echo hi"}
        )
        result = json.loads(result_json)
        # 别名归一后通过契约；真实执行可能因权限/沙箱失败，但绝不是 INVALID_ARGS
        self.assertNotEqual(result["data"].get("error_code"), INVALID_ARGS)

    def test_unknown_tool_code(self) -> None:
        events, result_json = self.executor.run_one("call_u", "no_such_tool", {})
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], UNKNOWN_TOOL)

    def test_permission_denied_code_in_plan_mode(self) -> None:
        root = Path(tempfile.mkdtemp())
        tools = WorkspaceTools(root, permission_mode="plan")
        executor = ToolExecutor(tools)
        events, result_json = executor.run_one(
            "call_p", "write_file", {"path": "x.txt", "content": "hi"}
        )
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], TOOL_PERMISSION_DENIED)

    def test_timeout_budget_returns_promptly(self) -> None:
        root = Path(tempfile.mkdtemp())
        tools = WorkspaceTools(root, permission_mode="bypass_permissions")
        original_run = tools.run

        def slow_run(name, arguments):
            time.sleep(1.5)
            return original_run(name, arguments)

        tools.run = slow_run  # type: ignore[method-assign]
        spec = TOOL_SPECS["todo_read"]
        TOOL_SPECS["todo_read"] = dataclasses.replace(spec, timeout_ms=100)
        try:
            executor = ToolExecutor(tools)
            started = time.perf_counter()
            events, result_json = executor.run_one("call_t", "todo_read", {})
            elapsed = time.perf_counter() - started
            result = json.loads(result_json)
            self.assertFalse(result["ok"])
            self.assertEqual(result["data"]["error_code"], TOOL_TIMEOUT)
            self.assertEqual(result["data"]["timeout_ms"], 100)
            self.assertLess(elapsed, 1.2, "超时应立即返回，不等慢 body")
        finally:
            TOOL_SPECS["todo_read"] = spec

    def test_cancel_before_dispatch_skips_body(self) -> None:
        invoked: list[str] = []
        root = Path(tempfile.mkdtemp())
        tools = WorkspaceTools(root, permission_mode="bypass_permissions")
        original_run = tools.run

        def probe_run(name, arguments):
            invoked.append(name)
            return original_run(name, arguments)

        tools.run = probe_run  # type: ignore[method-assign]
        executor = ToolExecutor(tools, cancel_requested=lambda: True)
        events, result_json = executor.run_one("call_c", "todo_read", {})
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], TOOL_ABORTED_BEFORE_DISPATCH)
        self.assertEqual(invoked, [])

    def test_output_contract_sanitizes_non_serializable_data(self) -> None:
        from nova.tools.workspace import ToolResult

        root = Path(tempfile.mkdtemp())
        tools = WorkspaceTools(root, permission_mode="bypass_permissions")

        def dirty_run(name, arguments):
            return ToolResult(
                tool=name,
                title="t",
                output="ok",
                ok=True,
                data={"blob": object(), "exit_code": 0},
            )

        tools.run = dirty_run  # type: ignore[method-assign]
        executor = ToolExecutor(tools)
        events, result_json = executor.run_one("call_o", "todo_read", {})
        result = json.loads(result_json)  # 若未净化，这里直接 TypeError
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["error_code"], INVALID_TOOL_OUTPUT)
        self.assertEqual(result["data"]["exit_code"], 0)
        self.assertIn("object at", result["data"]["blob"])


class ToolResultEventTest(unittest.TestCase):
    """tool_done 事件与 result JSON 的字段一致性。"""

    def test_events_carry_error_code(self) -> None:
        executor = _make_executor()
        events, _ = executor.run_one("call_e", "read_file", {})
        done = [e for e in events if e["type"] == "tool_done"][0]
        self.assertFalse(done["ok"])
        self.assertEqual(done["data"]["error_code"], INVALID_ARGS)


if __name__ == "__main__":
    unittest.main()
