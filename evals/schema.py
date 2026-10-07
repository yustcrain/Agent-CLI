"""Validation for versioned evaluation cases."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
CHECK_FIELDS = {
    "file_exists": {"path"},
    "file_equals": {"path", "text"},
    "file_contains": {"path", "text"},
    "file_not_contains": {"path", "text"},
    "output_contains": {"text"},
    "output_equals": {"text"},
    "min_summary_calls": {"value"},
    "python_test": {"path"},
    "output_not_contains": {"text"},
    "command": {"argv"},
    "tool_called": {"name"},
    "tool_not_called": {"name"},
    "max_model_calls": {"value"},
    "max_tool_calls": {"value"},
    "goal_status": {"value"},
    "policy_no_violation": set(),
}
CONFIG_FIELDS = {
    "context_window",
    "compact_at",
    "keep_turns",
    "max_turns",
    "goal_cycles",
    "tool_timeout",
    "permission_mode",
    "allow_bash",
    "new_session_before",
    "test_image",
}
SUITES = {"regression", "capability", "repository", "bigcodebench"}


@dataclass(frozen=True)
class EvalCase:
    id: str
    description: str
    category: str
    mode: str
    fixture: str
    turns: list[str]
    checks: list[dict]
    config: dict
    source: Path
    suite: str = "regression"
    provenance: dict | None = None
    reference: str | None = None

    @classmethod
    def load(cls, path: Path) -> "EvalCase":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid eval case {path}: {error}") from error
        if not isinstance(data, dict):
            raise ValueError(f"Eval case {path} must be a JSON object")
        required = {"id", "description", "category", "mode", "fixture", "turns", "checks"}
        unknown = set(data) - required - {"config", "suite", "provenance", "reference"}
        missing = required - set(data)
        if missing or unknown:
            raise ValueError(f"Invalid fields in {path}: missing={sorted(missing)}, unknown={sorted(unknown)}")
        identifier = data["id"]
        if not isinstance(identifier, str) or not CASE_ID.fullmatch(identifier):
            raise ValueError(f"Invalid eval case ID in {path}")
        for field in ("description", "category", "fixture"):
            if not isinstance(data[field], str) or not data[field].strip():
                raise ValueError(f"{field} must be a non-empty string in {path}")
        if data["mode"] not in {"agent", "goal"}:
            raise ValueError(f"mode must be agent or goal in {path}")
        turns = data["turns"]
        if not isinstance(turns, list) or not turns or not all(isinstance(turn, str) and turn.strip() for turn in turns):
            raise ValueError(f"turns must contain non-empty strings in {path}")
        if data["mode"] == "goal" and len(turns) != 1:
            raise ValueError(f"goal cases require exactly one objective in {path}")
        checks = data["checks"]
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"checks must be a non-empty list in {path}")
        cls._validate_checks(checks, path)
        config = data.get("config", {})
        if not isinstance(config, dict) or set(config) - CONFIG_FIELDS:
            raise ValueError(f"Invalid config in {path}")
        if config.get("permission_mode", "deny") not in {"deny", "allow"}:
            raise ValueError(f"permission_mode must be deny or allow in {path}")
        if "allow_bash" in config and not isinstance(config["allow_bash"], bool):
            raise ValueError(f"allow_bash must be true or false in {path}")
        if data.get("suite", "regression") not in SUITES:
            raise ValueError(f"Invalid suite in {path}")
        if not isinstance(data.get("provenance", {}), dict):
            raise ValueError(f"Invalid provenance in {path}")
        if "test_image" in config and (not isinstance(config["test_image"], str) or not config["test_image"].strip()):
            raise ValueError(f"Invalid test_image in {path}")
        reference = data.get("reference")
        if reference is not None and (not isinstance(reference, str) or not reference.strip()):
            raise ValueError(f"Invalid reference in {path}")
        resets = config.get("new_session_before", [])
        if (not isinstance(resets, list) or any(type(i) is not int or i < 1 or i >= len(turns) for i in resets)
                or len(set(resets)) != len(resets) or (resets and data["mode"] != "agent")):
            raise ValueError(f"Invalid new_session_before in {path}")
        return cls(
            identifier,
            data["description"].strip(),
            data["category"].strip(),
            data["mode"],
            data["fixture"],
            turns,
            checks,
            config,
            path,
            data.get("suite", "regression"),
            data.get("provenance", {}),
            reference,
        )

    @staticmethod
    def _validate_checks(checks: list[object], path: Path) -> None:
        for check in checks:
            if not isinstance(check, dict) or check.get("type") not in CHECK_FIELDS:
                raise ValueError(f"Invalid grader in {path}")
            expected = {"type", *CHECK_FIELDS[check["type"]]}
            if set(check) != expected:
                raise ValueError(f"Invalid fields for {check['type']} in {path}")
            if check["type"] == "command":
                argv = check["argv"]
                if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
                    raise ValueError(f"command argv must be a non-empty string list in {path}")
            elif "value" in check:
                if check["type"] != "goal_status" and (type(check["value"]) is not int or check["value"] < 0):
                    raise ValueError(f"Numeric grader value required in {path}")
                if check["type"] == "goal_status" and check["value"] not in {
                    "complete", "paused", "blocked", "cancelled"
                }:
                    raise ValueError(f"Invalid goal status in {path}")
            else:
                for field in expected - {"type"}:
                    if not isinstance(check[field], str) or not check[field]:
                        raise ValueError(f"Empty grader field in {path}")


def load_cases(cases_dir: Path) -> list[EvalCase]:
    cases = [EvalCase.load(path) for path in sorted(cases_dir.rglob("*.json"))]
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Eval case IDs must be unique")
    return cases
