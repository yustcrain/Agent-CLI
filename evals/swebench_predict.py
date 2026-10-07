"""Run picli on one pinned SWE-bench checkout and export an official-format prediction.

Requires a local clone of the task repository and a prebuilt picli Docker image.
Does not grade patches: use SWE-bench's official harness on predictions.jsonl.
"""
import argparse
import io
import json
import os
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, check=True).stdout


def prepare_checkout(repo, commit, workspace):
    archive = git('archive', '--format=tar', commit, cwd=repo)
    workspace.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(workspace, filter='data')


def export_patch(workspace, control):
    command = ['--git-dir', str(control), '--work-tree', str(workspace)]
    git(*command, 'add', '-N', '--', '.', cwd=workspace)
    return git(*command, 'diff', '--no-ext-diff', '--binary', 'HEAD', '--', '.',
               ':(exclude).pi-python', ':(exclude).pi-memory', cwd=workspace).decode()


def predict(task, repo, image, model, provider, output, timeout=600, max_turns=30):
    if timeout < 1 or max_turns < 1:
        raise ValueError('timeout and max-turns must be positive')
    if output.exists():
        raise ValueError('Use a new output directory for each prediction run')
    # git archive resolves only this known commit; no gold patch or future history is copied.
    with tempfile.TemporaryDirectory(prefix='picli-swe-') as directory:
        root = Path(directory)
        workspace = root/'workspace'
        prepare_checkout(repo.resolve(), task['base_commit'], workspace)
        control = root/'git-control'
        git('init', '--separate-git-dir', str(control), str(workspace))
        git('-C', str(workspace), 'add', '--all')
        git('-C', str(workspace), '-c', 'user.name=Eval', '-c', 'user.email=eval@localhost',
            '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Evaluation base')
        (workspace/'.git').unlink()  # Control data stays outside the container mount.
        name = 'picli-swe-' + uuid.uuid4().hex
        command = ['docker', 'run', '--rm', '--name', name, '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--pids-limit', '256',
                   '--memory', '2g', '--cpus', '2',
                   '--mount', f'type=bind,source={workspace},target=/workspace']
        for variable in ('API_KEY', 'AUTH_TOKEN', 'BASE_URL', 'OPENAI_API_KEY',
                         'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
            if os.getenv(variable):
                command += ['-e', variable]
        command += [image, 'python', '-B', '-m', 'picli', '--workspace', '/workspace',
                    '--provider', provider, '--model', model, '--allow-bash',
                    '--permission-mode', 'danger-full-access', '--max-turns', str(max_turns),
                    '--output-format', 'json', '--prompt',
                    task['problem_statement']+'\nResolve the issue by editing repository files. Do not modify tests.']
        timed_out = False
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            stdout, stderr, returncode = run.stdout, run.stderr, run.returncode
        except subprocess.TimeoutExpired:
            timed_out, stdout, stderr, returncode = True, '', 'Agent exceeded wall-clock limit', None
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
        patch = export_patch(workspace, control)
        output.mkdir(parents=True)
        prediction = {'instance_id': task['instance_id'], 'model_name_or_path': model, 'model_patch': patch}
        (output/'predictions.jsonl').write_text(json.dumps(prediction)+'\n')
        (output/'run.json').write_text(json.dumps({
            'instance_id':task['instance_id'], 'base_commit':task['base_commit'],
            'provenance':task.get('provenance', {}), 'model':model, 'provider':provider,
            'image':image, 'returncode':returncode, 'timed_out':timed_out,
            'graded':False, 'stdout':stdout[-20000:], 'stderr':stderr[-4000:],
        }, indent=2))
        return returncode == 0 and not timed_out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', required=True, type=Path)
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--image', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--provider', default='openai-compatible', choices=['openai-compatible','openai','anthropic','mock'])
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--max-turns', type=int, default=30)
    a = p.parse_args()
    task = json.loads(a.task.read_text())
    passed = predict(task, a.repo, a.image, a.model, a.provider, a.output, a.timeout, a.max_turns)
    print(f'Prediction exported to {a.output}; NOT graded. Run the official SWE-bench harness next.')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
