import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evals.graders import GradingView, grade_all
from evals.runner import ObservedProvider, classify_tool_outcome, collect_tool_events, summarize
from evals.schema import EvalCase, load_cases
from picli.tools.policy import Decision
from picli.tools.builtin import Tools


class EvalHarnessTests(unittest.TestCase):
    def test_observed_provider_forwards_streamed_text(self) -> None:
        class StreamingStub:
            model = "fake"
            demo = False

            def complete(self, messages, on_text=None):
                if on_text:
                    on_text("streamed")
                return {"role": "assistant", "content": "streamed"}

        fragments = []
        provider = ObservedProvider(StreamingStub())
        result = provider.complete([{"role": "user", "content": "hello"}], fragments.append)
        self.assertEqual(fragments, ["streamed"])
        self.assertEqual(result["content"], "streamed")
        self.assertEqual(provider.complete_calls, 1)

    def test_bundled_cases_and_fixtures_are_valid(self) -> None:
        suite = Path(__file__).resolve().parents[2] / "evals"
        cases = load_cases(suite / "cases")
        self.assertGreaterEqual(len(cases), 7)
        for case in cases:
            fixture = (suite / case.fixture).resolve()
            self.assertTrue(fixture.is_relative_to(suite))
            self.assertTrue(fixture.is_dir())

    def test_external_graders_check_state_output_trace_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            workspace.joinpath("result.txt").write_text("done\n", encoding="utf-8")
            view = GradingView(
                workspace,
                "finished",
                [{"name": "write", "policy": "allow", "outcome": "success", "blocked": False}],
                2,
                "complete",
            )
            grades = grade_all(
                [
                    {"type": "file_equals", "path": "result.txt", "text": "done\n"},
                    {"type": "output_contains", "text": "finish"},
                    {"type": "tool_called", "name": "write"},
                    {"type": "max_model_calls", "value": 3},
                    {"type": "goal_status", "value": "complete"},
                    {"type": "policy_no_violation"},
                ],
                view,
            )
            self.assertTrue(all(grade.passed for grade in grades))

    def test_case_schema_rejects_unknown_grader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                '{"id":"bad","description":"bad","category":"test","mode":"agent",'
                '"fixture":"fixtures/empty","turns":["run"],"checks":[{"type":"guess"}]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                EvalCase.load(path)

    def test_disabled_bash_is_capability_unavailable_not_policy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = Tools(Path(directory).resolve(), False, 1)
            messages = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "danger",
                            "function": {"name": "bash", "arguments": '{"command":"rm -rf /"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "danger",
                    "content": "Tool error: bash disabled; restart with --allow-bash",
                },
            ]
            events = collect_tool_events(messages, tools)
            self.assertEqual(events[0]["policy"], "deny")
            self.assertTrue(events[0]["blocked"])
            self.assertEqual(events[0]["outcome"], "capability_unavailable")

    def test_tool_outcomes_distinguish_security_and_execution_failures(self) -> None:
        cases = [
            (Decision.DENY, "Tool error: blocked by safety policy: prohibited", True, "policy_blocked"),
            (Decision.ASK, "Tool error: permission denied for shell command", True, "user_denied"),
            (Decision.ALLOW, "Tool error: Expected arguments: path", True, "invalid_arguments"),
            (Decision.ALLOW, "Tool error: file does not exist", True, "operation_error"),
            (Decision.ASK, "Tool error: command timed out after 10s", True, "timeout"),
            (Decision.ALLOW, "Tool interrupted; side effects may already have occurred", True, "interrupted"),
            (Decision.DENY, "Exit 0\n", True, "unsafe_executed"),
            (Decision.ALLOW, "write: result.txt succeeded", True, "success"),
            (Decision.ALLOW, "Tool error: malformed JSON", False, "invalid_arguments"),
        ]
        for policy, result, arguments_valid, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_tool_outcome(policy, result, arguments_valid), expected)

    def test_summary_keeps_policy_blocks_out_of_execution_errors(self) -> None:
        common = {
            "goal_status": None,
            "model_calls": 1,
            "tool_calls": 0,
            "input_tokens_estimated": 10,
            "output_tokens_estimated": 2,
            "duration_ms": 5,
        }
        results = [
            SimpleNamespace(
                **common,
                passed=True,
                safety_violations=0,
                tool_events=[
                    {"outcome": "success"},
                    {"outcome": "operation_error"},
                    {"outcome": "policy_blocked"},
                ],
            ),
            SimpleNamespace(
                **common,
                passed=False,
                safety_violations=1,
                tool_events=[
                    {"outcome": "capability_unavailable"},
                    {"outcome": "unsafe_executed"},
                ],
            ),
        ]
        metrics = summarize(results)
        self.assertEqual(metrics["execution_error_rate"], 0.5)
        self.assertEqual(metrics["tool_misuse_rate"], 0.2)
        self.assertEqual(metrics["policy_enforcement_rate"], 0.5)
        self.assertEqual(metrics["unsafe_execution_rate"], 0.5)
        self.assertEqual(metrics["recovery_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
