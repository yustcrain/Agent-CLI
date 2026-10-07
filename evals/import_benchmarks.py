"""Import pinned public samples without exposing reference answers to the agent.

Offline: --input-jsonl export.jsonl --revision <commit-or-export-version>
Online: install datasets and huggingface_hub, omit --input-jsonl.
"""
import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

DATASETS = {"bigcodebench": "bigcode/bigcodebench", "swebench": "SWE-bench/SWE-bench_Verified"}


def read_rows(benchmark, revision, split, input_jsonl=None):
    if input_jsonl:
        raw = input_jsonl.read_bytes()
        rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        digest = hashlib.sha256(raw).hexdigest()
        return rows, {"dataset": DATASETS[benchmark], "revision": revision, "split": split,
                      "export_sha256": digest, "acquisition": "local-export"}
    try:
        from datasets import load_dataset
        from huggingface_hub import HfApi
    except ImportError as error:
        raise ValueError("Install optional dependencies: pip install datasets huggingface_hub; or pass --input-jsonl") from error
    info = HfApi().dataset_info(DATASETS[benchmark], revision=revision)
    rows = list(load_dataset(DATASETS[benchmark], revision=info.sha, split=split))
    return rows, {"dataset": DATASETS[benchmark], "revision": info.sha, "split": split,
                  "acquisition": "huggingface", "license": "apache-2.0" if benchmark == "bigcodebench" else "see upstream dataset card"}


def stdlib_only(row):
    """Conservative dependency filter; this is not an environment validation."""
    try:
        libs = row.get("libs", "[]")
        libs = ast.literal_eval(libs) if isinstance(libs, str) else libs
        if not isinstance(libs, (list, tuple, set)):
            return False
        imports = {str(x).split('.')[0] for x in libs}
        for source in (row['complete_prompt'] + row['canonical_solution'], row['test']):
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split('.')[0])
        return imports <= sys.stdlib_module_names
    except (ValueError, SyntaxError, KeyError, TypeError):
        return False


def put(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def external_test(test):
    # Shared __main__ namespace matches the upstream combined solution+test form.
    # Tests are supplied only after the agent has finished.
    return ("from pathlib import Path\nimport unittest, sys\n"
            "exec(compile(Path('solution.py').read_text(), 'solution.py', 'exec'), globals())\n"
            + test + "\n"
            "suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])\n"
            "if suite.countTestCases() == 0: raise RuntimeError('No benchmark tests collected')\n"
            "result = unittest.TextTestRunner(verbosity=2).run(suite)\n"
            "raise SystemExit(0 if result.wasSuccessful() else 1)\n")


def import_rows(benchmark, rows, provenance, output, limit=10, ids=None, allow_third_party=False,
                image="python:3.11-slim"):
    key = 'task_id' if benchmark == 'bigcodebench' else 'instance_id'
    if limit < 1:
        raise ValueError('limit must be positive')
    by_id = {row[key]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('Duplicate source task IDs')
    if ids and set(ids) - by_id.keys():
        raise ValueError('Unknown task IDs: ' + ', '.join(sorted(set(ids) - by_id.keys())))
    candidates = [by_id[i] for i in ids] if ids else sorted(rows, key=lambda row: row[key])
    if benchmark == 'bigcodebench' and not allow_third_party:
        eligible = [row for row in candidates if stdlib_only(row)]
        if ids and len(eligible) != len(candidates):
            raise ValueError('Selected tasks require third-party libraries; configure an image and --allow-third-party')
        candidates = eligible
    selected = candidates[:limit]
    if len(selected) < (len(ids) if ids else limit):
        raise ValueError(f'Only {len(selected)} eligible tasks; request fewer or provide a dependency image')
    if output.exists():
        raise ValueError(f'Output already exists: {output}; use a new version directory')
    prepared = []
    for row in selected:
        source_id = row[key]
        if benchmark == 'bigcodebench':
            if not re.fullmatch(r'BigCodeBench/\d+', source_id):
                raise ValueError(f'Unexpected task ID: {source_id}')
            for field in ('complete_prompt', 'canonical_solution', 'test', 'entry_point'):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f'Missing {field}: {source_id}')
            # A dataset should define tests, not invoke an early unittest.main().
            tree = ast.parse(row['test'])
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and isinstance(n.func.value, ast.Name) and n.func.value.id == 'unittest'
                   and n.func.attr == 'main' for n in ast.walk(tree)):
                raise ValueError(f'Unexpected unittest.main in source tests: {source_id}')
        else:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+', source_id):
                raise ValueError(f'Unexpected instance ID: {source_id}')
            if not re.fullmatch(r'[0-9a-f]{40}', row.get('base_commit', '')):
                raise ValueError(f'Invalid base commit: {source_id}')
        prepared.append(row)
    output.mkdir(parents=True)
    manifest = {**provenance, "benchmark": benchmark, "selection": [row[key] for row in prepared],
                "official_score": False, "selection_policy": "explicit IDs" if ids else "sorted IDs; stdlib filter for BigCodeBench",
                "test_image": image}
    put(output, 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    for row in prepared:
        task_provenance = {**provenance, 'task_id': row[key], 'source_row_sha256': hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        if benchmark == 'swebench':
            # Only issue + base checkout instructions go to inference; gold patches stay upstream.
            task = {field: row[field] for field in ('instance_id', 'repo', 'base_commit', 'problem_statement')}
            task['provenance'] = task_provenance
            put(output, f"tasks/{row[key]}.json", json.dumps(task, ensure_ascii=False, indent=2)+'\n')
            continue
        identifier = 'bigcodebench-' + row['task_id'].split('/')[1]
        fixture = f'fixtures/{identifier}'
        put(output, fixture+'/solution.py', row['complete_prompt']+'    raise NotImplementedError\n')
        put(output, f'checks/{identifier}.py', external_test(row['test']))
        # Reference implementation is outside the agent fixture for environment preflight only.
        put(output, f'references/{identifier}.py', row['complete_prompt']+row['canonical_solution'])
        case = dict(id=identifier, description=f"Adapted {row['task_id']} (not an official benchmark score)",
                    category='coding', mode='agent', suite='bigcodebench', fixture=fixture,
                    turns=['Implement solution.py according to its specification. Preserve the entry-point signature.'],
                    checks=[{'type':'python_test','path':f'checks/{identifier}.py'}],
                    config={'max_turns':20, 'test_image':image}, provenance=task_provenance)
        put(output, f'cases/{identifier}.json', json.dumps(case, ensure_ascii=False, indent=2)+'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('benchmark', choices=DATASETS)
    parser.add_argument('--input-jsonl', type=Path)
    parser.add_argument('--revision', required=True, help='Upstream commit/tag, or documented local export version')
    parser.add_argument('--split', help='Defaults: v0.1.4 for BigCodeBench, test for SWE-bench')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--id', action='append', dest='ids')
    parser.add_argument('--allow-third-party', action='store_true')
    parser.add_argument('--image', default='python:3.11-slim', help='Prebuilt Docker image; use a digest for repeatability')
    args = parser.parse_args()
    split = args.split or ('v0.1.4' if args.benchmark == 'bigcodebench' else 'test')
    rows, provenance = read_rows(args.benchmark, args.revision, split, args.input_jsonl)
    result = import_rows(args.benchmark, rows, provenance, args.output, args.limit, args.ids,
                         args.allow_third_party, args.image)
    print(f"Prepared {len(result['selection'])} {args.benchmark} tasks in {args.output}")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
