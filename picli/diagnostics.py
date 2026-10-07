"""Machine-readable runtime status and preflight diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

from picli.config import AppConfig
from picli.providers.client import create_provider
from picli.tools.builtin import Tools


def status_report(config: AppConfig, workspace: Path, tools: Tools, session: str | None = None) -> dict:
    provider_name = create_provider(
        config.provider,
        config.base_url,
        config.api_key,
        config.model,
        False,
        config.auth_token,
    ).provider_name
    return {
        "ok": True,
        "provider": provider_name,
        "model": config.model or ("mock-v1" if config.provider == "mock" else ""),
        "workspace": str(workspace),
        "permission_mode": tools.permission_mode.value,
        "session": session,
        "tools": tools.registry.names(),
        "config_files": list(config.loaded_files),
    }


def doctor_report(config: AppConfig, workspace: Path, tools: Tools) -> dict:
    checks = [
        {
            "name": "python",
            "status": "pass" if sys.version_info >= (3, 10) else "fail",
            "detail": sys.version.split()[0],
        },
        {
            "name": "workspace",
            "status": "pass" if workspace.is_dir() else "fail",
            "detail": str(workspace),
        },
        {
            "name": "tools",
            "status": "pass" if tools.registry.names() else "fail",
            "detail": f"{len(tools.registry.names())} registered",
        },
        {
            "name": "provider",
            "status": "pass" if config.provider == "mock" or bool(config.model) else "warn",
            "detail": (
                "mock-v1 (offline)"
                if config.provider == "mock"
                else (config.provider if config.model else f"{config.provider}; model is not configured")
            ),
        },
        {
            "name": "credentials",
            "status": "pass" if config.provider == "mock" or bool(config.api_key or config.auth_token) else "warn",
            "detail": "not required for mock" if config.provider == "mock" else (
                "configured" if config.api_key or config.auth_token else "API credential is not configured"
            ),
        },
    ]
    return {
        "ok": all(check["status"] != "fail" for check in checks),
        "checks": checks,
        "config_files": list(config.loaded_files),
    }


def text_report(title: str, report: dict) -> str:
    if "checks" in report:
        lines = [title]
        for check in report["checks"]:
            lines.append(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
        return "\n".join(lines)
    lines = [title]
    for key, value in report.items():
        if key == "ok":
            continue
        rendered = ", ".join(value) if isinstance(value, list) else str(value or "(none)")
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines)
