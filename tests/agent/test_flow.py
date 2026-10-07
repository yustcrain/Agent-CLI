import contextlib
import json
import io
import tempfile
import unittest
from pathlib import Path

from picli.agent.acceptance import AcceptanceVerifier, validate_criteria
from picli.agent.core import Agent
from picli.memory.compaction import Compactor
from picli.agent.goal import GoalRunner
from picli.tools.permissions import PermissionManager, PermissionMode
from picli.providers.client import Provider
from picli.skills.registry import SkillRegistry
from picli.cli.terminal import PrefixCompleter, StreamingMarkdownRenderer
from picli.tools.builtin import Tools
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document


class FakeProvider:
    model = "fake"
    demo = False

    def __init__(self, responses: list[dict], criteria: list[dict] | None = None):
        self.responses = responses
        self.criteria = criteria or [
            {
                "id": "AC1",
                "description": "result file exists",
                "check": {"type": "file_exists", "path": "result.txt"},
            }
        ]
        self.contexts: list[list[dict]] = []
        self.summary_calls: list[list[dict]] = []

    def complete(self, messages: list[dict], on_text=None) -> dict:
        self.contexts.append(messages)
        response = self.responses.pop(0)
        if on_text and response.get("content"):
            on_text(response["content"])
        return response

    def summarize(self, messages: list[dict]) -> str:
        self.summary_calls.append(messages)
        return "preserved history summary"

    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        if not allow_manual and any(item["check"]["type"] == "manual" for item in self.criteria):
            raise ValueError("Manual acceptance checks are unavailable in unattended runs")
        return self.criteria


