import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from picli.cli.main import main
from picli.agent.core import Agent
from picli.memory.compaction import Compactor
from picli.config import AppConfig
from picli.providers.client import AnthropicProvider, MockProvider, create_provider
from picli.tools.registry import ToolRegistry, ToolSpec
from picli.tools.builtin import Tools, sanitize_process_output


class PlatformTests(unittest.TestCase):
    def test_process_output_removes_curses_terminal_controls(self) -> None:
        output = (
            "before\x1b[?1049h\x1b[2J\x1b[?25l\x1b=game"
            "\x1b[?25h\x1b[?1049l\x08after"
        )
        sanitized = sanitize_process_output(output)
        self.assertEqual(sanitized, "beforegameafter")
        self.assertNotIn("\x1b", sanitized)

    def test_tool_registry_validates_and_dispatches_registered_tool(self) -> None:
        registry = ToolRegistry()
        spec = ToolSpec("echo", "Echo text", {"text": "Text to echo"})
        registry.register(spec, lambda arguments: arguments["text"])
        self.assertEqual(registry.execute("echo", {"text": "hello"}), "hello")
        self.assertEqual(registry.schemas()[0]["function"]["name"], "echo")
        with self.assertRaisesRegex(ValueError, "Expected arguments"):
            registry.execute("echo", {"wrong": "hello"})
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(spec, lambda arguments: "duplicate")

    def test_agent_binds_dynamic_tool_schema_to_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tools = Tools(root, False, 1)
            tools.register(
                ToolSpec("echo", "Echo text", {"text": "Text to echo"}),
                lambda arguments: arguments["text"],
            )
            provider = MockProvider()
            Agent(root, provider, tools, "registry", 2, Compactor(32000, 0.75, 4))
            self.assertIn("echo", [schema["function"]["name"] for schema in provider.tool_schemas])
            self.assertEqual(tools.execute("echo", {"text": "registered"}), "registered")

    def test_mock_provider_performs_deterministic_tool_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = create_provider("mock", "", "", "", False)
            agent = Agent(
                root,
                provider,
                Tools(root, True, 1),
                "mock",
                3,
                Compactor(32000, 0.75, 4),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                finish = agent.run("mock:write result.txt::deterministic")
            self.assertEqual(finish, "model_done")
            self.assertEqual(root.joinpath("result.txt").read_text(), "deterministic")
            self.assertEqual(agent.messages[-1]["content"], "Mock tool result:\nwrite: result.txt succeeded")

    def test_anthropic_provider_converts_messages_tools_and_auth(self) -> None:
        provider = AnthropicProvider("", "", "oauth-token", "claude-test")
        provider.set_tool_schemas(
            [ToolSpec("read", "Read file", {"path": "File path"}).schema()]
        )
        payload = provider._payload(
            [
                {"role": "system", "content": "system rules"},
                {"role": "user", "content": "read it"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "hello"},
            ],
            tools=True,
            stream=True,
        )
        self.assertEqual(payload["system"], "system rules")
        self.assertEqual(payload["tools"][0]["name"], "read")
        self.assertEqual(payload["messages"][-1]["content"][0]["type"], "tool_result")
        self.assertEqual(provider._headers()["Authorization"], "Bearer oauth-token")
        message = provider._message_from_blocks(
            [{"type": "tool_use", "id": "x", "name": "read", "input": {"path": "a.txt"}}],
            tools=True,
        )
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read")

    def test_config_merges_workspace_then_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.joinpath(".pi-python").mkdir()
            root.joinpath(".pi-python/config.json").write_text(
                json.dumps({"provider": "anthropic", "model": "workspace-model", "max_turns": 7}),
                encoding="utf-8",
            )
            with patch.object(Path, "home", return_value=root / "empty-home"):
                config = AppConfig.load(
                    root,
                    environ={
                        "MODEL": "environment-model",
                        "PERMISSION_MODE": "read-only",
                        "OPENAI_API_KEY": "wrong-provider-key",
                        "ANTHROPIC_API_KEY": "anthropic-key",
                    },
                )
            self.assertEqual(config.model, "environment-model")
            self.assertEqual(config.max_turns, 7)
            self.assertEqual(config.permission_mode, "read-only")
            self.assertEqual(config.api_key, "anthropic-key")
            self.assertEqual(config.public()["api_key"], "configured")

    def test_removed_judge_settings_in_legacy_config_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({
                "provider": "mock", "goal_cycles": 3,
                "goal_judge_model": "obsolete-model",
                "goal_judge_base_url": "http://127.0.0.1:11434/v1",
                "goal_judge_api_key": "obsolete-key",
            }), encoding="utf-8")
            with patch.object(Path, "home", return_value=root / "empty-home"):
                config = AppConfig.load(root, config_path, environ={
                    "GOAL_JUDGE_MODEL": "obsolete-model",
                })
            self.assertEqual(config.goal_cycles, 3)
            self.assertFalse(any(key.startswith("goal_judge_") for key in config.public()))
            self.assertNotIn("obsolete-key", json.dumps(config.public()))

    def test_one_shot_json_output_is_one_valid_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            arguments = [
                "picli",
                "--workspace",
                directory,
                "--provider",
                "mock",
                "--output-format",
                "json",
                "prompt",
                "hello",
            ]
            with patch.object(sys, "argv", arguments), contextlib.redirect_stdout(output):
                self.assertEqual(main(), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["ok"])
            self.assertEqual(report["assistant"], "Mock response: hello")
            self.assertEqual(report["provider"], "mock")


if __name__ == "__main__":
    unittest.main()
