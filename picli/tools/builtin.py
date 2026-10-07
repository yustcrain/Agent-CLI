"""Workspace tools exposed to the model."""

import os
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from picli.tools.permissions import PermissionManager, PermissionMode, PermissionRequest
from picli.tools.policy import Decision, PermissionPolicy
from picli.skills.registry import SkillRegistry
from picli.tools.registry import ToolHandler, ToolRegistry, ToolSpec


ANSI_SEQUENCE = re.compile(
    r"\x1b(?:"
    r"\][^\x07\x1b]*(?:\x07|\x1b\\)|"  # OSC title/clipboard sequences
    r"[PX^_].*?\x1b\\|"                   # DCS/SOS/PM/APC strings
    r"\[[0-?]*[ -/]*[@-~]|"               # CSI cursor/color/screen controls
    r"[()][0-2A-Z]|"                       # character-set selection
    r"[=>]|"                               # keypad application/numeric mode
    r"[@-_]"                               # two-byte escape sequences
    r")",
    re.DOTALL,
)


def sanitize_process_output(text: str) -> str:
    """Remove terminal controls while preserving ordinary text for the model."""
    without_ansi = ANSI_SEQUENCE.sub("", text)
    return "".join(
        character
        for character in without_ansi
        if character in {"\n", "\t"} or ord(character) >= 32 and ord(character) != 127
    )


BUILTIN_SPECS = (
    ToolSpec("read", "Read a UTF-8 file", {"path": "Workspace-relative, absolute, or ~/ file path"}),
    ToolSpec(
        "write",
        "Write complete UTF-8 file content",
        {"path": "Workspace-relative, absolute, or ~/ file path", "content": "Complete new file content"},
    ),
    ToolSpec(
        "edit",
        "Replace one unique text occurrence in a UTF-8 file",
        {
            "path": "Workspace-relative, absolute, or ~/ file path",
            "old_text": "Unique exact text",
            "new_text": "Replacement",
        },
    ),
    ToolSpec("bash", "Run a shell command in the workspace", {"command": "Shell command executed in workspace"}),
    ToolSpec(
        "read_skill",
        "Load complete instructions for one discovered project skill",
        {"name": "Exact name from the available project skills catalog"},
    ),
)

# Backward-compatible schema export. Runtime providers receive schemas from each Tools registry.
SCHEMAS = [spec.schema() for spec in BUILTIN_SPECS]


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str
    error: str | None = None

    def render(self) -> str:
        if self.error:
            return f"Tool error: {self.error}" + (f"\n{self.output}" if self.output else "")
        return f"Exit {self.exit_code}\n{self.output}"


