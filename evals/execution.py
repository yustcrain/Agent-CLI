"""Execute external verifiers; public benchmark checks use a disposable container."""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


DOCKER_DESKTOP_CANDIDATES = (
    Path.home() / '.docker/bin/docker',
    Path('/Applications/Docker.app/Contents/Resources/bin/docker'),
)


def docker_executable() -> str | None:
    """Find Docker in PATH or in Docker Desktop's standard macOS locations."""
    executable = shutil.which('docker')
    if executable:
        return executable
    for candidate in DOCKER_DESKTOP_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def run_python_test(code: str, workspace: Path, image: str | None = None, timeout=20):
    if image is None:
        return subprocess.run([sys.executable, '-B', '-c', code], cwd=workspace,
                              capture_output=True, text=True, timeout=timeout, check=False)
    docker = docker_executable()
    if docker is None:
        message = (
            'Docker CLI not found. Start Docker Desktop and add ~/.docker/bin to PATH, '
            'or install Docker Desktop in /Applications.'
        )
        return subprocess.CompletedProcess(['docker'], 127, '', message)
    name = 'picli-check-' + uuid.uuid4().hex
    bootstrap = ("import shutil, os\nshutil.copytree('/submission', '/tmp/check')\n"
                 "os.chdir('/tmp/check')\n" + code)
    command = [docker, 'run', '--rm', '--name', name, '-i', '--network', 'none',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--pids-limit', '128', '--memory', '512m', '--cpus', '1',
               '--mount', f'type=bind,source={workspace},target=/submission,readonly',
               image, 'python', '-B', '-']
    try:
        return subprocess.run(command, input=bootstrap, capture_output=True, text=True,
                              timeout=timeout, check=False)
    finally:
        try:
            subprocess.run([docker, 'rm', '-f', name], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
