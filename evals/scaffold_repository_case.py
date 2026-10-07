"""Capture an initial repository, fixed reference, and hidden check as an eval case."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from .schema import CASE_ID


IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED or name == ".DS_Store"}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and not any(part in IGNORED for part in child.parts):
            digest.update(str(child.relative_to(path)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def capture(root: Path, case_id: str, description: str, prompt: str, initial: Path,
            reference: Path, check: Path, image: str) -> Path:
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("Case ID must use lowercase letters, numbers, underscores, or hyphens")
    for path, label in ((initial, "initial"), (reference, "reference")):
        if not path.is_dir():
            raise ValueError(f"{label} must be a directory: {path}")
    if not check.is_file():
        raise ValueError(f"check must be a Python file: {check}")
    case_path = root / "cases" / f"{case_id}.json"
    fixture_path = root / "fixtures" / case_id
    reference_path = root / "references" / case_id
    check_path = root / "checks" / f"{case_id}.py"
    destinations = (case_path, fixture_path, reference_path, check_path)
    if any(path.exists() for path in destinations):
        raise ValueError(f"Case already exists: {case_id}")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    check_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(initial, fixture_path, ignore=_ignore)
    shutil.copytree(reference, reference_path, ignore=_ignore)
    shutil.copyfile(check, check_path)
    case = {
        "id": case_id,
        "description": description,
        "category": "repository",
        "mode": "agent",
        "fixture": f"fixtures/{case_id}",
        "turns": [prompt],
        "checks": [
            {"type": "python_test", "path": f"checks/{case_id}.py"},
            {"type": "policy_no_violation"},
        ],
        "config": {"max_turns": 24, "test_image": image},
        "suite": "repository",
        "reference": f"references/{case_id}",
        "provenance": {
            "type": "captured-local-failure",
            "initial_sha256": _digest(initial),
            "reference_sha256": _digest(reference),
        },
    }
    case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return case_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--initial", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--check", required=True, type=Path)
    parser.add_argument("--suite-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--image", default="python:3.11-slim")
    args = parser.parse_args()
    path = capture(args.suite_root.resolve(), args.id, args.description, args.prompt,
                   args.initial.resolve(), args.reference.resolve(), args.check.resolve(), args.image)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
