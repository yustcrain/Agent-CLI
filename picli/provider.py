"""Provider abstraction plus OpenAI-compatible and deterministic mock implementations."""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from pathlib import Path

from .acceptance import validate_criteria
from .tools import SCHEMAS


def ssl_context() -> ssl.SSLContext:
    configured = os.getenv("SSL_CERT_FILE")
    if configured:
        return ssl.create_default_context(cafile=configured)
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile:
        return ssl.create_default_context(cafile=defaults.cafile)
    for candidate in (Path("/etc/ssl/cert.pem"), Path("/private/etc/ssl/cert.pem")):
        if candidate.is_file():
            return ssl.create_default_context(cafile=str(candidate))
    return ssl.create_default_context()


class BaseProvider(ABC):
    provider_name = "base"
    demo = False

    def __init__(self, model: str, tool_schemas: list[dict] | None = None):
        self.model = model
        self.tool_schemas = list(tool_schemas or SCHEMAS)

    def set_tool_schemas(self, schemas: list[dict]) -> None:
        self.tool_schemas = list(schemas)

    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def summarize(self, messages: list[dict]) -> str:
        raise NotImplementedError

    @abstractmethod
    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    provider_name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, demo: bool):
        super().__init__(model)
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.demo = demo

    def complete(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
    ) -> dict:
        if self.demo:
            if messages[-1]["role"] == "tool":
                message = {"role": "assistant", "content": "演示工具结果：\n" + messages[-1]["content"]}
            else:
                message = {"role": "assistant", "content": "演示模式：读取工作区 MEMORY.md。", "tool_calls": [{
                    "id": "demo_read", "type": "function", "function": {
                        "name": "read", "arguments": json.dumps({"path": "MEMORY.md"}),
                    },
                }]}
            if on_text and message.get("content"):
                on_text(message["content"])
            return message
        return self._request_stream(messages, on_text)

    def summarize(self, messages: list[dict]) -> str:
        if self.demo:
            content = messages[-1]["content"]
            return "离线演示摘要：" + content[-1200:]
        message = self._request(messages, tools=False)
        content = message.get("content")
        if not content:
            raise ValueError("Compaction model returned an empty summary")
        return content

    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        if self.demo:
            return [
                {
                    "id": "AC1",
                    "description": "The demo workspace memory file exists",
                    "check": {"type": "file_exists", "path": "MEMORY.md"},
                }
            ]
        command_schema = (
            '{"type":"command","command":"..."}, '
            if allow_bash
            else ""
        )
        command_rule = (
            "Command success means exit code 0. "
            if allow_bash
            else "Bash is disabled: never generate a command check. "
        )
        manual_schema = (
            ', or {"type":"manual","instructions":"what the user must inspect"}. '
            if allow_manual
            else ". "
        )
        manual_rule = (
            "Use manual only for inherently subjective or visual outcomes; never use it for file existence, "
            "file content, or behavior that a deterministic check can establish. "
            if allow_manual
            else "This is an unattended run: never generate a manual check. Every criterion must be verified "
            "using an available machine-checkable type. "
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Create a strict, machine-checkable acceptance contract for a coding agent goal. Cover every "
                    "explicit outcome in the goal. Return only JSON: {\"criteria\": [...]}. Each criterion has "
                    "id, description, and check. IDs are AC1, AC2, etc. A check is exactly one of: "
                    f"{command_schema}"
                    "{\"type\":\"file_exists\",\"path\":\"relative/path\"}, "
                    "{\"type\":\"file_equals\",\"path\":\"relative/path\",\"text\":\"exact complete content\"}, "
                    "{\"type\":\"file_contains\",\"path\":\"relative/path\",\"text\":\"exact text\"}, "
                    "{\"type\":\"file_not_contains\",\"path\":\"relative/path\",\"text\":\"exact text\"}"
                    f"{manual_schema}"
                    "For every explicitly requested complete file content, use file_equals with the exact content, "
                    "including any requested final newline. Use file_contains only for partial-content requirements. "
                    f"{command_rule}"
                    f"{manual_rule}"
                    "Paths must be relative to the "
                    "workspace. Do not add requirements that the user did not request. Limit the plan to 12 criteria."
                ),
            },
            {
                "role": "user",
                "content": f"Goal:\n{objective}\n\nHost command verification enabled: {allow_bash}",
            },
        ]
        error: ValueError | None = None
        for attempt in range(2):
            response = self._request(messages, tools=False)
            content = response.get("content") or ""
            try:
                return validate_criteria(
                    self._parse_json_object(content),
                    allow_bash=allow_bash,
                    allow_manual=allow_manual,
                )
            except ValueError as validation_error:
                error = validation_error
                if attempt == 0:
                    messages.extend(
                        [
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": (
                                    f"The contract is invalid: {validation_error}. Return a corrected JSON contract "
                                    "using only the allowed check types."
                                ),
                            },
                        ]
                    )
        raise error or ValueError("Goal controller returned an invalid acceptance contract")

    @staticmethod
    def _parse_json_object(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1])
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Goal controller did not return JSON") from None
            try:
                value = json.loads(text[start : end + 1])
            except json.JSONDecodeError as error:
                raise ValueError("Goal controller returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Goal controller returned invalid JSON")
        return value

    def _request(
        self,
        messages: list[dict],
        tools: bool,
        extra_payload: dict | None = None,
    ) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = self.tool_schemas
        if extra_payload:
            payload.update(extra_payload)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            # Avoid printing response bodies that might echo credentials.
            raise ValueError(f"Model API HTTP {error.code}; check endpoint, key and model") from error
        except urllib.error.URLError as error:
            raise ValueError(f"Cannot connect to model API: {error.reason}") from error
        try:
            message = data["choices"][0]["message"]
            return self._validate_message(message, tools)
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise ValueError("Invalid chat completions response") from error

    def _request_stream(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "tools": self.tool_schemas,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
                return self._read_stream(response, on_text)
        except urllib.error.HTTPError as error:
            raise ValueError(f"Model API HTTP {error.code}; check endpoint, key and model") from error
        except urllib.error.URLError as error:
            raise ValueError(f"Cannot connect to model API: {error.reason}") from error

    def _read_stream(
        self,
        lines: Iterable[bytes],
        on_text: Callable[[str], None] | None,
    ) -> dict:
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        try:
            for raw_line in lines:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                event = json.loads(data_text)
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                fragment = delta.get("content")
                if fragment:
                    if not isinstance(fragment, str):
                        raise ValueError("Model returned non-text content")
                    content_parts.append(fragment)
                    if on_text:
                        on_text(fragment)
                for part in delta.get("tool_calls") or []:
                    index = part.get("index", 0)
                    if not isinstance(index, int):
                        raise ValueError("Invalid streamed tool call index")
                    call = tool_calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if part.get("id"):
                        call["id"] += part["id"]
                    if part.get("type"):
                        call["type"] = part["type"]
                    function = part.get("function") or {}
                    if function.get("name"):
                        call["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        call["function"]["arguments"] += function["arguments"]
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError) as error:
            raise ValueError("Invalid streaming chat completions response") from error
        message = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return self._validate_message(message, tools=True)

    @staticmethod
    def _validate_message(message: object, tools: bool) -> dict:
        try:
            if not isinstance(message, dict):
                raise ValueError("Invalid chat completions response")
            content = message.get("content")
            calls = message.get("tool_calls") or []
            if not tools and calls:
                raise ValueError("Model unexpectedly requested a tool while tools were disabled")
            if content is not None and not isinstance(content, str):
                raise ValueError("Model returned non-text content")
            ids = set()
            for call in calls:
                if not isinstance(call["id"], str) or call["id"] in ids:
                    raise ValueError("Invalid or duplicate tool call ID")
                ids.add(call["id"])
                if not isinstance(call["function"]["name"], str) or not isinstance(call["function"]["arguments"], str):
                    raise ValueError("Invalid tool call")
            result = {"role": "assistant", "content": content}
            if calls:
                result["tool_calls"] = calls
            return result
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise ValueError("Invalid chat completions response") from error


class AnthropicProvider(BaseProvider):
    """Native Anthropic Messages API transport."""

    provider_name = "anthropic"

    def __init__(self, base_url: str, api_key: str, auth_token: str, model: str):
        super().__init__(model)
        root = (base_url or "https://api.anthropic.com").rstrip("/")
        self.url = root + ("/messages" if root.endswith("/v1") else "/v1/messages")
        self.api_key = api_key
        self.auth_token = auth_token

    def complete(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
    ) -> dict:
        return self._request_stream(messages, on_text)

    def summarize(self, messages: list[dict]) -> str:
        message = self._request(messages, tools=False)
        content = message.get("content")
        if not content:
            raise ValueError("Compaction model returned an empty summary")
        return content

    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        # The acceptance contract is provider-independent; this method uses this class's
        # Anthropic _request implementation with the shared contract parser and prompt.
        return OpenAICompatibleProvider.create_acceptance_criteria(
            self,
            objective,
            allow_bash,
            allow_manual,
        )

    _parse_json_object = staticmethod(OpenAICompatibleProvider._parse_json_object)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        elif self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _payload(self, messages: list[dict], tools: bool, stream: bool) -> dict:
        system, converted = self._convert_messages(messages)
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": converted,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": schema["function"]["name"],
                    "description": schema["function"]["description"],
                    "input_schema": schema["function"]["parameters"],
                }
                for schema in self.tool_schemas
            ]
        return payload

    @staticmethod
    def _convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        converted: list[dict] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content") or ""))
                continue
            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(message.get("content") or ""),
                }
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
                continue
            content: list[dict] = []
            if message.get("content"):
                content.append({"type": "text", "text": str(message["content"])})
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                    except (KeyError, TypeError, json.JSONDecodeError) as error:
                        raise ValueError("Invalid tool call in conversation history") from error
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["function"]["name"],
                            "input": arguments,
                        }
                    )
            converted.append({"role": role, "content": content or [{"type": "text", "text": ""}]})
        return "\n\n".join(system_parts), converted

    def _request(self, messages: list[dict], tools: bool) -> dict:
        request = urllib.request.Request(
            self.url,
            json.dumps(self._payload(messages, tools, False)).encode(),
            self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError(f"Anthropic API HTTP {error.code}; check endpoint, key and model") from error
        except urllib.error.URLError as error:
            raise ValueError(f"Cannot connect to Anthropic API: {error.reason}") from error
        try:
            return self._message_from_blocks(data["content"], tools)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("Invalid Anthropic Messages response") from error

    def _request_stream(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None,
    ) -> dict:
        request = urllib.request.Request(
            self.url,
            json.dumps(self._payload(messages, True, True)).encode(),
            self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
                return self._read_anthropic_stream(response, on_text)
        except urllib.error.HTTPError as error:
            raise ValueError(f"Anthropic API HTTP {error.code}; check endpoint, key and model") from error
        except urllib.error.URLError as error:
            raise ValueError(f"Cannot connect to Anthropic API: {error.reason}") from error

    def _read_anthropic_stream(
        self,
        lines: Iterable[bytes],
        on_text: Callable[[str], None] | None,
    ) -> dict:
        content_parts: list[str] = []
        calls: dict[int, dict] = {}
        try:
            for raw_line in lines:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                event_type = event.get("type")
                index = event.get("index", 0)
                if event_type == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        calls[index] = {
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {"name": block.get("name", ""), "arguments": ""},
                        }
                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        fragment = delta.get("text", "")
                        content_parts.append(fragment)
                        if on_text and fragment:
                            on_text(fragment)
                    elif delta.get("type") == "input_json_delta" and index in calls:
                        calls[index]["function"]["arguments"] += delta.get("partial_json", "")
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError) as error:
            raise ValueError("Invalid Anthropic streaming response") from error
        message = {"role": "assistant", "content": "".join(content_parts) or None}
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return OpenAICompatibleProvider._validate_message(message, tools=True)

    @staticmethod
    def _message_from_blocks(blocks: object, tools: bool) -> dict:
        if not isinstance(blocks, list):
            raise ValueError("Invalid Anthropic Messages response")
        text_parts: list[str] = []
        calls: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("Invalid Anthropic content block")
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
        message = {"role": "assistant", "content": "".join(text_parts) or None}
        if calls:
            message["tool_calls"] = calls
        return OpenAICompatibleProvider._validate_message(message, tools)


class MockProvider(BaseProvider):
    """Offline provider with reproducible responses for CLI and end-to-end tests."""

    provider_name = "mock"
    demo = True

    def __init__(self, responses: list[dict] | None = None, model: str = "mock-v1"):
        super().__init__(model)
        self.responses = [dict(response) for response in responses or []]

    def complete(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
    ) -> dict:
        if self.responses:
            message = dict(self.responses.pop(0))
        else:
            message = self._deterministic_response(messages)
        content = message.get("content")
        if on_text and isinstance(content, str):
            on_text(content)
        return OpenAICompatibleProvider._validate_message(message, tools=True)

    def summarize(self, messages: list[dict]) -> str:
        serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return "Mock summary: " + serialized[-1200:]

    def create_acceptance_criteria(
        self,
        objective: str,
        allow_bash: bool,
        allow_manual: bool = True,
    ) -> list[dict]:
        match = re.search(r"(?:create|创建)\s+([\w.-]+)", objective, re.IGNORECASE)
        path = match.group(1) if match else "result.txt"
        return [
            {
                "id": "AC1",
                "description": f"{path} exists",
                "check": {"type": "file_exists", "path": path},
            }
        ]

    @staticmethod
    def _deterministic_response(messages: list[dict]) -> dict:
        last = messages[-1]
        if last.get("role") == "tool":
            return {"role": "assistant", "content": "Mock tool result:\n" + str(last.get("content") or "")}
        prompt = str(last.get("content") or "")
        if prompt.startswith("mock:read "):
            path = prompt.removeprefix("mock:read ").strip()
            return MockProvider._tool_call("mock-read", "read", {"path": path})
        if prompt.startswith("mock:write "):
            value = prompt.removeprefix("mock:write ")
            path, separator, content = value.partition("::")
            if not separator or not path.strip():
                return {"role": "assistant", "content": "Mock error: use mock:write path::content"}
            return MockProvider._tool_call(
                "mock-write",
                "write",
                {"path": path.strip(), "content": content},
            )
        return {"role": "assistant", "content": f"Mock response: {prompt}"}

    @staticmethod
    def _tool_call(identifier: str, name: str, arguments: dict[str, str]) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": identifier,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }


def create_provider(
    name: str,
    base_url: str,
    api_key: str,
    model: str,
    demo: bool = False,
    auth_token: str = "",
) -> BaseProvider:
    normalized = name.strip().lower()
    if demo or normalized == "mock":
        return MockProvider(model=model or "mock-v1")
    if normalized == "auto" and (
        auth_token
        or (model.startswith("claude-") and (not base_url or "anthropic.com" in base_url))
    ):
        normalized = "anthropic"
    if normalized in {"", "auto", "openai", "openai-compatible"}:
        return OpenAICompatibleProvider(base_url or "https://api.openai.com/v1", api_key, model, False)
    if normalized == "anthropic":
        return AnthropicProvider(base_url, api_key, auth_token, model)
    raise ValueError(f"Unknown provider: {name}")


# Compatibility for existing imports and external callers.
Provider = OpenAICompatibleProvider
