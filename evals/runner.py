"""Isolated, repeatable end-to-end runner for real-model agent evaluations."""

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import statistics
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from picli.agent.core import Agent
from picli.memory.compaction import Compactor, estimate_tokens
from picli.agent.goal import GoalRunner
from picli.tools.permissions import PermissionManager
from picli.tools.policy import Decision
from picli.providers.client import BaseProvider, create_provider
from picli.tools.builtin import Tools

from .graders import GradingView, grade_all
from .schema import EvalCase


class ObservedProvider:
    """Provider adapter that records model-level cost proxies without changing the agent."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.complete_calls = 0
        self.summary_calls = 0
        self.criteria_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.duration_ms = 0

    @property
    def model(self) -> str:
        return self.provider.model

    @model.setter
    def model(self, value: str) -> None:
        self.provider.model = value

    @property
    def demo(self) -> bool:
        return self.provider.demo

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    def set_tool_schemas(self, schemas: list[dict]) -> None:
        self.provider.set_tool_schemas(schemas)

    @property
    def model_calls(self) -> int:
        return self.complete_calls + self.summary_calls + self.criteria_calls

    def complete(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
    ) -> dict:
        self.complete_calls += 1
        return self._observe(messages, lambda: self.provider.complete(messages, on_text=on_text))

    def summarize(self, messages: list[dict]) -> str:
        self.summary_calls += 1
        return self._observe(messages, lambda: self.provider.summarize(messages))

    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        self.criteria_calls += 1
        started = time.perf_counter()
        try:
            result = self.provider.create_acceptance_criteria(objective, allow_bash, allow_manual)
            self.input_tokens += estimate_tokens({"objective": objective, "allow_bash": allow_bash})
            self.output_tokens += estimate_tokens(result)
            return result
        finally:
            self.duration_ms += round((time.perf_counter() - started) * 1000)

    def _observe(self, messages: list[dict], operation):
        started = time.perf_counter()
        try:
            result = operation()
            self.input_tokens += estimate_tokens(messages)
            self.output_tokens += estimate_tokens(result)
            return result
        finally:
            self.duration_ms += round((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class EvalRunResult:
    case_id: str
    category: str
    repetition: int
    passed: bool
    error: str | None
    final_output: str
    goal_status: str | None
    model_calls: int
    complete_calls: int
    summary_calls: int
    criteria_calls: int
    tool_calls: int
    tool_outcomes: dict[str, int]
    input_tokens_estimated: int
    output_tokens_estimated: int
    duration_ms: int
    safety_violations: int
    grades: list[dict]
    tool_events: list[dict]
    transcript: str
    schema_version: int = 3
    suite: str = "regression"
    metadata: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class EvalRunner:
    def __init__(
        self,
        suite_root: Path,
        base_url: str,
        api_key: str,
        model: str,
        allow_bash: bool,
        provider_name: str = "auto",
        auth_token: str = "",
    ):
        self.suite_root = suite_root.resolve()
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.allow_bash = allow_bash
        self.provider_name = provider_name
        self.auth_token = auth_token

    def run_case(self, case: EvalCase, repetition: int) -> EvalRunResult:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=f"picli-eval-{case.id}-") as directory:
            workspace = Path(directory).resolve() / "workspace"
            fixture = (self.suite_root / case.fixture).resolve()
            if not fixture.is_relative_to(self.suite_root) or not fixture.is_dir():
                raise ValueError(f"Invalid fixture for {case.id}: {case.fixture}")
            shutil.copytree(fixture, workspace)
            config = case.config
            allow_bash = self.allow_bash or bool(config.get("allow_bash", False))
            permission_choice = "session" if config.get("permission_mode", "deny") == "allow" else "deny"
            permissions = PermissionManager(lambda request: permission_choice)
            tools = Tools(
                workspace,
                allow_bash,
                int(config.get("tool_timeout", 30)),
                permissions,
            )
            provider = ObservedProvider(
                create_provider(
                    self.provider_name,
                    self.base_url,
                    self.api_key,
                    self.model,
                    False,
                    self.auth_token,
                )
            )
            agent = Agent(
                workspace,
                provider,
                tools,
                f"eval-{repetition}",
                int(config.get("max_turns", 20)),
                Compactor(
                    int(config.get("context_window", 32000)),
                    float(config.get("compact_at", 0.75)),
                    int(config.get("keep_turns", 4)),
                ),
            )
            audit_messages = []
            def observe_tools(calls, results):
                audit_messages.extend(copy.deepcopy([{"role": "assistant", "tool_calls": calls}, *results]))
            agent.tool_observer = observe_tools
            error = None
            goal_status = None
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    if case.mode == "goal":
                        goal = GoalRunner(
                            agent,
                            int(config.get("goal_cycles", 6)),
                            allow_manual=False,
                        ).start(case.turns[0])
                        goal_status = goal.status
                    else:
                        for index, turn in enumerate(case.turns):
                            if index in config.get("new_session_before", []):
                                agent.select(f"eval-{repetition}-turn-{index}")
                            agent.run(turn)
            except Exception as exception:
                error = f"{type(exception).__name__}: {exception}"
            events = collect_tool_events(audit_messages, tools)
            final_output = next(
                (
                    str(message.get("content") or "")
                    for message in reversed(agent.messages)
                    if message.get("role") == "assistant" and message.get("content")
                ),
                "",
            )
            grades = grade_all(
                case.checks,
                GradingView(workspace, final_output, events, provider.model_calls, goal_status,
                            provider.summary_calls, self.suite_root, config.get("test_image")),
            )
            outcomes = Counter(event["outcome"] for event in events)
            safety_violations = outcomes["unsafe_executed"]
            duration_ms = round((time.perf_counter() - started) * 1000)
            return EvalRunResult(
                case.id,
                case.category,
                repetition,
                error is None and all(grade.passed for grade in grades),
                error,
                final_output[-4000:],
                goal_status,
                provider.model_calls,
                provider.complete_calls,
                provider.summary_calls,
                provider.criteria_calls,
                len(events),
                dict(sorted(outcomes.items())),
                provider.input_tokens,
                provider.output_tokens,
                duration_ms,
                safety_violations,
                [grade.as_dict() for grade in grades],
                events,
                output.getvalue()[-12000:],
                suite=case.suite,
                metadata={
                    "provider": provider.provider_name, "model": provider.model,
                    "config": {**config, "allow_bash": allow_bash},
                    "case_sha256": hashlib.sha256(case.source.read_bytes()).hexdigest(),
                    "source_sha256": source_digest(Path(__file__).resolve().parents[1]),
                    "provenance": case.provenance,
                    "fixture_sha256": tree_digest(fixture),
                    "checks_sha256": {c["path"]: hashlib.sha256((self.suite_root / c["path"]).read_bytes()).hexdigest()
                                      for c in case.checks if c["type"] == "python_test"},
                    "reference_sha256": reference_digest(self.suite_root, case.reference),
                },
            )


def collect_tool_events(messages: list[dict], tools: Tools) -> list[dict]:
    results = {
        message.get("tool_call_id"): str(message.get("content") or "")
        for message in messages
        if message.get("role") == "tool"
    }
    events = []
    for message in messages:
        for call in message.get("tool_calls", []):
            name = call.get("function", {}).get("name", "")
            try:
                arguments = json.loads(call["function"]["arguments"])
                arguments_valid = isinstance(arguments, dict)
                if not arguments_valid:
                    arguments = {}
            except (KeyError, TypeError, json.JSONDecodeError):
                arguments = {}
                arguments_valid = False
            if name == "bash":
                policy = tools.policy.shell(str(arguments.get("command", ""))).decision
            elif name in {"read", "write", "edit"}:
                path = tools.path(str(arguments.get("path", "")))
                policy = tools.policy.file(name, path).decision
            else:
                policy = Decision.ALLOW
            result = results.get(call.get("id"), "")
            outcome = classify_tool_outcome(policy, result, arguments_valid)
            events.append(
                {
                    "id": call.get("id"),
                    "name": name,
                    "arguments": _safe_arguments(arguments),
                    "policy": policy.value,
                    "outcome": outcome,
                    "blocked": outcome in {"policy_blocked", "user_denied", "capability_unavailable"},
                    "result": result[-2000:],
                }
            )
    return events


def classify_tool_outcome(policy: Decision, result: str, arguments_valid: bool = True) -> str:
    """Classify why a tool did or did not run; ordering reflects the execution gate order."""
    lowered = result.casefold()
    if "bash disabled" in lowered:
        return "capability_unavailable"
    if "blocked by safety policy" in lowered:
        return "policy_blocked"
    if not result:
        return "operation_error"
    if policy is Decision.DENY:
        return "unsafe_executed"
    if "permission denied" in lowered:
        return "user_denied"
    if not arguments_valid or any(
        marker in lowered
        for marker in ("unknown tool:", "expected arguments:", "tool arguments must be strings")
    ):
        return "invalid_arguments"
    if "timed out" in lowered:
        return "timeout"
    if "tool interrupted" in lowered or "tool cancelled" in lowered:
        return "interrupted"
    if "tool error:" in lowered or not result:
        return "operation_error"
    exit_match = re.match(r"Exit\s+(-?\d+)", result)
    if exit_match and int(exit_match.group(1)) != 0:
        return "operation_error"
    return "success"


def _safe_arguments(arguments: object) -> object:
    if isinstance(arguments, dict):
        return {
            str(key): "[REDACTED]"
            if any(marker in str(key).casefold() for marker in ("key", "token", "password", "secret"))
            else _safe_arguments(value)
            for key, value in arguments.items()
        }
    if isinstance(arguments, list):
        return [_safe_arguments(value) for value in arguments[:100]]
    if isinstance(arguments, str):
        return arguments[:4000] + ("[truncated]" if len(arguments) > 4000 else "")
    return arguments


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for folder in ("picli", "evals"):
        for path in sorted((root / folder).rglob("*.py")):
            if "results" not in path.parts:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def reference_digest(root: Path, value: str | None) -> str | None:
    if value is None:
        return None
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Reference path escapes the evaluation suite")
    if path.is_dir():
        return tree_digest(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    raise ValueError(f"Missing reference: {value}")


def summarize(results: list[EvalRunResult]) -> dict:
    total = len(results)
    passed = sum(result.passed for result in results)
    completed_goals = sum(result.goal_status == "complete" for result in results)
    false_completions = sum(
        result.goal_status == "complete" and not result.passed for result in results
    )
    outcome_counts = Counter(
        event["outcome"]
        for result in results
        for event in result.tool_events
    )
    execution_errors = outcome_counts["operation_error"] + outcome_counts["timeout"]
    execution_attempts = outcome_counts["success"] + execution_errors
    tool_misuses = outcome_counts["invalid_arguments"] + outcome_counts["capability_unavailable"]
    tool_calls = sum(outcome_counts.values())
    deny_attempts = outcome_counts["policy_blocked"] + outcome_counts["unsafe_executed"]
    recoverable = {"invalid_arguments", "capability_unavailable", "operation_error", "timeout"}
    runs_with_recoverable_errors = [
        result for result in results
        if any(event["outcome"] in recoverable for event in result.tool_events)
    ]
    return {
        "runs": total,
        "passed": passed,
        "success_rate": passed / total if total else 0.0,
        "false_completion_rate": false_completions / completed_goals if completed_goals else None,
        "completed_goals": completed_goals,
        "false_completions": false_completions,
        "safety_violations": sum(result.safety_violations for result in results),
        "tool_outcomes": dict(sorted(outcome_counts.items())),
        "execution_error_rate": execution_errors / execution_attempts if execution_attempts else None,
        "tool_misuse_rate": tool_misuses / tool_calls if tool_calls else None,
        "policy_enforcement_rate": outcome_counts["policy_blocked"] / deny_attempts if deny_attempts else None,
        "unsafe_execution_rate": outcome_counts["unsafe_executed"] / deny_attempts if deny_attempts else None,
        "recovery_rate": (
            sum(result.passed for result in runs_with_recoverable_errors) / len(runs_with_recoverable_errors)
            if runs_with_recoverable_errors
            else None
        ),
        "policy_blocks": outcome_counts["policy_blocked"],
        "user_denials": outcome_counts["user_denied"],
        "average_model_calls": statistics.fmean(result.model_calls for result in results) if results else 0.0,
        "average_tool_calls": statistics.fmean(result.tool_calls for result in results) if results else 0.0,
        "average_input_tokens_estimated": (
            statistics.fmean(result.input_tokens_estimated for result in results) if results else 0.0
        ),
        "average_output_tokens_estimated": (
            statistics.fmean(result.output_tokens_estimated for result in results) if results else 0.0
        ),
        "average_duration_ms": statistics.fmean(result.duration_ms for result in results) if results else 0.0,
    }


def write_report(output_dir: Path, results: list[EvalRunResult], model: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    jsonl_path = output_dir / f"{timestamp}.jsonl"
    summary_path = output_dir / f"{timestamp}.md"
    with jsonl_path.open("w", encoding="utf-8") as output:
        for result in results:
            output.write(json.dumps(result.as_dict(), ensure_ascii=False) + "\n")
    metrics = summarize(results)
    lines = [
        f"# yu Eval Report — {timestamp}",
        "",
        f"Model: `{model}`",
        "",
        f"- Runs: {metrics['runs']}",
        f"- Passed: {metrics['passed']}",
        f"- Success rate: {metrics['success_rate']:.1%}",
        f"- Goal false completion rate: {_format_rate(metrics['false_completion_rate'])} "
        f"({metrics['false_completions']}/{metrics['completed_goals']} completed goals)",
        f"- Safety violations: {metrics['safety_violations']}",
        f"- Tool execution error rate: {_format_rate(metrics['execution_error_rate'])}",
        f"- Tool misuse rate: {_format_rate(metrics['tool_misuse_rate'])}",
        f"- Policy enforcement rate: {_format_rate(metrics['policy_enforcement_rate'])}",
        f"- Unsafe execution rate: {_format_rate(metrics['unsafe_execution_rate'])}",
        f"- Recovery rate: {_format_rate(metrics['recovery_rate'])}",
        f"- Expected policy blocks: {metrics['policy_blocks']}",
        f"- User-denied ASK operations: {metrics['user_denials']}",
        f"- Average model calls: {metrics['average_model_calls']:.2f}",
        f"- Average tool calls: {metrics['average_tool_calls']:.2f}",
        f"- Average estimated input tokens: {metrics['average_input_tokens_estimated']:.0f}",
        f"- Average estimated output tokens: {metrics['average_output_tokens_estimated']:.0f}",
        f"- Average duration: {metrics['average_duration_ms']:.0f} ms",
        "",
        "Tool outcomes: `" + json.dumps(metrics["tool_outcomes"], ensure_ascii=False, sort_keys=True) + "`",
        "",
        "| Case | Run | Result | Model calls | Tool calls | Outcomes | Duration |",
        "|---|---:|---|---:|---:|---|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result.case_id} | {result.repetition} | {'PASS' if result.passed else 'FAIL'} | "
            f"{result.model_calls} | {result.tool_calls} | "
            f"`{json.dumps(result.tool_outcomes, sort_keys=True)}` | {result.duration_ms} ms |"
        )
    lines += ["", "## Results by suite and category", "", "| Suite / category | Passed / runs | Rate |", "|---|---:|---:|"]
    groups = sorted({(r.suite, r.category) for r in results})
    for suite, category in groups:
        group = [r for r in results if (r.suite, r.category) == (suite, category)]
        count = sum(r.passed for r in group)
        lines.append(f"| {suite} / {category} | {count}/{len(group)} | {count / len(group):.1%} |")
    lines += ["", "## Failed checks", ""]
    for result in results:
        if not result.passed:
            lines.append(f"- {result.case_id} run {result.repetition}: {result.error or 'grader failure'}")
            for grade in result.grades:
                if not grade["passed"]:
                    lines.append(f"  - {grade['check']['type']}: {grade['evidence']}")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl_path, summary_path


def _format_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"
