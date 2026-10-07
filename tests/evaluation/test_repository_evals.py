import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.compare import render_comparison
from evals.execution import docker_executable, run_python_test
from evals.graders import GradingView, grade_all
from evals.preflight import _apply_reference
from evals.schema import load_cases


ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"


class RepositoryEvalTests(unittest.TestCase):
    def test_missing_docker_returns_clear_grader_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('evals.execution.shutil.which', return_value=None), \
                patch('evals.execution.DOCKER_DESKTOP_CANDIDATES', ()):
            result = run_python_test('pass', Path(directory), 'python:3.11-slim')
        self.assertEqual(result.returncode, 127)
        self.assertIn('Docker CLI not found', result.stderr)

    def test_repository_cases_start_broken_and_references_pass(self):
        cases = [case for case in load_cases(EVALS / "cases") if case.suite == "repository"]
        self.assertEqual(len(cases), 10)
        for case in cases:
            with self.subTest(case=case.id), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / "workspace"
                shutil.copytree(EVALS / case.fixture, workspace)
                checks = [check for check in case.checks if check["type"] == "python_test"]
                initial = grade_all(checks, GradingView(workspace, "", [], 0, None, suite_root=EVALS))
                self.assertTrue(any(not grade.passed for grade in initial), "initial fixture unexpectedly passes")
                _apply_reference(EVALS, case, workspace)
                reference = grade_all(checks, GradingView(workspace, "", [], 0, None, suite_root=EVALS))
                self.assertTrue(all(grade.passed for grade in reference), reference)

    def test_comparison_reports_aggregate_and_per_case_deltas(self):
        baseline = [
            {"case_id": "a", "passed": False, "goal_status": None, "model_calls": 2,
             "tool_calls": 3, "input_tokens_estimated": 10, "output_tokens_estimated": 4, "duration_ms": 100},
            {"case_id": "b", "passed": True, "goal_status": "complete", "model_calls": 4,
             "tool_calls": 5, "input_tokens_estimated": 20, "output_tokens_estimated": 8, "duration_ms": 200},
        ]
        candidate = [{**row, "passed": True} for row in baseline]
        report = render_comparison(baseline, candidate, "old.jsonl", "new.jsonl")
        self.assertIn("Success rate | 50.0% | 100.0% | +50.0%", report)
        self.assertIn("| a | 0/1 (0.0%) | 1/1 (100.0%) | +100.0% |", report)


if __name__ == "__main__":
    unittest.main()
