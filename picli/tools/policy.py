"""Three-level ALLOW / ASK / DENY policy for host operations."""

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    risk: str
    reason: str


class PermissionPolicy:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.home = Path.home().resolve()
        self.internal = self.workspace / ".pi-python"
        self.protected_write_roots = tuple(
            Path(path).resolve()
            for path in (
                "/System",
                "/Library",
                "/Applications",
                "/private/etc",
                "/private/var/db",
                "/bin",
                "/sbin",
                "/usr",
            )
        )

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        return path == root or path.is_relative_to(root)

    def file(self, action: str, path: Path) -> PolicyResult:
        path = path.resolve()
        if self._within(path, self.internal):
            return PolicyResult(Decision.DENY, "agent-state", "Agent session and Goal state cannot be modified through tools.")
        if self._is_private_credential(path):
            return PolicyResult(Decision.DENY, "credential", "Private keys, credential stores, cookies, and login databases are never exposed to the model.")
        if str(path).startswith("/dev/disk"):
            return PolicyResult(Decision.DENY, "raw-device", "Direct access to raw disk devices is prohibited.")
        if action in {"write", "edit"} and any(self._within(path, root) for root in self.protected_write_roots):
            return PolicyResult(Decision.DENY, "system-modification", "Structured file tools cannot modify protected operating-system locations.")
        if self._looks_sensitive(path):
            return PolicyResult(Decision.ASK, "sensitive-data", "The target may contain credentials or secrets.")
        if self._within(path, self.workspace):
            return PolicyResult(Decision.ALLOW, "workspace", "Ordinary file operation inside the trusted workspace.")
        risk = "sensitive-data" if action == "read" else "host-modification"
        return PolicyResult(Decision.ASK, risk, "The target is outside the trusted workspace.")

    def shell(self, command: str) -> PolicyResult:
        command = command.strip()
        if not command:
            return PolicyResult(Decision.DENY, "invalid-command", "Empty shell commands are prohibited.")
        denial = self._shell_denial_reason(command)
        if denial:
            return PolicyResult(Decision.DENY, "destructive-command", denial)
        if self._safe_shell(command):
            return PolicyResult(Decision.ALLOW, "read-only", "Strictly parsed read-only command in the trusted workspace.")
        return PolicyResult(
            Decision.ASK,
            "host-command",
            "The command may execute code, access the network, or modify files and processes.",
        )

    def _is_private_credential(self, path: Path) -> bool:
        text = str(path).lower()
        name = path.name.lower()
        ssh = self.home / ".ssh"
        if self._within(path, ssh) and name.startswith("id_") and not name.endswith(".pub"):
            return True
        exact = {
            self.home / ".aws" / "credentials",
            self.home / ".config" / "gcloud" / "application_default_credentials.json",
            self.home / ".netrc",
        }
        if path in exact:
            return True
        if self._within(path, self.home / "Library" / "Keychains"):
            return True
        return any(marker in text for marker in ("/cookies", "/login data", "/logins.json", "/key4.db"))

    @staticmethod
    def _looks_sensitive(path: Path) -> bool:
        name = path.name.lower()
        if name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"}:
            return True
        if path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}:
            return True
        return name in {"credentials.json", "secrets.json", ".npmrc", ".pypirc"}

    def _shell_denial_reason(self, command: str) -> str | None:
        lowered = " ".join(command.lower().split())
        secret_markers = (
            "~/.ssh/id_",
            "$home/.ssh/id_",
            str(self.home / ".ssh" / "id_").lower(),
            "~/.aws/credentials",
            "$home/.aws/credentials",
            "library/keychains",
            "/cookies",
            "/login data",
        )
        if any(marker in lowered for marker in secret_markers):
            return "Shell access to private credentials or login stores is prohibited."
        patterns = (
            (
                r"\brm\s+(?:-[^\s]+\s+)*(?:/|~|\$home|/users|/system|/library|/applications|/private|/usr|/bin|/sbin|/var|/etc)(?:\s|$|\*|/\*)",
                "Recursive or broad deletion of a system or home root is prohibited.",
            ),
            (r"\bfind\s+(?:/|~|\$home)\b.*\s-delete\b", "Broad find -delete operations are prohibited."),
            (r"\b(?:mkfs(?:\.[a-z0-9]+)?|fdisk)\b", "Formatting or repartitioning filesystems is prohibited."),
            (r"\bdd\b[^\n]*\bof\s*=\s*/dev/", "Writing directly to a device with dd is prohibited."),
            (r"\bdiskutil\b[^\n]*\b(?:erase\w*|partition\w*|deletevolume|apfs\s+delete)\b", "Erasing or repartitioning disks is prohibited."),
            (r"\b(?:chmod|chown)\b[^\n]*\s-r\b[^\n]*(?:\s/|\s~|\$home)", "Recursive permission changes on system or home roots are prohibited."),
            (r"\b(?:shutdown|reboot|halt|poweroff)\b", "Power and reboot commands are prohibited."),
            (r"\bcsrutil\s+disable\b|\bspctl\s+--master-disable\b", "Disabling operating-system security controls is prohibited."),
            (r"socketfilterfw[^\n]*--setglobalstate\s+off", "Disabling the system firewall is prohibited."),
            (r"(?:>|\btee\b)[^\n]*/(?:private/)?etc/sudoers", "Modifying sudoers is prohibited."),
            (r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:", "Fork bombs are prohibited."),
            (r"\b(?:curl|wget)\b[^\n]*(?:\||\$\()[^\n]*\b(?:sh|bash|zsh)\b", "Piping downloaded content directly into a shell is prohibited."),
            (r"\bbase64\b[^\n]*-d[^\n]*\|[^\n]*\b(?:sh|bash|zsh)\b", "Executing encoded shell content is prohibited."),
        )
        for pattern, reason in patterns:
            if re.search(pattern, lowered):
                return reason
        return None

    def _safe_shell(self, command: str) -> bool:
        if any(marker in command for marker in ("\n", ";", "&", "|", ">", "<", "`", "$(", "${")):
            return False
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if tokens == ["pwd"]:
            return True
        if tokens and tokens[0] == "ls":
            allowed_flags = {"-a", "-l", "-la", "-al", "-1", "-h", "-lh", "-hl", "-lah", "-lha", "-alh"}
            operands = [token for token in tokens[1:] if token not in allowed_flags]
            if any(token.startswith("-") for token in operands):
                return False
            return all(self.file("read", self._shell_path(token)).decision is Decision.ALLOW for token in operands)
        if tokens == ["rg", "--files"]:
            return True
        return False

    def _shell_path(self, value: str) -> Path:
        requested = Path(value).expanduser()
        return requested.resolve() if requested.is_absolute() else (self.workspace / requested).resolve()
