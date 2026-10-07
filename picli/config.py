"""Layered application configuration with validation and redacted inspection."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from picli.tools.permissions import PermissionMode


ENVIRONMENT_KEYS = {
    "provider": "PROVIDER",
    "base_url": "BASE_URL",
    "api_key": "API_KEY",
    "auth_token": "AUTH_TOKEN",
    "model": "MODEL",
    "permission_mode": "PERMISSION_MODE",
    "context_window": "CONTEXT_WINDOW",
    "compact_at": "COMPACT_AT",
    "keep_turns": "KEEP_TURNS",
    "goal_cycles": "GOAL_CYCLES",
    "max_turns": "MAX_TURNS",
    "tool_timeout": "TOOL_TIMEOUT",
    "output_format": "OUTPUT_FORMAT",
}


@dataclass(frozen=True)
class AppConfig:
    provider: str = "auto"
    base_url: str = ""
    api_key: str = ""
    auth_token: str = ""
    model: str = ""
    permission_mode: str = "workspace-write"
    context_window: int = 32000
    compact_at: float = 0.75
    keep_turns: int = 4
    goal_cycles: int = 12
    max_turns: int = 20
    tool_timeout: int = 60
    output_format: str = "text"
    loaded_files: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def load(
        cls,
        workspace: Path,
        explicit_path: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> "AppConfig":
        environment = dict(os.environ if environ is None else environ)
        values: dict[str, object] = {}
        loaded: list[str] = []
        paths = [
            Path.home() / ".config" / "pi-cli" / "config.json",
            workspace / ".pi-python" / "config.json",
        ]
        if explicit_path is not None:
            paths.append(explicit_path.expanduser().resolve())
        for path in paths:
            if not path.is_file():
                if explicit_path is not None and path == explicit_path.expanduser().resolve():
                    raise ValueError(f"Config file does not exist: {path}")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON config: {path}: {error.msg}") from error
            if not isinstance(data, dict):
                raise ValueError(f"Config must contain a JSON object: {path}")
            # Ignore obsolete semantic-judge settings in existing configuration files.
            for obsolete in ("goal_judge_model", "goal_judge_base_url", "goal_judge_api_key"):
                data.pop(obsolete, None)
            unknown = set(data) - set(ENVIRONMENT_KEYS)
            if unknown:
                raise ValueError(f"Unknown config keys in {path}: {', '.join(sorted(unknown))}")
            values.update(data)
            loaded.append(str(path))
        for field_name, variable in ENVIRONMENT_KEYS.items():
            if variable in environment:
                values[field_name] = environment[variable]
        if "provider" not in values and (
            environment.get("ANTHROPIC_API_KEY") or environment.get("ANTHROPIC_AUTH_TOKEN")
        ) and not (environment.get("API_KEY") or environment.get("OPENAI_API_KEY")):
            values["provider"] = "anthropic"
        configured_provider = str(values.get("provider", "auto"))
        if "api_key" not in values and configured_provider == "anthropic" and environment.get("ANTHROPIC_API_KEY"):
            values["api_key"] = environment["ANTHROPIC_API_KEY"]
        if "api_key" not in values and environment.get("OPENAI_API_KEY"):
            values["api_key"] = environment["OPENAI_API_KEY"]
        if "api_key" not in values and environment.get("ANTHROPIC_API_KEY"):
            values["api_key"] = environment["ANTHROPIC_API_KEY"]
        if "auth_token" not in values and environment.get("ANTHROPIC_AUTH_TOKEN"):
            values["auth_token"] = environment["ANTHROPIC_AUTH_TOKEN"]
        if "base_url" not in values:
            provider_name = str(values.get("provider", "auto"))
            if provider_name == "anthropic" and environment.get("ANTHROPIC_BASE_URL"):
                values["base_url"] = environment["ANTHROPIC_BASE_URL"]
            elif environment.get("OPENAI_BASE_URL"):
                values["base_url"] = environment["OPENAI_BASE_URL"]
        values["loaded_files"] = tuple(loaded)
        return cls._validated(values)

    @classmethod
    def _validated(cls, values: dict[str, object]) -> "AppConfig":
        integer_fields = ("context_window", "keep_turns", "goal_cycles", "max_turns", "tool_timeout")
        for name in integer_fields:
            if name in values:
                try:
                    values[name] = int(values[name])
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Config {name} must be an integer") from error
                if values[name] < 1:
                    raise ValueError(f"Config {name} must be positive")
        if "compact_at" in values:
            try:
                values["compact_at"] = float(values["compact_at"])
            except (TypeError, ValueError) as error:
                raise ValueError("Config compact_at must be a number") from error
            if not 0.1 <= values["compact_at"] <= 0.95:
                raise ValueError("Config compact_at must be between 0.1 and 0.95")
        permission_mode = str(values.get("permission_mode", cls.permission_mode))
        PermissionMode.parse(permission_mode)
        values["permission_mode"] = permission_mode
        provider = str(values.get("provider", cls.provider)).lower()
        if provider not in {"auto", "openai", "openai-compatible", "anthropic", "mock"}:
            raise ValueError("Config provider must be auto, openai-compatible, anthropic, or mock")
        values["provider"] = provider
        output_format = str(values.get("output_format", cls.output_format)).lower()
        if output_format not in {"text", "json"}:
            raise ValueError("Config output_format must be text or json")
        values["output_format"] = output_format
        for name in (
            "base_url",
            "api_key",
            "auth_token",
            "model",
        ):
            if name in values:
                values[name] = str(values[name])
        return cls(**values)

    def override(self, **values: object) -> "AppConfig":
        selected = {key: value for key, value in values.items() if value is not None}
        merged = asdict(self)
        merged.update(selected)
        merged["loaded_files"] = self.loaded_files
        return self._validated(merged)

    def public(self) -> dict:
        data = asdict(self)
        data["api_key"] = "configured" if self.api_key else "missing"
        data["auth_token"] = "configured" if self.auth_token else "missing"
        data["loaded_files"] = list(self.loaded_files)
        return data
