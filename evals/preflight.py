"""Validate scored fixtures: the initial state must fail and the reference must pass."""
import argparse
import shutil
import tempfile
from pathlib import Path
from .execution import run_python_test
from .schema import load_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', required=True, type=Path)
    parser.add_argument('--suite', choices=('all', 'repository', 'bigcodebench'), default='all')
    args = parser.parse_args()
    root = args.suite_root.resolve()
    cases = [
        c for c in load_cases(root / 'cases')
        if c.suite in {'repository', 'bigcodebench'} and (args.suite == 'all' or c.suite == args.suite)
    ]
    if not cases:
        parser.error('No preflightable cases')
    failures = []
    for case in cases:
        fixture = (root / case.fixture).resolve()
        checks = [(root / item['path']).resolve() for item in case.checks if item['type'] == 'python_test']
        if not checks:
            failures.append(case.id)
            print(f'{case.id}: ENVIRONMENT/GRADER FAILURE\nno python_test checks')
            continue
        if not fixture.is_relative_to(root) or any(not check.is_relative_to(root) for check in checks):
            raise ValueError('Case path escapes suite')
        with tempfile.TemporaryDirectory(prefix='picli-preflight-') as directory:
            workspace = Path(directory)/'workspace'
            shutil.copytree(fixture, workspace)
            initial = [run_python_test(check.read_text(), workspace, case.config.get('test_image')) for check in checks]
            _apply_reference(root, case, workspace)
            gold = [run_python_test(check.read_text(), workspace, case.config.get('test_image')) for check in checks]
        passed = any(result.returncode != 0 for result in initial) and all(result.returncode == 0 for result in gold)
        print(f'{case.id}: {"READY" if passed else "ENVIRONMENT/GRADER FAILURE"}')
        if not passed:
            failures.append(case.id)
            for result in gold:
                if result.returncode:
                    print((result.stdout+result.stderr)[-2000:])
    return int(bool(failures))


def _apply_reference(root, case, workspace):
    if case.reference:
        reference = (root / case.reference).resolve()
        if not reference.is_relative_to(root):
            raise ValueError('Reference path escapes suite')
        if reference.is_dir():
            shutil.copytree(reference, workspace, dirs_exist_ok=True)
            return
        if reference.is_file():
            shutil.copyfile(reference, workspace / reference.name)
            return
        raise ValueError(f'Missing reference for {case.id}: {case.reference}')
    if case.suite == 'bigcodebench':
        reference = root / 'references' / f'{case.id}.py'
        shutil.copyfile(reference, workspace / 'solution.py')
        return
    raise ValueError(f'No reference configured for {case.id}')


if __name__ == '__main__':
    raise SystemExit(main())
