"""workflow 脚本编排（dsh workflow 对齐）单测。

不依赖真实模型：spawn_agent 用假实现；宿主需要 node（无 node 则跳过——
CI 环境以真机为准，本机有 node）。
"""

from __future__ import annotations

import shutil
import unittest

from nova.workflow.orchestrator import (
    WorkflowScriptError,
    extract_json_object,
    run_workflow_script,
    validate_schema_subset,
)

NODE = shutil.which("node")


def fake_spawn(prompt: str, opts: dict) -> dict:
    if "FAIL" in prompt:
        return {"ok": False, "error": "boom", "result": None}
    if opts.get("schema"):
        return {"ok": True, "result": {"capital": "巴黎"}, "error": None}
    return {"ok": True, "result": prompt.upper(), "error": None}


@unittest.skipIf(NODE is None, "node 不在 PATH，跳过脚本编排宿主测试")
class WorkflowScriptOrchestrationTest(unittest.TestCase):
    def test_hooks_semantics(self) -> None:
        script = """
phase('stage-1');
const a = await agent('hello');
const failed = await agent('FAIL me');
const withSchema = await agent('capital', {schema: {type:'object', properties:{capital:{type:'string'}}, required:['capital'], additionalProperties:false}});
const par = await parallel([() => agent('alpha'), () => Promise.resolve(7), () => { throw new Error('x') }]);
const pipe = await pipeline([1, 2], (p) => p * 2, (p) => p + 100);
const pipeDropped = await pipeline([1, 2], (p) => { if (p === 1) throw new Error('drop'); return p; });
log('done');
return {a, failed, withSchema, par, pipe, pipeDropped};
"""
        out = run_workflow_script(script, {"k": 1}, spawn_agent=fake_spawn, node_bin=NODE)
        value = out["value"]
        self.assertEqual(value["a"], "HELLO")
        self.assertIsNone(value["failed"])  # 失败 agent → null
        self.assertEqual(value["withSchema"], {"capital": "巴黎"})  # schema 结果是对象
        self.assertEqual(value["par"], ["ALPHA", 7, None])  # 抛错 thunk → null
        self.assertEqual(value["pipe"], [102, 104])  # (p*2)+100
        self.assertEqual(value["pipeDropped"], [None, 2])  # 阶段抛错→该 item null
        self.assertEqual([e["title"] for e in out["events"] if e["kind"] == "phase"], ["stage-1"])
        self.assertEqual(len([a for a in out["agents"] if a["ok"]]), 3)
        self.assertEqual(len([a for a in out["agents"] if not a["ok"]]), 1)

    def test_args_exposed_and_return_value(self) -> None:
        script = "return { got: args.k, doubled: args.k * 2 };"
        out = run_workflow_script(script, {"k": 21}, spawn_agent=fake_spawn, node_bin=NODE)
        self.assertEqual(out["value"], {"got": 21, "doubled": 42})

    def test_misused_hook_kills_script(self) -> None:
        with self.assertRaises(WorkflowScriptError) as ctx:
            run_workflow_script("const x = await agent(123); return x;", {}, spawn_agent=fake_spawn, node_bin=NODE)
        self.assertIn("non-empty prompt", str(ctx.exception))

    def test_syntax_error_kills_script(self) -> None:
        with self.assertRaises(WorkflowScriptError):
            run_workflow_script("return {{{;", {}, spawn_agent=fake_spawn, node_bin=NODE)

    def test_no_fs_or_require_in_sandbox(self) -> None:
        script = "const fs = require('fs'); return 1;"
        with self.assertRaises(WorkflowScriptError):
            run_workflow_script(script, {}, spawn_agent=fake_spawn, node_bin=NODE)

    def test_total_timeout(self) -> None:
        def slow_spawn(prompt: str, opts: dict) -> dict:
            import time as _t
            _t.sleep(3)
            return {"ok": True, "result": "late", "error": None}

        with self.assertRaises(WorkflowScriptError) as ctx:
            run_workflow_script(
                "return await agent('slow');",
                {},
                spawn_agent=slow_spawn,
                node_bin=NODE,
                total_timeout_s=1.5,
            )
        self.assertIn("超时", str(ctx.exception))


class SchemaSubsetTest(unittest.TestCase):
    def test_object_valid_and_invalid(self) -> None:
        schema = {
            "type": "object",
            "properties": {"capital": {"type": "string"}},
            "required": ["capital"],
            "additionalProperties": False,
        }
        self.assertEqual(validate_schema_subset({"capital": "巴黎"}, schema), [])
        self.assertTrue(validate_schema_subset({"capital": 1}, schema))
        self.assertTrue(validate_schema_subset({"capital": "x", "extra": 1}, schema))
        self.assertTrue(validate_schema_subset({}, schema))

    def test_enum_const_oneof(self) -> None:
        self.assertEqual(validate_schema_subset("a", {"enum": ["a", "b"]}), [])
        self.assertTrue(validate_schema_subset("c", {"enum": ["a", "b"]}))
        self.assertEqual(validate_schema_subset(2, {"const": 2}), [])
        oneof = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        self.assertEqual(validate_schema_subset("x", oneof), [])
        self.assertTrue(validate_schema_subset(True, oneof))  # bool 不算 integer

    def test_array_items(self) -> None:
        schema = {"type": "array", "items": {"type": "integer"}}
        self.assertEqual(validate_schema_subset([1, 2], schema), [])
        self.assertTrue(validate_schema_subset([1, "2"], schema))

    def test_unsupported_keys_rejected(self) -> None:
        self.assertTrue(validate_schema_subset("x", {"pattern": "^x$"}))
        self.assertTrue(validate_schema_subset(5, {"minimum": 1}))


class ExtractJsonTest(unittest.TestCase):
    def test_fenced_and_noisy_output(self) -> None:
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json_object('前置说明 {"a": {"b": 2}} 尾部'), {"a": {"b": 2}})
        with self.assertRaises(ValueError):
            extract_json_object("没有任何 JSON")


if __name__ == "__main__":
    unittest.main()