class Tools:
    def __init__(
        self,
        workspace: Path,
        allow_bash: bool,
        timeout: int,
        permissions: PermissionManager | None = None,
        permission_mode: str | PermissionMode = PermissionMode.WORKSPACE_WRITE,
    ):
        self.workspace = workspace
        self.permission_mode = PermissionMode.parse(permission_mode)
        self.allow_bash = allow_bash and self.permission_mode is not PermissionMode.READ_ONLY
        self.timeout = timeout
        self.permissions = permissions or PermissionManager()
        self.policy = PermissionPolicy(workspace)
        self.skills = SkillRegistry(workspace)
        self.registry = ToolRegistry()
        handlers = {
            "read": self._read,
            "write": self._write,
            "edit": self._edit,
            "bash": self._bash,
            "read_skill": self._read_skill,
        }
        self.registry.extend((spec, handlers[spec.name]) for spec in BUILTIN_SPECS)

    @property
    def schemas(self) -> list[dict]:
        return self.registry.schemas()

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self.registry.register(spec, handler)

    def set_permission_mode(self, value: str | PermissionMode) -> PermissionMode:
        self.permission_mode = PermissionMode.parse(value)
        self.allow_bash = self.permission_mode is not PermissionMode.READ_ONLY
        return self.permission_mode

    def path(self, value: str) -> Path:
        requested = Path(value).expanduser()
        path = requested.resolve() if requested.is_absolute() else (self.workspace / requested).resolve()
        return path

    def check_file_permission(self, action: str, path: Path) -> bool:
        result = self.policy.file(action, path)
        if result.decision is Decision.DENY:
            raise ValueError(f"Blocked by safety policy: {result.reason}")
        if self.permission_mode is PermissionMode.READ_ONLY and action in {"write", "edit"}:
            raise ValueError(
                f"Permission mode read-only cannot {action} files; "
                "switch to workspace-write or danger-full-access"
            )
        if result.decision is Decision.ALLOW:
            return True
        if self.permission_mode is PermissionMode.DANGER_FULL_ACCESS:
            return True
        labels = {"read": "read file", "write": "write file", "edit": "edit file"}
        return self.permissions.check_permission(
            PermissionRequest(
                labels[action],
                str(path),
                result.risk,
                result.reason,
            )
        )

    def display_path(self, path: Path) -> str:
        return str(path.relative_to(self.workspace)) if path.is_relative_to(self.workspace) else str(path)

    def execute(self, name: str, arguments: object) -> str:
        try:
            return self.registry.execute(name, arguments)
        except (OSError, ValueError, UnicodeError) as error:
            return f"Tool error: {error}"

    def _file_path(self, action: str, arguments: dict[str, str]) -> Path:
        path = self.path(arguments["path"])
        if not self.check_file_permission(action, path):
            raise ValueError(f"Permission denied for {action}: {path}")
        return path

    def _read(self, arguments: dict[str, str]) -> str:
        path = self._file_path("read", arguments)
        with path.open(encoding="utf-8") as source:
            result = source.read(24001)
        return result[:24000] + ("\n[truncated]" if len(result) > 24000 else "")

    def _write(self, arguments: dict[str, str]) -> str:
        path = self._file_path("write", arguments)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return f"write: {self.display_path(path)} succeeded"

    def _edit(self, arguments: dict[str, str]) -> str:
        path = self._file_path("edit", arguments)
        content = path.read_text(encoding="utf-8")
        old = arguments["old_text"]
        if not old or content.count(old) != 1:
            raise ValueError("old_text must be nonempty and match exactly once")
        path.write_text(content.replace(old, arguments["new_text"], 1), encoding="utf-8")
        return f"edit: {self.display_path(path)} succeeded"

    def _bash(self, arguments: dict[str, str]) -> str:
        return self.shell(arguments["command"])

    def _read_skill(self, arguments: dict[str, str]) -> str:
        return self.skills.read(arguments["name"])

    def shell(self, command: str) -> str:
        return self.run_shell(command).render()

    def run_shell(self, command: str) -> CommandResult:
        if self.permission_mode is PermissionMode.READ_ONLY:
            return CommandResult(
                None,
                "",
                "bash unavailable in read-only mode; switch to workspace-write or danger-full-access",
            )
        if not self.allow_bash:
            return CommandResult(
                None,
                "",
                "bash disabled; restart with --permission-mode workspace-write",
            )
        policy = self.policy.shell(command)
        if policy.decision is Decision.DENY:
            return CommandResult(None, "", f"blocked by safety policy: {policy.reason}")
        if (
            policy.decision is Decision.ASK
            and self.permission_mode is not PermissionMode.DANGER_FULL_ACCESS
        ):
            allowed = self.permissions.check_permission(
                PermissionRequest("run shell command", command, policy.risk, policy.reason)
            )
            if not allowed:
                return CommandResult(None, "", "permission denied for shell command")
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                command, shell=True, executable="/bin/bash", cwd=self.workspace,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                process.wait(timeout=self.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if isinstance(error, KeyboardInterrupt):
                    raise
                output.seek(0)
                data = output.read(24001)
                text = sanitize_process_output(data[:24000].decode("utf-8", errors="replace"))
                if len(data) > 24000:
                    text += "\n[truncated]"
                return CommandResult(None, text, f"command timed out after {self.timeout}s")
            output.seek(0)
            data = output.read(24001)
            text = sanitize_process_output(data[:24000].decode("utf-8", errors="replace"))
            if len(data) > 24000:
                text += "\n[truncated]"
            return CommandResult(process.returncode, text)
