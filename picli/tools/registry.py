"""Typed tool registration and dispatch independent from the agent loop."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


ToolHandler = Callable[[dict[str, str]], str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fields: dict[str, str]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string", "description": description}
                        for key, description in self.fields.items()
                    },
                    "required": list(self.fields),
                    "additionalProperties": False,
                },
            },
        }


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if not spec.name or spec.name in self._tools:
            raise ValueError(f"Tool already registered or invalid: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec, handler)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in self.names()]

    def schemas(self) -> list[dict]:
        return [spec.schema() for spec in self.specs()]

    def execute(self, name: str, arguments: object) -> str:
        registered = self._tools.get(name)
        if registered is None:
            raise ValueError(f"Unknown tool: {name}")
        fields = registered.spec.fields
        if not isinstance(arguments, dict) or set(arguments) != set(fields):
            raise ValueError(f"Expected arguments: {', '.join(fields)}")
        if not all(isinstance(value, str) for value in arguments.values()):
            raise ValueError("Tool arguments must be strings")
        return registered.handler(arguments)

    def extend(self, tools: Iterable[tuple[ToolSpec, ToolHandler]]) -> None:
        for spec, handler in tools:
            self.register(spec, handler)

