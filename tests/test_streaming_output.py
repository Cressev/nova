"""真流式输出测试：决策阶段逐字下发、<tool_call>/<tool_calls> 门控、
工具后回答流式、原生 tool_calls 跨块聚合。"""

from __future__ import annotations

import asyncio
import unittest

from nova.runtime.agent import _ToolCallGate, CodexLikeAgentRuntime
from nova.runtime.loop import AgentLoop


class ToolCallGateTest(unittest.TestCase):
    def test_plain_text_passes_through_immediately(self) -> None:
        gate = _ToolCallGate()
        emitted: list[str] = []
        for chunk in ["你好", "，我是", "Nova"]:
            emitted.extend(gate.feed(chunk))
        # 每块喂入后立即放行（不被攒住）——最后一块若非标记前缀也应放空 buffer
        emitted.extend(gate.flush())
        self.assertEqual("".join(emitted), "你好，我是Nova")
        self.assertFalse(gate.tool_detected)

    def test_marker_split_across_chunks_is_withheld(self) -> None:
        gate = _ToolCallGate()
        emitted: list[str] = []
        for chunk in ["查一下\n<tool_ca", 'll>{"name":"bash"}</tool_', "call>", "尾部不外放"]:
            emitted.extend(gate.feed(chunk))
        emitted.extend(gate.flush())
        self.assertEqual("".join(emitted), "查一下\n")
        self.assertTrue(gate.tool_detected)
        self.assertIn("尾部不外放", gate.full_text)

    def test_plural_marker_withheld(self) -> None:
        gate = _ToolCallGate()
        emitted: list[str] = []
        for chunk in ["<tool_", 'calls>[{"tool":"read"}]</tool_calls>']:
            emitted.extend(gate.feed(chunk))
        self.assertEqual("".join(emitted), "")
        self.assertTrue(gate.tool_detected)

    def test_dangling_prefix_flushed_at_end(self) -> None:
        gate = _ToolCallGate()
        emitted = gate.feed("答案<tool")
        self.assertEqual("".join(emitted), "答案")
        self.assertEqual(gate.flush(), ["<tool"])


class _FakeProvider:
    """逐块回放的假流：验证 delta 即时穿透（不等待整段完成）。"""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.is_configured = lambda: True

    async def stream_with_tools(self, messages, *, tools=None):
        for chunk in self._chunks:
            yield {"type": "delta", "text": chunk}
        yield {"type": "decision", "content": "".join(self._chunks), "tool_calls": []}

    async def stream(self, messages):
        for chunk in self._chunks:
            yield chunk


class StreamingDecisionTest(unittest.TestCase):
    def _runtime(self, chunks: list[str]) -> CodexLikeAgentRuntime:
        import tempfile
        from pathlib import Path

        from nova.tools.workspace import WorkspaceTools

        root = Path(tempfile.mkdtemp())
        runtime = CodexLikeAgentRuntime.__new__(CodexLikeAgentRuntime)
        runtime.provider = _FakeProvider(chunks)
        runtime.tools = WorkspaceTools(root, permission_mode="bypass_permissions")
        runtime.max_tool_rounds = 2
        runtime.default_max_tokens = 512
        return runtime

    def test_decision_phase_streams_deltas_live(self) -> None:
        runtime = self._runtime(["第一段", "第二段", "第三段"])
        events: list[dict] = []

        async def collect() -> None:
            async for event in runtime._stream_tool_decision([]):
                events.append(event)

        asyncio.run(collect())
        deltas = [e["delta"] for e in events if e["type"] == "assistant_delta"]
        self.assertEqual(deltas, ["第一段", "第二段", "第三段"])
        decision = next(e for e in events if e["type"] == "decision")
        self.assertEqual(decision["content"], "第一段第二段第三段")
        self.assertEqual(decision["tool_calls"], [])

    def test_loop_streams_answer_without_waiting(self) -> None:
        runtime = self._runtime(["开头", "，结尾"])
        loop = AgentLoop(runtime)
        events: list[dict] = []

        async def collect() -> None:
            async for event in loop.run([], latest_user="讲个故事", trace_turn_id="t"):
                events.append(event)

        runtime._system_prompt = lambda: "sys"  # type: ignore[method-assign]
        runtime._skill_response_from_dollar = lambda _u: ""  # type: ignore[method-assign]
        runtime._direct_tool_calls_from_user = lambda _u: []  # type: ignore[method-assign]
        runtime._trace_generation = lambda *_a, **_k: None  # type: ignore[method-assign]
        asyncio.run(collect())
        deltas = [e["delta"] for e in events if e["type"] == "assistant_delta"]
        self.assertEqual("".join(deltas), "开头，结尾")
        done = next(e for e in events if e["type"] == "assistant_done_content")
        self.assertEqual(done["content"], "开头，结尾")

    def test_tagged_tool_text_is_not_streamed_to_user(self) -> None:
        chunks = ["我想执行<tool_ca", 'll>{"tool":"bash","arguments":{"command":"pwd"}}</tool_call>']
        runtime = self._runtime(chunks)
        events: list[dict] = []

        async def collect() -> None:
            async for event in runtime._stream_tool_decision([]):
                events.append(event)

        asyncio.run(collect())
        deltas = "".join(e["delta"] for e in events if e["type"] == "assistant_delta")
        self.assertNotIn("<tool_call>", deltas)
        decision = next(e for e in events if e["type"] == "decision")
        self.assertEqual(decision["tool_calls"][0]["tool"], "bash")


