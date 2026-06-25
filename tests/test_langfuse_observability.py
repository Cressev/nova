from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nova.observability.langfuse import (
    LangfuseConfig,
    LangfuseTraceRecorder,
    load_langfuse_config,
    update_langfuse_secrets,
)


class LangfuseObservabilityTest(unittest.TestCase):
    def test_secret_file_loader_redacts_status_and_reports_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime-secrets.json"

            status = update_langfuse_secrets(
                path,
                public_key="pk-test",
                secret_key="sk-test",
                host="https://cloud.langfuse.com",
            )

            self.assertTrue(status["configured"])
            self.assertTrue(status["public_key_set"])
            self.assertTrue(status["secret_key_set"])
            self.assertNotIn("pk-test", str(status))
            self.assertNotIn("sk-test", str(status))
            config = load_langfuse_config(path)
            self.assertEqual(config.host, "https://cloud.langfuse.com")
            self.assertTrue(config.configured)

    def test_loader_accepts_langfuse_base_url_alias_from_console_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime-secrets.json"
            path.write_text(
                '{"LANGFUSE_PUBLIC_KEY":"pk-test","LANGFUSE_SECRET_KEY":"sk-test",'
                '"LANGFUSE_BASE_URL":"https://hipaa.cloud.langfuse.com"}',
                encoding="utf-8",
            )

            config = load_langfuse_config(path)

            self.assertTrue(config.configured)
            self.assertEqual(config.host, "https://hipaa.cloud.langfuse.com")

    def test_recorder_creates_turn_generation_tool_and_end_observations(self) -> None:
        fake_client = _FakeLangfuseClient()
        recorder = LangfuseTraceRecorder(
            LangfuseConfig(
                public_key="pk-test",
                secret_key="sk-test",
                host="https://cloud.langfuse.com",
            ),
            client=fake_client,
        )

        turn_id = recorder.start_turn(
            session_id="chat_1",
            turn_id="turn_1",
            user_input="读取 README",
            metadata={"workspace": "/tmp/project"},
        )
        recorder.record_generation(
            turn_id=turn_id,
            name="tool-decision",
            model="glm-4.7",
            input_messages=[{"role": "user", "content": "读取 README"}],
            output="准备读取",
            tool_calls=[{"tool": "read_file"}],
        )
        recorder.record_tool(
            turn_id=turn_id,
            call_id="tool_1",
            tool="read_file",
            arguments={"path": "README.md"},
            output="Nova",
            ok=True,
            metadata={"duration_ms": 12},
        )
        recorder.end_turn(turn_id=turn_id, output="完成", status="ok")

        session = fake_client.roots[0]
        self.assertEqual(session.name, "nova.agent.session")
        self.assertEqual(session.metadata["session_id"], "chat_1")
        root = session.children[0]
        self.assertEqual(root.name, "nova.agent.turn")
        self.assertEqual(root.input["user_input"], "读取 README")
        child_names = [child.name for child in root.children]
        self.assertIn("tool-decision", child_names)
        self.assertIn("tool.read_file", child_names)
        self.assertTrue(root.ended)
        self.assertFalse(session.ended)
        self.assertEqual(fake_client.flush_count, 1)

    def test_recorder_groups_multiple_turns_under_one_session_observation(self) -> None:
        fake_client = _FakeLangfuseClient()
        recorder = LangfuseTraceRecorder(
            LangfuseConfig(public_key="pk-test", secret_key="sk-test"),
            client=fake_client,
        )

        first_turn = recorder.start_turn(session_id="chat_1", turn_id="turn_1", user_input="第一轮")
        second_turn = recorder.start_turn(session_id="chat_1", turn_id="turn_2", user_input="第二轮")
        recorder.end_turn(turn_id=first_turn, output="第一轮完成")
        recorder.end_turn(turn_id=second_turn, output="第二轮完成")

        self.assertEqual([root.name for root in fake_client.roots], ["nova.agent.session"])
        session = fake_client.roots[0]
        self.assertEqual(session.metadata["session_id"], "chat_1")
        self.assertFalse(session.ended)
        self.assertEqual([child.name for child in session.children], ["nova.agent.turn", "nova.agent.turn"])
        self.assertEqual([child.metadata["turn_id"] for child in session.children], ["turn_1", "turn_2"])
        self.assertTrue(all(child.ended for child in session.children))

    def test_recorder_noops_when_not_configured(self) -> None:
        recorder = LangfuseTraceRecorder(LangfuseConfig())

        turn_id = recorder.start_turn(session_id="s", turn_id="t", user_input="x")
        recorder.record_generation(turn_id=turn_id, name="g", model="m", input_messages=[], output="", tool_calls=[])
        recorder.record_tool(turn_id=turn_id, call_id="c", tool="read_file", arguments={}, output="", ok=True)
        recorder.end_turn(turn_id=turn_id, output="")

        self.assertEqual(turn_id, "t")


class _FakeObservation:
    def __init__(self, name: str, **kwargs) -> None:
        self.name = name
        self.input = kwargs.get("input")
        self.output = kwargs.get("output")
        self.metadata = kwargs.get("metadata") or {}
        self.children: list[_FakeObservation] = []
        self.ended = False

    def start_observation(self, name: str, **kwargs):
        child = _FakeObservation(name, **kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs):
        if "output" in kwargs:
            self.output = kwargs["output"]
        if "metadata" in kwargs:
            self.metadata.update(kwargs["metadata"] or {})
        return self

    def end(self):
        self.ended = True
        return self


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.roots: list[_FakeObservation] = []
        self.flush_count = 0

    def start_observation(self, name: str, **kwargs):
        root = _FakeObservation(name, **kwargs)
        self.roots.append(root)
        return root

    def flush(self):
        self.flush_count += 1


if __name__ == "__main__":
    unittest.main()
