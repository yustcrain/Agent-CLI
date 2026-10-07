"""Machine-checkable acceptance contracts and deterministic verification."""

import re
from dataclasses import asdict, dataclass

from picli.tools.builtin import Tools


CHECK_FIELDS = {
    "command": {"command"},
    "file_exists": {"path"},
    "file_equals": {"path", "text"},
    "file_contains": {"path", "text"},
    "file_not_contains": {"path", "text"},
    "manual": {"instructions"},
}


def validate_criteria(
    value: object,
    allow_bash: bool | None = None,
    allow_manual: bool = True,
) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("criteria")
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError("Acceptance plan must contain 1 to 12 criteria")
    criteria: list[dict] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Acceptance criterion must be an object")
        identifier = item.get("id")
        description = item.get("description")
        check = item.get("check")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,31}", identifier):
            raise ValueError("Acceptance criterion has an invalid ID")
        if identifier in ids:
            raise ValueError("Acceptance criterion IDs must be unique")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Acceptance criterion needs a description")
        if not isinstance(check, dict) or check.get("type") not in CHECK_FIELDS:
            raise ValueError("Acceptance criterion has an invalid check")
        check_type = check["type"]
        if check_type == "command" and allow_bash is False:
            raise ValueError("Command acceptance checks require Bash to be enabled")
        if check_type == "manual" and not allow_manual:
            raise ValueError("Manual acceptance checks are unavailable in unattended runs")
        expected = CHECK_FIELDS[check_type]
        if set(check) != {"type", *expected}:
            raise ValueError(f"Invalid fields for {check_type} acceptance check")
        for field in expected:
            field_value = check.get(field)
            if not isinstance(field_value, str):
                raise ValueError(f"Invalid field in {check_type} acceptance check")
            if not field_value.strip() and not (check_type == "file_equals" and field == "text"):
                raise ValueError(f"Empty field in {check_type} acceptance check")
        ids.add(identifier)
        criteria.append({"id": identifier, "description": description.strip(), "check": check})
    return criteria


@dataclass(frozen=True)
class VerificationResult:
    criterion_id: str
    status: str
    evidence: str

    def as_dict(self) -> dict:
        return asdict(self)


class AcceptanceVerifier:
    def __init__(self, tools: Tools):
        self.tools = tools

    def verify(self, criteria: list[dict], approvals: list[str]) -> list[VerificationResult]:
        return [self._verify_one(criterion, approvals) for criterion in criteria]

    def _verify_one(self, criterion: dict, approvals: list[str]) -> VerificationResult:
        identifier = criterion["id"]
        check = criterion["check"]
        check_type = check["type"]
        try:
            if check_type == "manual":
                if identifier in approvals:
                    return VerificationResult(identifier, "passed", "Explicitly approved by the user")
                return VerificationResult(identifier, "pending", check["instructions"])
            if check_type == "command":
                result = self.tools.run_shell(check["command"])
                output = result.output[-4000:].strip()
                if result.error and result.exit_code is None:
                    status = "blocked" if not self.tools.allow_bash else "failed"
                    evidence = result.error + (f"\n{output}" if output else "")
                    return VerificationResult(identifier, status, evidence)
                evidence = f"$ {check['command']}\nexit={result.exit_code}"
                if output:
                    evidence += f"\n{output}"
                return VerificationResult(
                    identifier,
                    "passed" if result.exit_code == 0 else "failed",
                    evidence,
                )
            path = self.tools.path(check["path"])
            relative = path.relative_to(self.tools.workspace)
            if check_type == "file_exists":
                return VerificationResult(
                    identifier,
                    "passed" if path.exists() else "failed",
                    f"{relative}: {'exists' if path.exists() else 'does not exist'}",
                )
            if not path.is_file():
                return VerificationResult(identifier, "failed", f"{relative}: file does not exist")
            content = path.read_text(encoding="utf-8")
            if check_type == "file_equals":
                matches = content == check["text"]
                evidence = (
                    f"{relative}: exact content matches"
                    if matches
                    else f"{relative}: exact content differs (expected {len(check['text'])} characters, got {len(content)})"
                )
                return VerificationResult(identifier, "passed" if matches else "failed", evidence)
            contains = check["text"] in content
            expected = contains if check_type == "file_contains" else not contains
            phrase = "contains" if contains else "does not contain"
            return VerificationResult(identifier, "passed" if expected else "failed", f"{relative}: {phrase} expected text")
        except (OSError, UnicodeError, ValueError) as error:
            return VerificationResult(identifier, "failed", str(error))


def format_criteria(criteria: list[dict]) -> str:
    lines = []
    for criterion in criteria:
        check = criterion["check"]
        detail = check.get("command") or check.get("path") or check.get("instructions")
        lines.append(f"{criterion['id']}: {criterion['description']} [{check['type']}: {detail}]")
    return "\n".join(lines)


def format_results(results: list[dict]) -> str:
    return "\n".join(
        f"{result['criterion_id']} {result['status'].upper()}: {result['evidence']}" for result in results
    )
