"""Compare two JSONL eval reports produced by ``python -m evals``."""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_report(path: Path) -> list[dict]:
    rows = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
                raise ValueError(f"invalid result on line {number}")
            rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read eval report {path}: {error}") from error
    if not rows:
        raise ValueError(f"Eval report is empty: {path}")
    return rows


def report_metrics(rows: list[dict]) -> dict:
    completed = [row for row in rows if row.get("goal_status") == "complete"]
    return {
        "runs": len(rows),
        "success_rate": sum(bool(row.get("passed")) for row in rows) / len(rows),
        "false_completion_rate": (
            sum(not bool(row.get("passed")) for row in completed) / len(completed) if completed else None
        ),
        "average_model_calls": _mean(rows, "model_calls"),
        "average_tool_calls": _mean(rows, "tool_calls"),
        "average_input_tokens": _mean(rows, "input_tokens_estimated"),
        "average_output_tokens": _mean(rows, "output_tokens_estimated"),
        "average_duration_ms": _mean(rows, "duration_ms"),
    }


def _mean(rows: list[dict], field: str) -> float:
    values = [row.get(field, 0) for row in rows]
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        raise ValueError(f"Non-numeric {field} in report")
    return statistics.fmean(values)


def case_rates(rows: list[dict]) -> dict[str, tuple[int, int, float]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(bool(row.get("passed")))
    return {
        case_id: (sum(values), len(values), sum(values) / len(values))
        for case_id, values in sorted(grouped.items())
    }


def render_comparison(baseline: list[dict], candidate: list[dict], baseline_name: str, candidate_name: str) -> str:
    old = report_metrics(baseline)
    new = report_metrics(candidate)
    lines = [
        "# yu Eval Comparison",
        "",
        f"Baseline: `{baseline_name}`  ",
        f"Candidate: `{candidate_name}`",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
        _metric("Success rate", old["success_rate"], new["success_rate"], percent=True),
        _metric("Goal false completion rate", old["false_completion_rate"], new["false_completion_rate"], percent=True),
        _metric("Average model calls", old["average_model_calls"], new["average_model_calls"]),
        _metric("Average tool calls", old["average_tool_calls"], new["average_tool_calls"]),
        _metric("Average estimated input tokens", old["average_input_tokens"], new["average_input_tokens"]),
        _metric("Average estimated output tokens", old["average_output_tokens"], new["average_output_tokens"]),
        _metric("Average duration (ms)", old["average_duration_ms"], new["average_duration_ms"]),
        "",
        "## Results by case",
        "",
        "| Case | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
    ]
    old_cases, new_cases = case_rates(baseline), case_rates(candidate)
    for case_id in sorted(set(old_cases) | set(new_cases)):
        left, right = old_cases.get(case_id), new_cases.get(case_id)
        left_text = f"{left[0]}/{left[1]} ({left[2]:.1%})" if left else "missing"
        right_text = f"{right[0]}/{right[1]} ({right[2]:.1%})" if right else "missing"
        delta = f"{right[2] - left[2]:+.1%}" if left and right else "N/A"
        lines.append(f"| {case_id} | {left_text} | {right_text} | {delta} |")
    return "\n".join(lines) + "\n"


def _metric(label: str, old: float | None, new: float | None, percent: bool = False) -> str:
    if old is None or new is None:
        return f"| {label} | {_format(old, percent)} | {_format(new, percent)} | N/A |"
    delta = new - old
    return f"| {label} | {_format(old, percent)} | {_format(new, percent)} | {_format(delta, percent, signed=True)} |"


def _format(value: float | None, percent: bool, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    if percent:
        return f"{value:+.1%}" if signed else f"{value:.1%}"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--fail-on-success-drop",
        action="store_true",
        help="Exit 1 when aggregate candidate success rate is below the baseline",
    )
    args = parser.parse_args()
    baseline, candidate = load_report(args.baseline), load_report(args.candidate)
    text = render_comparison(baseline, candidate, args.baseline.name, args.candidate.name)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(args.output)
    else:
        print(text, end="")
    dropped = report_metrics(candidate)["success_rate"] < report_metrics(baseline)["success_rate"]
    return int(args.fail_on_success_drop and dropped)


if __name__ == "__main__":
    raise SystemExit(main())
