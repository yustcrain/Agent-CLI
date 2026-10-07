"""Interactive permission checks for operations outside the trusted workspace."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class PermissionMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"

    @classmethod
    def parse(cls, value: str | "PermissionMode") -> "PermissionMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError as error:
            expected = ", ".join(mode.value for mode in cls)
            raise ValueError(f"Invalid permission mode; expected one of: {expected}") from error


@dataclass(frozen=True)
class PermissionRequest:
    action: str
    resource: str
    risk: str
    reason: str

    @property
    def fingerprint(self) -> tuple[str, str]:
        return self.action, self.resource


PermissionHandler = Callable[[PermissionRequest], str]


class TerminalPermissionHandler:
    def __call__(self, request: PermissionRequest) -> str:
        if not sys.stdin.isatty():
            return "deny"
        resource = request.resource
        if len(resource) > 1000:
            resource = resource[:1000] + "\n[truncated]"
        print(
            "\n[permission required]"
            f"\nAction: {request.action}"
            f"\nRisk: {request.risk}"
            f"\nTarget: {resource}"
            f"\nReason: {request.reason}",
            flush=True,
        )
        while True:
            try:
                answer = input("Allow? [y] once / [a] this exact operation for this run / [N] deny: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "deny"
            if answer in {"y", "yes"}:
                return "once"
            if answer in {"a", "always"}:
                return "session"
            if answer in {"", "n", "no"}:
                return "deny"
            print("Please enter y, a, or n.", flush=True)


class PermissionManager:
    def __init__(self, handler: PermissionHandler | None = None):
        self.handler = handler or TerminalPermissionHandler()
        self.granted: set[tuple[str, str]] = set()
        self.denied: set[tuple[str, str]] = set()
        self.history: list[tuple[PermissionRequest, str]] = []

    def check_permission(self, request: PermissionRequest) -> bool:
        fingerprint = request.fingerprint
        if fingerprint in self.granted:
            self.history.append((request, "session"))
            return True
        if fingerprint in self.denied:
            self.history.append((request, "deny"))
            return False
        decision = self.handler(request)
        if decision not in {"once", "session", "deny"}:
            decision = "deny"
        if decision == "session":
            self.granted.add(fingerprint)
        elif decision == "deny":
            self.denied.add(fingerprint)
        self.history.append((request, decision))
        return decision in {"once", "session"}

    def clear(self) -> None:
        self.granted.clear()
        self.denied.clear()

    def status(self) -> str:
        grants = sorted(f"{action}: {resource}" for action, resource in self.granted)
        denials = sorted(f"{action}: {resource}" for action, resource in self.denied)
        return (
            "Session approvals:\n"
            + ("\n".join(grants) if grants else "(none)")
            + "\nSession denials:\n"
            + ("\n".join(denials) if denials else "(none)")
        )
