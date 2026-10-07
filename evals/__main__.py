"""Run with python -m evals."""

import argparse
import os
import sys
from pathlib import Path

from .runner import EvalRunner, summarize, write_report
from .schema import SUITES, load_cases


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run isolated real-model evaluations for yu")
    parser.add_argument(
        "--provider",
        choices=("auto", "openai", "openai-compatible", "anthropic", "mock"),
        default=os.getenv("PROVIDER", "auto"),
    )
    parser.add_argument("--model", default=os.getenv("MODEL", ""))
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", ""))
    parser.add_argument("--suite-root", type=Path, default=root)
    parser.add_argument("--suite", choices=(*sorted(SUITES), "all"), default="regression")
    parser.add_argument("--case", action="append", dest="case_ids", help="Case ID; repeat to select multiple")
    parser.add_argument("--runs", type=positive, default=1, help="Repetitions per case")
    parser.add_argument("--allow-bash", action="store_true", help="Expose bash to the evaluated agent")
    parser.add_argument("--list", action="store_true", help="List cases and exit")
    parser.add_argument("--validate", action="store_true", help="Validate cases and fixtures without calling a model")
    parser.add_argument("--output-dir", type=Path, default=root / "results")
    args = parser.parse_args()
    root = args.suite_root.resolve()
    cases = load_cases(root / "cases")
    selected = [case for case in cases if (case.id in args.case_ids if args.case_ids else args.suite == "all" or case.suite == args.suite)]
    if not selected:
        parser.error("No cases selected; import the requested dataset first")
    unknown = set(args.case_ids or []) - {case.id for case in cases}
    if unknown:
        parser.error("Unknown eval cases: " + ", ".join(sorted(unknown)))
    for case in selected:
        fixture = (root / case.fixture).resolve()
        if case.reference:
            reference = (root / case.reference).resolve()
            if not reference.is_relative_to(root) or not reference.exists():
                parser.error(f"Invalid reference for {case.id}: {case.reference}")
        for check in case.checks:
            if check["type"] == "python_test":
                test = (root / check["path"]).resolve()
                if not test.is_relative_to(root) or not test.is_file():
                    parser.error(f"Invalid external test for {case.id}")
        if not fixture.is_relative_to(root) or not fixture.is_dir():
            parser.error(f"Invalid fixture for {case.id}: {case.fixture}")
    if args.list:
        for case in selected:
            print(f"{case.id:24} {case.category:12} {case.description}")
        return 0
    if args.validate:
        print(f"Validated {len(selected)} eval cases and fixtures.")
        return 0
    if not args.model and args.provider != "mock":
        parser.error("Set --model or MODEL; --validate does not call a model")
    runner = EvalRunner(
        root,
        args.base_url,
        os.getenv("API_KEY", os.getenv("OPENAI_API_KEY", "")),
        args.model,
        args.allow_bash,
        args.provider,
        os.getenv("AUTH_TOKEN", os.getenv("ANTHROPIC_AUTH_TOKEN", "")),
    )
    results = []
    for case in selected:
        for repetition in range(1, args.runs + 1):
            print(f"[{case.id} run {repetition}/{args.runs}]", flush=True)
            result = runner.run_case(case, repetition)
            results.append(result)
            print(
                f"  {'PASS' if result.passed else 'FAIL'} | model={result.model_calls} "
                f"tools={result.tool_calls} duration={result.duration_ms}ms",
                flush=True,
            )
            if not result.passed:
                if result.error:
                    print(f"    agent error: {result.error}", flush=True)
                for grade in result.grades:
                    if not grade["passed"]:
                        evidence = " | ".join(
                            line.strip() for line in grade["evidence"].splitlines() if line.strip()
                        )
                        if len(evidence) > 800:
                            evidence = evidence[:797] + "..."
                        print(f"    {grade['check']['type']}: {evidence}", flush=True)
    jsonl_path, markdown_path = write_report(args.output_dir.resolve(), results, args.model)
    metrics = summarize(results)
    print(f"Success: {metrics['passed']}/{metrics['runs']} ({metrics['success_rate']:.1%})")
    print(f"Detailed results: {jsonl_path}")
    print(f"Summary report: {markdown_path}")
    return 0 if metrics["passed"] == metrics["runs"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"Eval error: {error}", file=sys.stderr)
        sys.exit(2)