class PocketFlowAgentTests(unittest.TestCase):
    def make_agent(self, root: Path, provider: FakeProvider, max_turns: int = 5) -> Agent:
        return Agent(
            root,
            provider,
            Tools(root, False, 1),
            "test",
            max_turns,
            Compactor(32000, 0.75, 2),
        )

    def test_direct_response_runs_through_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider([{"role": "assistant", "content": "hello"}])
            agent = self.make_agent(Path(directory).resolve(), provider)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                finish_reason = agent.run("hi")
            self.assertEqual(finish_reason, "model_done")
            self.assertIn("agent 本轮结束", output.getvalue())
            self.assertNotIn("agent 本轮暂停", output.getvalue())
            self.assertEqual([message["role"] for message in agent.messages], ["user", "assistant"])
            self.assertEqual(provider.contexts[0][-1]["content"], "hi")

    def test_terminal_completer_uses_dynamic_slash_candidates(self) -> None:
        candidates = ["/help", "/history", "/goal status"]
        completer = PrefixCompleter(lambda: candidates)
        matches = list(
            completer.get_completions(Document("/hi", cursor_position=3), CompleteEvent())
        )
        self.assertEqual([match.text for match in matches], ["/history"])

    def test_streaming_renderer_keeps_plain_mode_for_non_tty_output(self) -> None:
        output = io.StringIO()
        renderer = StreamingMarkdownRenderer(interactive=False)
        with contextlib.redirect_stdout(output):
            renderer.start()
            renderer.write("hel")
            renderer.write("lo")
            renderer.finish()
        self.assertEqual(output.getvalue(), "\nassistant> hello\n")

    def test_tool_call_at_model_turn_limit_is_reported_as_paused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            call = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write-before-limit",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"path": "partial.txt", "content": "partial work"}
                            ),
                        },
                    }
                ],
            }
            agent = self.make_agent(root, FakeProvider([call]), max_turns=1)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                finish_reason = agent.run("start a task")
            self.assertEqual(finish_reason, "turn_limit")
            self.assertIn("agent 本轮暂停", output.getvalue())
            self.assertIn("达到模型调用上限 1/1", output.getvalue())
            self.assertIn("任务可能未完成", output.getvalue())
            self.assertNotIn("agent 本轮结束", output.getvalue())
            self.assertEqual((root / "partial.txt").read_text(), "partial work")

    def test_streaming_provider_assembles_text_and_tool_calls(self) -> None:
        provider = Provider("https://example.test", "key", "model", False)
        events = [
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "type": "function",
                "function": {"name": "write", "arguments": "{\"path\":"},
            }]}}]},
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"arguments": "\"result.txt\",\"content\":\"ok\"}"},
            }]}}]},
        ]
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
        fragments = []
        message = provider._read_stream(io.BytesIO(payload.encode()), fragments.append)
        self.assertEqual("".join(fragments), "hello")
        self.assertEqual(message["content"], "hello")
        self.assertEqual(message["tool_calls"][0]["id"], "call-1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "write")
        self.assertEqual(
            json.loads(message["tool_calls"][0]["function"]["arguments"]),
            {"path": "result.txt", "content": "ok"},
        )

    def test_tool_call_loops_back_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            call = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps({"path": "result.txt", "content": "done"}),
                        },
                    }
                ],
            }
            provider = FakeProvider([call, {"role": "assistant", "content": "finished"}])
            agent = self.make_agent(root, provider)
            agent.run("create a file")
            self.assertEqual((root / "result.txt").read_text(), "done")
            self.assertEqual(provider.contexts[1][-1]["role"], "tool")

    def test_manual_compaction_uses_pocketflow_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([])
            agent = self.make_agent(root, provider)
            for number in range(4):
                agent.messages.extend(
                    [
                        {"role": "user", "content": f"question {number}"},
                        {"role": "assistant", "content": f"answer {number}"},
                    ]
                )
            self.assertTrue(agent.compact(force=True))
            self.assertEqual(agent.summary, "preserved history summary")
            self.assertEqual(len(provider.summary_calls), 1)
            restored = self.make_agent(root, provider)
            self.assertEqual(restored.summary, agent.summary)
            self.assertEqual(len(restored.store.nodes), 8)
            self.assertTrue((root / ".pi-python" / "archives" / "test.jsonl").exists())

    def test_session_tree_can_checkout_branch_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([])
            agent = self.make_agent(root, provider)
            agent.messages.append({"role": "user", "content": "root question"})
            root_id = agent.store.head_id
            agent.messages.append({"role": "assistant", "content": "first answer"})
            first_answer_id = agent.store.head_id
            agent.store.save()

            agent.store.checkout(root_id[:8])
            agent.messages.append({"role": "assistant", "content": "alternative answer"})
            alternative_id = agent.store.head_id
            agent.store.save()

            self.assertEqual(len(agent.store.nodes), 3)
            self.assertEqual(
                [message["content"] for message in agent.store.all_messages],
                ["root question", "alternative answer"],
            )
            tree = agent.store.format_tree()
            self.assertIn(first_answer_id[:8], tree)
            self.assertIn(f"> {alternative_id[:8]}", tree)

            restored = self.make_agent(root, provider)
            self.assertEqual(restored.store.head_id, alternative_id)
            self.assertEqual(len(restored.store.nodes), 3)
            self.assertEqual(restored.messages[-1]["content"], "alternative answer")

    def test_session_fork_copies_only_selected_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([])
            agent = self.make_agent(root, provider)
            agent.messages.append({"role": "user", "content": "question"})
            agent.messages.append({"role": "assistant", "content": "answer"})
            answer_id = agent.store.head_id
            agent.store.save()
            agent.store.checkout(agent.store.nodes[0]["id"])
            agent.messages.append({"role": "assistant", "content": "other"})
            agent.store.save()

            agent.store.fork("experiment", answer_id[:8])
            self.assertEqual(agent.session, "experiment")
            self.assertEqual(len(agent.store.nodes), 2)
            self.assertEqual(
                [message["content"] for message in agent.store.all_messages],
                ["question", "answer"],
            )

    def test_legacy_linear_session_migrates_to_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sessions = root / ".pi-python" / "sessions"
            sessions.mkdir(parents=True)
            sessions.joinpath("test.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "summary": "legacy summary",
                        "messages": [
                            {"role": "user", "content": "legacy question"},
                            {"role": "assistant", "content": "legacy answer"},
                        ],
                    }
                )
            )
            agent = self.make_agent(root, FakeProvider([]))
            self.assertEqual(agent.summary, "legacy summary")
            self.assertEqual(len(agent.store.nodes), 2)
            agent.store.save()
            migrated, _ = agent.store.database.load('test')
            self.assertEqual(json.loads(sessions.joinpath('test.json').read_text())['version'], 2)
            self.assertEqual(migrated["version"], 3)
            self.assertEqual(migrated["head_id"], agent.store.head_id)

    def test_checkout_before_compaction_does_not_reuse_future_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([])
            agent = self.make_agent(root, provider)
            for number in range(4):
                agent.messages.extend(
                    [
                        {"role": "user", "content": f"question {number}"},
                        {"role": "assistant", "content": f"answer {number}"},
                    ]
                )
            early_id = agent.store.nodes[1]["id"]
            self.assertTrue(agent.compact(force=True))
            self.assertEqual(agent.summary, "preserved history summary")
            agent.store.checkout(early_id)
            self.assertEqual(agent.summary, "")
            self.assertEqual(
                [message["content"] for message in agent.messages],
                ["question 0", "answer 0"],
            )

    def test_flow_compacts_before_calling_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([{"role": "assistant", "content": "continued"}])
            agent = Agent(
                root,
                provider,
                Tools(root, False, 1),
                "test",
                5,
                Compactor(100, 0.5, 2),
            )
            for number in range(4):
                agent.messages.extend(
                    [
                        {"role": "user", "content": f"question {number} " + "x" * 80},
                        {"role": "assistant", "content": f"answer {number}"},
                    ]
                )
            agent.run("continue the work")
            self.assertEqual(len(provider.summary_calls), 1)
            self.assertIn("preserved history summary", provider.contexts[0][0]["content"])
            self.assertEqual(
                [message["content"] for message in provider.contexts[0] if message["role"] == "user"],
                ["question 3 " + "x" * 80, "continue the work"],
            )

    def test_goal_continues_after_agent_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider(
                [
                    {"role": "assistant", "content": "I inspected the project."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "write-goal",
                                "type": "function",
                                "function": {
                                    "name": "write",
                                    "arguments": json.dumps(
                                        {"path": "result.txt", "content": "verified artifact"}
                                    ),
                                },
                            }
                        ],
                    },
                    {"role": "assistant", "content": "Implementation finished."},
                ],
            )
            agent = self.make_agent(root, provider)
            goal = GoalRunner(agent, 5).start("Finish the project change")
            self.assertEqual(goal.status, "complete")
            self.assertEqual(goal.cycle, 2)
            self.assertEqual(len(provider.contexts), 3)
            self.assertEqual(goal.verification[0]["status"], "passed")
            self.assertTrue((root / "result.txt").exists())
            self.assertIn("FAILED", provider.contexts[1][-1]["content"])

    def test_goal_pauses_at_cycle_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider(
                [{"role": "assistant", "content": "Partial work."}],
            )
            agent = self.make_agent(root, provider)
            goals = GoalRunner(agent, 1)
            goal = goals.start("Long task")
            self.assertEqual(goal.status, "paused")
            self.assertIn("failed checks", goal.last_error)
            self.assertEqual(goals.current().id, goal.id)

    def test_resume_legacy_goal_uses_acceptance_checks_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.joinpath("result.txt").write_text("ready", encoding="utf-8")
            provider = FakeProvider([{"role": "assistant", "content": "Done."}])
            goals = GoalRunner(self.make_agent(root, provider), 2)
            store = goals.store()
            store.start("Finish the project change", 2, provider.criteria)
            legacy = json.loads(store.path.read_text())
            legacy.update(status="paused", model_verification={"status": "error", "feedback": "judge unavailable"})
            store.path.write_text(json.dumps(legacy), encoding="utf-8")

            goal = goals.resume()

            self.assertEqual(goal.status, "complete")
            self.assertEqual(goal.cycle, 1)
            self.assertEqual(goal.verification[0]["status"], "passed")
            self.assertEqual(len(provider.contexts), 1)
            self.assertNotIn("model_verification", json.loads(store.path.read_text()))
            self.assertNotIn("semantic verification", provider.contexts[0][-1]["content"])

    def test_manual_acceptance_requires_user_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider(
                [{"role": "assistant", "content": "Ready for visual review."}],
                [
                    {
                        "id": "AC1",
                        "description": "User accepts the visual result",
                        "check": {"type": "manual", "instructions": "Inspect the rendered result"},
                    }
                ],
            )
            agent = self.make_agent(root, provider)
            goals = GoalRunner(agent, 2)
            goal = goals.start("Create a visual result")
            self.assertEqual(goal.status, "blocked")
            self.assertEqual(goal.verification[0]["status"], "pending")
            approved = goals.approve("AC1")
            self.assertIn("AC1", approved.approvals)

    def test_machine_acceptance_checks_real_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "artifact.txt").write_text("ready\n")
            verifier = AcceptanceVerifier(
                Tools(root, True, 2, PermissionManager(lambda request: "session"))
            )
            results = verifier.verify(
                [
                    {
                        "id": "AC1",
                        "description": "artifact exists",
                        "check": {"type": "file_exists", "path": "artifact.txt"},
                    },
                    {
                        "id": "AC2",
                        "description": "artifact is ready",
                        "check": {"type": "file_contains", "path": "artifact.txt", "text": "ready"},
                    },
                    {
                        "id": "AC3",
                        "description": "artifact has no failure marker",
                        "check": {
                            "type": "file_not_contains",
                            "path": "artifact.txt",
                            "text": "FAILED",
                        },
                    },
                    {
                        "id": "AC4",
                        "description": "artifact exactly matches",
                        "check": {"type": "file_equals", "path": "artifact.txt", "text": "ready\n"},
                    },
                    {
                        "id": "AC5",
                        "description": "shell check succeeds",
                        "check": {"type": "command", "command": "test -f artifact.txt"},
                    },
                ],
                [],
            )
            self.assertEqual([result.status for result in results], ["passed"] * 5)

    def test_file_equals_requires_exact_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.joinpath("artifact.txt").write_text("ready")
            verifier = AcceptanceVerifier(Tools(root, False, 2))
            result = verifier.verify(
                [
                    {
                        "id": "AC1",
                        "description": "artifact includes a final newline",
                        "check": {"type": "file_equals", "path": "artifact.txt", "text": "ready\n"},
                    }
                ],
                [],
            )[0]
            self.assertEqual(result.status, "failed")
            self.assertIn("exact content differs", result.evidence)

    def test_command_acceptance_is_rejected_when_bash_is_disabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "require Bash"):
            validate_criteria(
                [
                    {
                        "id": "AC1",
                        "description": "run a shell check",
                        "check": {"type": "command", "command": "test -f artifact.txt"},
                    }
                ],
                allow_bash=False,
            )

    def test_manual_acceptance_is_rejected_in_unattended_runs(self) -> None:
        with self.assertRaisesRegex(ValueError, "unattended"):
            validate_criteria(
                [
                    {
                        "id": "AC1",
                        "description": "inspect the result",
                        "check": {"type": "manual", "instructions": "Inspect it"},
                    }
                ],
                allow_manual=False,
            )

    def test_global_and_session_memory_are_loaded_every_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.joinpath("MEMORY.md").write_text("Global project preference")
            provider = FakeProvider([{"role": "assistant", "content": "ready"}])
            agent = self.make_agent(root, provider)
            agent.store.session_memory_path.parent.mkdir(parents=True)
            agent.store.session_memory_path.write_text("Session-specific decision")
            agent.run("continue")
            system = provider.contexts[0][0]["content"]
            self.assertIn("### Global Workspace Memory\nGlobal project preference", system)
            self.assertIn("### Session-Specific Memory\nSession-specific decision", system)

    def test_switching_session_switches_session_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            provider = FakeProvider([{"role": "assistant", "content": "ready"}])
            agent = self.make_agent(root, provider)
            old_path = agent.store.session_memory_path
            agent.select("other")
            agent.store.session_memory_path.parent.mkdir(parents=True)
            agent.store.session_memory_path.write_text("Other session memory")
            agent.run("continue")
            system = provider.contexts[0][0]["content"]
            self.assertNotEqual(old_path, agent.store.session_memory_path)
            self.assertIn("Other session memory", system)

    def test_skill_catalog_uses_progressive_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            skill_directory = root / ".pi" / "skills" / "release-check"
            skill_directory.mkdir(parents=True)
            skill_directory.joinpath("SKILL.md").write_text(
                "---\nname: release-check\ndescription: Validate a release candidate.\n---\n\n"
                "SECRET FULL PROCEDURE\n"
            )
            provider = FakeProvider([{"role": "assistant", "content": "ready"}])
            agent = self.make_agent(root, provider)
            agent.run("prepare a release")
            system = provider.contexts[0][0]["content"]
            self.assertIn("release-check: Validate a release candidate.", system)
            self.assertNotIn("SECRET FULL PROCEDURE", system)
            self.assertIn(
                "SECRET FULL PROCEDURE",
                agent.tools.execute("read_skill", {"name": "release-check"}),
            )

    def test_skill_command_loads_full_instructions_for_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            skill_directory = root / "skills" / "inspect-code"
            skill_directory.mkdir(parents=True)
            skill_directory.joinpath("SKILL.md").write_text(
                "---\nname: inspect-code\ndescription: Inspect code safely.\n---\n\nRead files before editing.\n"
            )
            provider = FakeProvider([{"role": "assistant", "content": "done"}])
            agent = self.make_agent(root, provider)
            agent.run_skill("inspect-code", "inspect app.py")
            prompt = provider.contexts[0][-1]["content"]
            self.assertIn("Read files before editing.", prompt)
            self.assertIn("inspect app.py", prompt)

    def test_invalid_skill_is_reported_and_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            skill_directory = root / ".agents" / "skills" / "broken"
            skill_directory.mkdir(parents=True)
            skill_directory.joinpath("SKILL.md").write_text("# Missing frontmatter\n")
            registry = SkillRegistry(root)
            self.assertEqual(registry.list(), [])
            self.assertTrue(registry.diagnostics)

    def test_workspace_file_operations_do_not_request_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            manager = PermissionManager(lambda request: requests.append(request) or "deny")
            tools = Tools(Path(directory).resolve(), False, 1, manager)
            self.assertIn("succeeded", tools.execute("write", {"path": "inside.txt", "content": "ok"}))
            self.assertEqual(tools.execute("read", {"path": "inside.txt"}), "ok")
            self.assertEqual(requests, [])

    def test_read_only_mode_blocks_workspace_mutation_and_bash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            root = Path(directory).resolve()
            tools = Tools(
                root,
                True,
                1,
                PermissionManager(lambda request: requests.append(request) or "once"),
                permission_mode="read-only",
            )
            write_result = tools.execute("write", {"path": "blocked.txt", "content": "no"})
            shell_result = tools.run_shell("pwd")
            self.assertIn("read-only", write_result)
            self.assertIn("read-only", shell_result.error or "")
            self.assertFalse(root.joinpath("blocked.txt").exists())
            self.assertEqual(requests, [])

    def test_danger_full_access_skips_ask_but_keeps_hard_denies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            outside = base / "outside.txt"
            requests = []
            tools = Tools(
                workspace,
                True,
                1,
                PermissionManager(lambda request: requests.append(request) or "deny"),
                permission_mode="danger-full-access",
            )
            self.assertIn(
                "succeeded",
                tools.execute("write", {"path": str(outside), "content": "allowed"}),
            )
            denied = tools.run_shell("rm -rf /")
            self.assertEqual(outside.read_text(), "allowed")
            self.assertIn("blocked by safety policy", denied.error or "")
            self.assertEqual(requests, [])

    def test_permission_mode_can_change_during_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = Tools(Path(directory).resolve(), True, 1)
            self.assertEqual(tools.set_permission_mode("read-only"), PermissionMode.READ_ONLY)
            self.assertFalse(tools.allow_bash)
            self.assertEqual(
                tools.set_permission_mode("danger-full-access"),
                PermissionMode.DANGER_FULL_ACCESS,
            )
            self.assertTrue(tools.allow_bash)

    def test_outside_workspace_write_requires_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            outside = base / "outside.txt"
            denied = Tools(workspace, False, 1, PermissionManager(lambda request: "deny"))
            result = denied.execute("write", {"path": str(outside), "content": "blocked"})
            self.assertIn("Permission denied", result)
            self.assertFalse(outside.exists())
            allowed = Tools(workspace, False, 1, PermissionManager(lambda request: "once"))
            self.assertIn("succeeded", allowed.execute("write", {"path": str(outside), "content": "allowed"}))
            self.assertEqual(outside.read_text(), "allowed")

    def test_shell_requires_permission_and_caches_exact_session_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            manager = PermissionManager(lambda request: requests.append(request) or "session")
            tools = Tools(Path(directory).resolve(), True, 2, manager)
            command = "echo hello"
            first = tools.run_shell(command)
            second = tools.run_shell(command)
            self.assertEqual(first.exit_code, 0)
            self.assertEqual(second.exit_code, 0)
            self.assertEqual(len(requests), 1)

    def test_shell_permission_denial_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "marker.txt"
            tools = Tools(
                Path(directory).resolve(),
                True,
                2,
                PermissionManager(lambda request: "deny"),
            )
            result = tools.run_shell(f"touch {marker}")
            self.assertIsNone(result.exit_code)
            self.assertIn("permission denied", result.error or "")
            self.assertFalse(marker.exists())

    def test_safe_read_only_shell_command_is_automatically_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            manager = PermissionManager(lambda request: requests.append(request) or "deny")
            tools = Tools(Path(directory).resolve(), True, 2, manager)
            result = tools.run_shell("pwd")
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(requests, [])

    def test_catastrophic_shell_command_is_denied_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            manager = PermissionManager(lambda request: requests.append(request) or "session")
            tools = Tools(Path(directory).resolve(), True, 2, manager)
            result = tools.run_shell("rm -rf /")
            self.assertIsNone(result.exit_code)
            self.assertIn("blocked by safety policy", result.error or "")
            self.assertEqual(requests, [])
            disk = tools.run_shell("diskutil eraseDisk APFS empty disk2")
            self.assertIn("blocked by safety policy", disk.error or "")
            self.assertEqual(requests, [])

    def test_sensitive_workspace_file_requires_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.joinpath(".env").write_text("API_KEY=secret")
            requests = []
            tools = Tools(root, False, 1, PermissionManager(lambda request: requests.append(request) or "deny"))
            result = tools.execute("read", {"path": ".env"})
            self.assertIn("Permission denied", result)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].risk, "sensitive-data")

    def test_private_key_and_system_write_are_hard_denied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            tools = Tools(
                Path(directory).resolve(),
                False,
                1,
                PermissionManager(lambda request: requests.append(request) or "session"),
            )
            key_result = tools.execute("read", {"path": str(Path.home() / ".ssh" / "id_ed25519")})
            system_result = tools.execute("write", {"path": "/System/picli-policy-test", "content": "x"})
            self.assertIn("Blocked by safety policy", key_result)
            self.assertIn("Blocked by safety policy", system_result)
            self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
