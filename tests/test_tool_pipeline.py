"""工具管线对齐 dsh 的边际测试（dsh 工具面：read/write/edit/glob/grep/
bash/todo_write/web_fetch/web_search/memory_write/memory_remove）。

覆盖：参数契约验证（INVALID_ARGS + path 限定违规）、无损快照边界、超时
预算（TIMEOUT）、未知工具（UNKNOWN_TOOL）、派发前取消
（ABORTED_BEFORE_DISPATCH）、权限拒绝码、输出契约（INVALID_TOOL_OUTPUT）、
schema 注册期静态校验、bash 渲染（stderr 段/退出标记/非零不失败）、
read 行号信封、edit 唯一性。
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
from nova.tools.workspace import TOOL_SPECS, ToolResult, ToolExecutionError, WorkspaceTools


def _make_tools(**kwargs) -> WorkspaceTools:
    root = Path(tempfile.mkdtemp())
    return WorkspaceTools(root, permission_mode="bypass_permissions", **kwargs)


def _make_executor(**kwargs) -> ToolExecutor:
    return ToolExecutor(_make_tools(), **kwargs)


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
        schema = TOOL_SPECS["read"].json_schema
        violations = validate_arguments(schema, {})
        self.assertEqual(violations, ['missing required property "arguments.file_path"'])

    def test_wrong_type_violation(self) -> None:
        schema = TOOL_SPECS["read"].json_schema
        violations = validate_arguments(schema, {"file_path": 123})
        self.assertEqual(violations, ['"arguments.file_path" must be a string'])

    def test_enum_violation_in_nested_array_item(self) -> None:
        schema = TOOL_SPECS["todo_write"].json_schema
        violations = validate_arguments(
            schema,
            {"todos": [{"content": "任务", "status": "done"}]},
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("arguments.todos[0].status", violations[0])
        self.assertIn("must be one of", violations[0])

    def test_additional_properties_false_rejects_extras(self) -> None:
        schema = TOOL_SPECS["read"].json_schema
        violations = validate_arguments(schema, {"file_path": "a.txt", "extra": 1})
        self.assertEqual(
            violations,
            ['"arguments.extra" is not a declared property (additionalProperties: false)'],
        )

    def test_bool_is_not_integer(self) -> None:
        violations = validate_value({"type": "integer"}, True, "arguments.n")
        self.assertEqual(violations, ['"arguments.n" must be a integer'])

    def test_one_of_branch_matching(self) -> None:
        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        self.assertEqual(validate_value(schema, "x", "arguments.v"), [])
        self.assertEqual(validate_value(schema, 3, "arguments.v"), [])
        violations = validate_value(schema, True, "arguments.v")
        self.assertEqual(len(violations), 1)
        self.assertIn("exactly one oneOf branch", violations[0])

    def test_const_violation_format(self) -> None:
        violations = validate_value({"type": "string", "const": "exact"}, "other", "arguments.v")
        self.assertEqual(violations, ['"arguments.v" must be "exact"'])

    def test_bash_description_is_required(self) -> None:
        schema = TOOL_SPECS["bash"].json_schema
        self.assertIn("description", schema["required"])
        self.assertIn("timeoutMs", schema["properties"])


class SnapshotBoundaryTest(unittest.TestCase):
    """无损 JSON 快照：隔离 + 非序列化提前爆炸。"""

    def test_snapshot_returns_isolated_copy(self) -> None:
        original = {"file_path": "a.txt", "nested": {"list": [1, 2]}}
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
        arguments = {"file_path": "a.txt"}
        arguments["bad"] = object()  # type: ignore[assignment]
        events, result_json = executor.run_one("call_x", "read", arguments)
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
        events, result_json = self.executor.run_one("call_v", "read", {})
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], INVALID_ARGS)
        self.assertIn("missing required property", result["error"])
        self.assertIn('"arguments.file_path"', result["error"])
        self.assertEqual(invoked, [])

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
            "call_p", "write", {"file_path": "x.txt", "content": "hi"}
        )
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], TOOL_PERMISSION_DENIED)

    def test_timeout_budget_returns_promptly(self) -> None:
        tools = _make_tools()
        original_run = tools.run

        def slow_run(name, arguments):
            time.sleep(1.5)
            return original_run(name, arguments)

        tools.run = slow_run  # type: ignore[method-assign]
        spec = TOOL_SPECS["glob"]
        TOOL_SPECS["glob"] = dataclasses.replace(spec, timeout_ms=100)
        try:
            executor = ToolExecutor(tools)
            started = time.perf_counter()
            events, result_json = executor.run_one(
                "call_t", "glob", {"pattern": "*"}
            )
            elapsed = time.perf_counter() - started
            result = json.loads(result_json)
            self.assertFalse(result["ok"])
            self.assertEqual(result["data"]["error_code"], TOOL_TIMEOUT)
            self.assertEqual(result["data"]["timeout_ms"], 100)
            self.assertLess(elapsed, 1.2, "超时应立即返回，不等慢 body")
        finally:
            TOOL_SPECS["glob"] = spec

    def test_cancel_before_dispatch_skips_body(self) -> None:
        invoked: list[str] = []
        tools = _make_tools()
        original_run = tools.run

        def probe_run(name, arguments):
            invoked.append(name)
            return original_run(name, arguments)

        tools.run = probe_run  # type: ignore[method-assign]
        executor = ToolExecutor(tools, cancel_requested=lambda: True)
        events, result_json = executor.run_one("call_c", "glob", {"pattern": "*"})
        result = json.loads(result_json)
        self.assertFalse(result["ok"])
        self.assertEqual(result["data"]["error_code"], TOOL_ABORTED_BEFORE_DISPATCH)
        self.assertEqual(invoked, [])

    def test_output_contract_sanitizes_non_serializable_data(self) -> None:
        tools = _make_tools()

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
        events, result_json = executor.run_one("call_o", "glob", {"pattern": "*"})
        result = json.loads(result_json)  # 若未净化，这里直接 TypeError
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["error_code"], INVALID_TOOL_OUTPUT)
        self.assertEqual(result["data"]["exit_code"], 0)
        self.assertIn("object at", result["data"]["blob"])


class DshToolSemanticsTest(unittest.TestCase):
    """dsh 工具语义对齐：read 信封 / edit 唯一性 / bash 标记 / memory 分层。"""

    def setUp(self) -> None:
        self.tools = _make_tools()
        (self.tools.project_root / "a.txt").write_text(
            "alpha\nbeta cat\ngamma\n" * 3, encoding="utf-8"
        )

    def test_read_line_numbered_envelope_with_footer(self) -> None:
        result = self.tools.run("read", {"file_path": "a.txt", "limit": 4})
        self.assertTrue(result.ok)
        self.assertIn("<path>a.txt</path>", result.output)
        self.assertIn("<type>file</type>", result.output)
        self.assertIn("1: alpha", result.output)
        self.assertIn("(Showing lines 1-4 of 9. Use offset=5 to continue.)", result.output)

    def test_read_offset_continuation(self) -> None:
        result = self.tools.run("read", {"file_path": "a.txt", "offset": 8})
        self.assertIn("8: beta cat", result.output)
        self.assertIn("(End of file - total 9 lines)", result.output)

    def test_edit_uniqueness_enforced_with_line_numbers(self) -> None:
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run(
                "edit",
                {"file_path": "a.txt", "old_string": "beta cat", "new_string": "beta dog"},
            )
        self.assertIn("Multiple occurrences", str(ctx.exception))
        self.assertIn("[2, 5, 8]", str(ctx.exception))

    def test_edit_replace_all_and_empty_new_string_deletes(self) -> None:
        result = self.tools.run(
            "edit",
            {"file_path": "a.txt", "old_string": "beta cat", "new_string": "beta dog", "replace_all": True},
        )
        self.assertEqual(result.data["replacements"], 3)
        result2 = self.tools.run(
            "edit",
            {"file_path": "a.txt", "old_string": "beta dog", "new_string": "", "replace_all": True},
        )
        self.assertEqual(result2.data["replacements"], 3)
        after = (self.tools.project_root / "a.txt").read_text(encoding="utf-8")
        self.assertNotIn("beta", after)

    def test_edit_rejects_identical_strings(self) -> None:
        with self.assertRaises(ToolExecutionError) as ctx:
            self.tools.run(
                "edit",
                {"file_path": "a.txt", "old_string": "alpha", "new_string": "alpha"},
            )
        self.assertIn("must differ", str(ctx.exception))

    def test_bash_nonzero_exit_is_not_tool_failure(self) -> None:
        result = self.tools.run(
            "bash",
            {"command": "echo hi; echo err >&2; exit 3", "description": "Test exit markers"},
        )
        self.assertTrue(result.ok, "非零退出不是工具失败（dsh 语义）")
        self.assertIn("[stderr]\nerr", result.output)
        self.assertTrue(result.output.rstrip().endswith("[exit code: 3]"), "退出标记必须在最后")
        self.assertEqual(result.data["exitCode"], 3)

    def test_bash_no_output_marker(self) -> None:
        result = self.tools.run(
            "bash",
            {"command": "true", "description": "No output command"},
        )
        self.assertEqual(result.output, "(no output)")

    def test_bash_timeout_marker(self) -> None:
        result = self.tools.run(
            "bash",
            {"command": "sleep 5", "timeoutMs": 200, "description": "Sleep to test timeout"},
        )
        self.assertTrue(result.ok)
        self.assertIn("[timed out after 200ms]", result.output)

    def test_memory_write_then_remove_roundtrip(self) -> None:
        wrote = self.tools.run(
            "memory_write",
            {"scope": "project", "title": "对齐测试", "content": "分层记忆正文"},
        )
        self.assertTrue(wrote.ok)
        self.assertIn("Memory (project) saved", wrote.output)
        items_dir = self.tools.project_root / ".nova-memory" / "items"
        index = (self.tools.project_root / ".nova-memory" / "index.md").read_text(encoding="utf-8")
        self.assertIn("对齐测试", index)
        # 索引段渲染（进系统提示词）
        from nova.memory import layered

        section = layered.render_section(self.tools.project_root)
        self.assertIn("对齐测试", section)
        # read 工具可读条目详情（dsh：标准 read 读 items）
        item_files = list(items_dir.glob("*.md"))
        self.assertEqual(len(item_files), 1)
        rel = item_files[0].relative_to(self.tools.project_root).as_posix()
        read_back = self.tools.run("read", {"file_path": rel})
        self.assertIn("分层记忆正文", read_back.output)
        removed = self.tools.run(
            "memory_remove", {"scope": "project", "id": item_files[0].stem}
        )
        self.assertTrue(removed.ok)
        self.assertIn("removed", removed.output)

    def test_glob_returns_files_only_mtime_order_cap(self) -> None:
        root = self.tools.project_root
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("x", encoding="utf-8")
        result = self.tools.run("glob", {"pattern": "**/*"})
        self.assertNotIn(".git/", result.output)
        self.assertIn("a.txt", result.output)

    def test_grep_grouped_by_file_with_line_numbers(self) -> None:
        result = self.tools.run("grep", {"pattern": "alpha", "include": "*.txt"})
        self.assertIn("a.txt:", result.output)
        self.assertIn("1: alpha", result.output)


class ToolResultEventTest(unittest.TestCase):
    """tool_done 事件与 result JSON 的字段一致性。"""

    def test_events_carry_error_code(self) -> None:
        executor = _make_executor()
        events, _ = executor.run_one("call_e", "read", {})
        done = [e for e in events if e["type"] == "tool_done"][0]
        self.assertFalse(done["ok"])
        self.assertEqual(done["data"]["error_code"], INVALID_ARGS)

    def test_bash_title_uses_description(self) -> None:
        executor = _make_executor()
        events, _ = executor.run_one(
            "call_b",
            "bash",
            {"command": "echo hi", "description": "Say hello to test titles"},
        )
        start = [e for e in events if e["type"] == "tool_start"][0]
        self.assertEqual(start["title"], "Say hello to test titles")


if __name__ == "__main__":
    unittest.main()
