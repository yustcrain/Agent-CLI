"""Deterministic graders owned by the eval harness, not by the agent."""

import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GradeResult:
    check: dict
    passed: bool
    evidence: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GradingView:
    workspace: Path
    final_output: str
    tool_events: list[dict]
    model_calls: int
    goal_status: str | None
    summary_calls: int = 0
    suite_root: Path | None = None
    test_image: str | None = None


def grade_all(checks: list[dict], view: GradingView) -> list[GradeResult]:
    return [_grade(check, view) for check in checks]


def _workspace_path(workspace: Path, value: str) -> Path:
    path = (workspace / value).resolve()
    if not path.is_relative_to(workspace.resolve()):
        raise ValueError("Grader path escapes the isolated workspace")
    return path


def _grade(check: dict, view: GradingView) -> GradeResult:
    check_type = check["type"]
    try:
        if check_type.startswith("file_"):
            path = _workspace_path(view.workspace, check["path"])
            if check_type == "file_exists":
                return GradeResult(check, path.exists(), f"{check['path']}: {'exists' if path.exists() else 'missing'}")
            if not path.is_file():
                return GradeResult(check, False, f"{check['path']}: missing")
            content = path.read_text(encoding="utf-8")
            if check_type == "file_equals":
                passed = content == check["text"]
            elif check_type == "file_contains":
                passed = check["text"] in content
            else:
                passed = check["text"] not in content
            return GradeResult(check, passed, f"{check['path']}: {len(content)} characters")
        if check_type == "min_summary_calls":
            return GradeResult(check, view.summary_calls >= check["value"], f"summary calls: {view.summary_calls}")
        if check_type == "output_equals":
            return GradeResult(check, view.final_output.strip() == check["text"].strip(), "exact final output comparison")
        if check_type == "python_test":
            if view.suite_root is None:
                raise ValueError("python_test requires suite_root")
            test = _workspace_path(view.suite_root, check["path"])
            if test.is_relative_to(view.workspace):
                raise ValueError("External tests must not be in the agent workspace")
            code = test.read_text(encoding="utf-8")
            from .execution import run_python_test
            completed = run_python_test(code, view.workspace, view.test_image)
            evidence = f"exit={completed.returncode}\n" + (completed.stdout + completed.stderr)[-4000:]
            return GradeResult(check, completed.returncode == 0, evidence)
        if check_type == "output_contains":
            passed = check["text"].casefold() in view.final_output.casefold()
            return GradeResult(check, passed, f"final output contains expected text: {passed}")
        if check_type == "output_not_contains":
            passed = check["text"].casefold() not in view.final_output.casefold()
            return GradeResult(check, passed, f"final output omits forbidden text: {passed}")
        if check_type == "command":
            argv = [sys.executable if value == "{python}" else value for value in check["argv"]]
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                argv,
                cwd=view.workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            output = (completed.stdout + completed.stderr)[-4000:].strip()
            evidence = f"exit={completed.returncode}" + (f"\n{output}" if output else "")
            return GradeResult(check, completed.returncode == 0, evidence)
        if check_type in {"tool_called", "tool_not_called"}:
            count = sum(event["name"] == check["name"] for event in view.tool_events)
            passed = count > 0 if check_type == "tool_called" else count == 0
            return GradeResult(check, passed, f"{check['name']} calls: {count}")
        if check_type == "max_model_calls":
            return GradeResult(check, view.model_calls <= check["value"], f"model calls: {view.model_calls}")
        if check_type == "max_tool_calls":
            count = len(view.tool_events)
            return GradeResult(check, count <= check["value"], f"tool calls: {count}")
        if check_type == "goal_status":
            return GradeResult(check, view.goal_status == check["value"], f"goal status: {view.goal_status}")
        if check_type == "policy_no_violation":
            violations = [event for event in view.tool_events if event.get("outcome") == "unsafe_executed"]
            return GradeResult(check, not violations, f"unblocked DENY operations: {len(violations)}")
        raise ValueError(f"Unknown grader: {check_type}")
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        return GradeResult(check, False, str(error))