class ProviderStreamWithToolsTest(unittest.TestCase):
    def test_tool_call_fragments_accumulate_by_index(self) -> None:
        from nova.providers.bigmodel import BigModelProvider

        provider = BigModelProvider.__new__(BigModelProvider)

        class _Piece:
            def __init__(self, index, piece_id=None, name=None, arguments=None):
                self.index = index
                self.id = piece_id
                self.function = {"name": name, "arguments": arguments} if (name or arguments) else {}

        class _Delta:
            def __init__(self, tool_calls=None, content=None):
                self.tool_calls = tool_calls or []
                self.content = content

        class _Choice:
            def __init__(self, delta):
                self.delta = delta

        class _Chunk:
            def __init__(self, delta):
                self.choices = [_Choice(delta)]

        class _Stream:
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for chunk in self._chunks:
                    yield chunk

        provider._api_key = lambda: "k"  # type: ignore[method-assign]
        provider.api_key_env = "BIGMODEL_API_KEY"
        provider.model = "glm-4.7"

        async def fake_create(**_kwargs):
            return _Stream(
                [
                    _Chunk(_Delta(content="思考中")),
                    _Chunk(_Delta(tool_calls=[_Piece(0, piece_id="call_1", name="ba")])),
                    _Chunk(_Delta(tool_calls=[_Piece(0, name="sh")])),
                    _Chunk(_Delta(tool_calls=[_Piece(0, arguments='{"comm')])),
                    _Chunk(_Delta(tool_calls=[_Piece(0, arguments='and":"pwd"}')])),
                ]
            )

        class _Client:
            class chat:
                class completions:
                    create = staticmethod(fake_create)

        provider._openai_client = lambda _key: _Client()  # type: ignore[method-assign]
        events = []

        async def collect():
            async for event in provider.stream_with_tools([]):
                events.append(event)

        asyncio.run(collect())
        deltas = [e["text"] for e in events if e["type"] == "delta"]
        self.assertEqual(deltas, ["思考中"])
        decision = events[-1]
        self.assertEqual(decision["tool_calls"][0]["tool"], "bash")
        self.assertEqual(decision["tool_calls"][0]["arguments"], {"command": "pwd"})


if __name__ == "__main__":
    unittest.main()


class ToolResultAnswerGateTest(unittest.TestCase):
    """回归：续答阶段模型违规输出 <tool_call> 时不得外流、不得落库。"""

    def _runtime(self, chunks: list[str]) -> CodexLikeAgentRuntime:
        import tempfile
        from pathlib import Path

        from nova.tools.workspace import WorkspaceTools

        root = Path(tempfile.mkdtemp())
        runtime = CodexLikeAgentRuntime.__new__(CodexLikeAgentRuntime)
        runtime.provider = _FakeProvider(chunks)
        runtime.tools = WorkspaceTools(root, permission_mode="bypass_permissions")
        return runtime

    def test_tool_result_answer_withholds_tool_xml(self) -> None:
        chunks = ["我需要重新查询天气信息。<tool_ca", 'll>{"tool":"web_search","arguments":{"query":"今天天气"}}</tool_call>']
        runtime = self._runtime(chunks)
        events: list[dict] = []

        async def collect() -> None:
            async for event in runtime._stream_tool_result_answer([], ['{"ok": false, "error": "搜索失败"}']):
                events.append(event)

        runtime._system_prompt = lambda: "sys"  # type: ignore[method-assign]
        asyncio.run(collect())
        deltas = "".join(e["delta"] for e in events if e["type"] == "assistant_delta")
        done = next(e for e in events if e["type"] == "assistant_done_content")
        self.assertNotIn("<tool_call>", deltas, "工具 XML 不得流向前端")
        self.assertNotIn("<tool_call>", done["content"], "工具 XML 不得进入落库内容")
        self.assertIn("我需要重新查询天气信息", deltas)

    def test_persist_sanitizer_strips_dangling_tags(self) -> None:
        from nova.runtime.session_runner import TOOL_TAG_PATTERN

        raw = '前文 <tool_call>{"tool":"bash"}</tool_call> 后文'
        self.assertEqual(" ".join(TOOL_TAG_PATTERN.sub("", raw).split()), "前文 后文")
        dangling = "只有开头 <tool_calls> {\"a\": 1}"
        self.assertEqual(" ".join(TOOL_TAG_PATTERN.sub("", dangling).split()), "只有开头")


if __name__ == "__main__":
    unittest.main()
